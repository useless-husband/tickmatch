// tickmatch: a matching engine for one TWSE-listed stock for one trading day.
//
// The section numbers (R1..R9) refer to docs/RULES.md, where each rule is
// quoted from its TWSE source.  docs/DESIGN.md explains the structure.
//
//   book        one FIFO of orders per price level and side, as doubly linked
//               lists threaded through three RAMs indexed by order id;
//               the daily limits make the set of prices finite (R2, R3), so a
//               level is addressed directly and a bitmap + priority encoder
//               gives the best price (no sorting, no tree)
//   market      one FIFO per side for resting market orders, served first (R7.1)
//   continuous  price-time matching with ROD / IOC / FOK and market orders (R4, R7)
//   auction     one pass over the levels finds the call-auction price (R6)
//   volatility  rolling five-minute average in 300 one-second buckets (R8)
//   clock       TIME messages move the session through its phases (R1)
//
// One message is processed at a time: in_ready is high only when the engine
// is idle and every output message of the previous input has been taken.
module tm_engine #(
    parameter ID_W    = 12,             // order ids 0 .. 2**ID_W-1; the id is the RAM address
    parameter LVL_W   = 8,              // up to 2**LVL_W price levels inside the daily limits
    parameter QTY_W   = 12,             // order quantity in board lots, 1 .. 2**QTY_W-1
    parameter PRICE_W = 24,             // prices in cents
    parameter T_OPEN  = 32400,          // 09:00:00  (R1), seconds of the day
    parameter T_CCALL = 48300,          // 13:25:00
    parameter T_CLOSE = 48600,          // 13:30:00
    parameter T_ACCEPT = 30600,         // 08:30:00, the engine's time after reset
    parameter LIMIT_PCT = 10,           // R3
    parameter VI_BAND_PERMILLE = 35,    // R8
    parameter VI_HALT_S = 120,
    parameter VI_FIX_S  = 300,
    parameter VI_WIN_S  = 300,          // at most 512
    parameter VI_MIN_REF = 100          // R8: no interruption if the reference is below TWD 1.00
) (
    input  wire               clk,
    input  wire               rst,            // synchronous; starts a new trading day
    input  wire [PRICE_W-1:0] cfg_ref_price,  // auction reference price (R3.1), sampled in reset
    input  wire               cfg_vi_en,      // volatility interruption applies to this symbol (R8)
    output wire               cfg_done,
    output wire               cfg_error,      // reference price needs more than 2**LVL_W levels
    output wire [PRICE_W-1:0] cfg_lim_dn,
    output wire [PRICE_W-1:0] cfg_lim_up,
    output wire [LVL_W:0]     cfg_n_levels,

    input  wire               in_valid,
    output wire               in_ready,
    input  wire [2:0]         in_op,          // 1 NEW  2 CANCEL  3 REDUCE  4 TIME  5 SNAP
    input  wire [ID_W-1:0]    in_id,
    input  wire               in_side,        // 0 buy, 1 sell
    input  wire               in_mkt,         // market order
    input  wire [1:0]         in_tif,         // 0 ROD  1 IOC  2 FOK
    input  wire [PRICE_W-1:0] in_price,       // cents; for TIME: seconds of the day
    input  wire [QTY_W-1:0]   in_qty,         // lots; for REDUCE: lots to take off

    output reg                out_valid,
    input  wire               out_ready,
    output reg  [3:0]         out_type,       // see the O_ constants
    output reg  [ID_W-1:0]    out_id,
    output reg  [ID_W-1:0]    out_id2,
    output reg  [PRICE_W-1:0] out_price,
    output reg  [31:0]        out_qty,
    output reg  [3:0]         out_code,

    output reg                err_vwap_ovf    // sticky: five-minute volume exceeded 2**32 lots
);
    localparam NL   = 1 << LVL_W;
    localparam NO   = 1 << ID_W;
    localparam LQ_W = ID_W + QTY_W;           // a level can hold every order at full size
    localparam T_W  = 18;
    localparam SQ_W = 32;
    localparam SPQ_W = PRICE_W + SQ_W;
    localparam CW   = SPQ_W + 12;             // width of the band comparison
    localparam IC_W = (ID_W > 9) ? ID_W : 9;

    // input ops
    localparam [2:0] OP_NEW = 3'd1, OP_CANCEL = 3'd2, OP_REDUCE = 3'd3, OP_TIME = 3'd4, OP_SNAP = 3'd5;
    // output message types
    localparam [3:0] O_ACK = 4'd1, O_REJ = 4'd2, O_TRADE = 4'd3, O_CXL = 4'd4, O_RED = 4'd5,
                     O_VI = 4'd6, O_AUC = 4'd7, O_PHASE = 4'd8, O_MDL = 4'd9, O_MDE = 4'd10;
    // phases (R1)
    localparam [2:0] PH_PRE = 3'd0, PH_CONT = 3'd1, PH_VI = 3'd2, PH_CCALL = 3'd3, PH_CLOSED = 3'd4;
    // reject codes
    localparam [3:0] RJ_CLOSED = 4'd1, RJ_TYPE = 4'd2, RJ_LIMIT = 4'd3, RJ_TICK = 4'd4, RJ_QTY = 4'd5,
                     RJ_DUP = 4'd6, RJ_UNKNOWN = 4'd7, RJ_TIME = 4'd8, RJ_OP = 4'd9, RJ_CFG = 4'd10;
    // cancel reasons
    localparam [3:0] CX_USER = 4'd0, CX_REDUCE = 4'd1, CX_IOC = 4'd2, CX_FOK = 4'd3, CX_BAND = 4'd4,
                     CX_PURGE = 4'd5;
    localparam [1:0] TIF_ROD = 2'd0, TIF_FOK = 2'd2;
    localparam [1:0] AK_OPEN = 2'd0, AK_VI = 2'd1, AK_CLOSE = 2'd2;

    localparam [T_W-1:0] TP_OPEN = T_OPEN, TP_CCALL = T_CCALL, TP_CLOSE = T_CLOSE, TP_ACCEPT = T_ACCEPT;
    localparam [T_W-1:0] TP_HALT = VI_HALT_S, TP_FIX = VI_FIX_S, TP_WIN = VI_WIN_S;
    localparam [8:0]     WIN_LAST = VI_WIN_S - 1;
    localparam [10:0]    BAND_HI = 1000 + VI_BAND_PERMILLE;
    localparam [10:0]    BAND_LO = 1000 - VI_BAND_PERMILLE;

    // states
    localparam [5:0]
        S_INIT = 6'd0,  S_IDLE = 6'd1,  S_DEC = 6'd2,   S_M0 = 6'd3,    S_M1 = 6'd4,   S_M2 = 6'd5,
        S_VIOL = 6'd6,  S_POST = 6'd7,  S_INS = 6'd8,   S_PG0 = 6'd9,   S_PG1 = 6'd10, S_PG2 = 6'd11,
        S_UNL = 6'd12,  S_RED = 6'd13,  S_FOK0 = 6'd14, S_FOK1 = 6'd15, S_FOK2 = 6'd16, S_FOK3 = 6'd17,
        S_FOKF = 6'd18, S_A0 = 6'd19,   S_A1 = 6'd20,   S_A2 = 6'd21,   S_A3 = 6'd22,  S_A4 = 6'd23,
        S_U0 = 6'd24,   S_U1 = 6'd25,   S_U2 = 6'd26,   S_U3 = 6'd27,   S_A9 = 6'd28,  S_T0 = 6'd29,
        S_TA1 = 6'd30,  S_TA2 = 6'd31,  S_MD0 = 6'd32,  S_MD1 = 6'd33,  S_MD2 = 6'd34, S_MD3 = 6'd35,
        S_MD4 = 6'd36,  S_MD5 = 6'd37,  S_MD6 = 6'd38;

    reg [5:0] st;

    // ------------------------------------------------------------------ day configuration (R2, R3)
    reg                start_cfg;
    reg  [PRICE_W-1:0] ref_price;
    reg                vi_en;
    wire [LVL_W-1:0]   ref_lvl;
    wire [LVL_W:0]     brk_lvl;
    wire [PRICE_W-1:0] brk_price;
    wire [9:0]         tick_lo, tick_hi;

    tm_daycfg #(.PRICE_W(PRICE_W), .LVL_W(LVL_W), .LIMIT_PCT(LIMIT_PCT)) u_cfg (
        .clk(clk), .start(start_cfg), .ref_price(ref_price), .done(cfg_done), .error(cfg_error),
        .lim_dn(cfg_lim_dn), .lim_up(cfg_lim_up), .n_levels(cfg_n_levels), .ref_lvl(ref_lvl),
        .brk_lvl(brk_lvl), .brk_price(brk_price), .tick_lo(tick_lo), .tick_hi(tick_hi));

    wire [LVL_W-1:0] last_level_idx = cfg_n_levels[LVL_W-1:0] - 1'b1;

    // level index -> price in cents
    function [PRICE_W-1:0] l2p(input [LVL_W-1:0] lvl);
        reg [LVL_W:0] off;
        begin
            if ({1'b0, lvl} < brk_lvl) begin
                l2p = cfg_lim_dn + {{(PRICE_W-LVL_W){1'b0}}, lvl} * {{(PRICE_W-10){1'b0}}, tick_lo};
            end else begin
                off = {1'b0, lvl} - brk_lvl;
                l2p = brk_price + {{(PRICE_W-LVL_W-1){1'b0}}, off} * {{(PRICE_W-10){1'b0}}, tick_hi};
            end
        end
    endfunction

    // ------------------------------------------------------------------ message being processed
    reg [2:0]         m_op;
    reg [ID_W-1:0]    m_id;
    reg               m_side, m_mkt;
    reg [1:0]         m_tif;
    reg [PRICE_W-1:0] m_price;
    reg [QTY_W-1:0]   m_qty;
    reg [LVL_W-1:0]   o_lvl;      // price level of the incoming limit order
    reg [QTY_W-1:0]   o_rem;      // quantity of the incoming order not yet filled
    wire [T_W-1:0]    m_time = m_price[T_W-1:0];

    // price in cents -> level index, with range and tick checks (R2, R3): a restoring
    // divider, combinational, LVL_W steps; the divisor is one of the day's two tick sizes
    wire               p_in_range = (m_price >= cfg_lim_dn) && (m_price <= cfg_lim_up);
    wire               p_hi   = (m_price >= brk_price);
    wire [PRICE_W-1:0] p_base = p_hi ? brk_price : cfg_lim_dn;
    wire [9:0]         p_tick = p_hi ? tick_hi : tick_lo;
    reg  [PRICE_W-1:0] p_rem;
    reg  [LVL_W-1:0]   p_quo;
    integer            k;
    always @* begin
        p_rem = m_price - p_base;
        p_quo = {LVL_W{1'b0}};
        for (k = LVL_W - 1; k >= 0; k = k - 1) begin
            if (p_rem >= ({{(PRICE_W-10){1'b0}}, p_tick} << k)) begin
                p_rem    = p_rem - ({{(PRICE_W-10){1'b0}}, p_tick} << k);
                p_quo[k] = 1'b1;
            end
        end
    end
    wire               p_on_tick = (p_rem == {PRICE_W{1'b0}});
    wire [LVL_W-1:0]   p_lvl = (p_hi ? brk_lvl[LVL_W-1:0] : {LVL_W{1'b0}}) + p_quo;

    // ------------------------------------------------------------------ order RAMs (block RAM, sync read)
    // o_info[id] = {market, side, level, qty}; qty = 0 means the id is free
    localparam OI_W = 2 + LVL_W + QTY_W;
    reg [OI_W-1:0] o_info [0:NO-1];
    reg [ID_W-1:0] o_next [0:NO-1];
    reg [ID_W-1:0] o_prev [0:NO-1];
    reg            oi_we, on_we, op_we, oi_re, on_re, op_re;
    reg [ID_W-1:0] oi_wa, on_wa, op_wa, oi_ra, on_ra, op_ra;
    reg [OI_W-1:0] oi_wd, oi_q;
    reg [ID_W-1:0] on_wd, op_wd, on_q, op_q;
    always @(posedge clk) begin
        if (oi_we) o_info[oi_wa] <= oi_wd;
        if (oi_re) oi_q <= o_info[oi_ra];
    end
    always @(posedge clk) begin
        if (on_we) o_next[on_wa] <= on_wd;
        if (on_re) on_q <= o_next[on_ra];
    end
    always @(posedge clk) begin
        if (op_we) o_prev[op_wa] <= op_wd;
        if (op_re) op_q <= o_prev[op_ra];
    end
    wire             q_mkt  = oi_q[OI_W-1];
    wire             q_side = oi_q[OI_W-2];
    wire [LVL_W-1:0] q_lvl  = oi_q[QTY_W +: LVL_W];
    wire [QTY_W-1:0] q_qty  = oi_q[QTY_W-1:0];

    // ------------------------------------------------------------------ level tables (LUT RAM, async read)
    reg [ID_W-1:0] l_head [0:2*NL-1];      // address {side, level}
    reg [ID_W-1:0] l_tail [0:2*NL-1];
    reg [LQ_W-1:0] lq_b [0:NL-1];          // total resting quantity per level, buy side
    reg [LQ_W-1:0] lq_a [0:NL-1];          // ... sell side
    reg            lh_we, lt_we, lqb_we, lqa_we;
    reg [LVL_W:0]  lh_wa, lt_wa;
    reg [ID_W-1:0] lh_wd, lt_wd;
    reg [LVL_W-1:0] lqb_ad, lqa_ad;
    reg [LQ_W-1:0] lqb_wd, lqa_wd;
    wire [LQ_W-1:0] lqb_rd = lq_b[lqb_ad];
    wire [LQ_W-1:0] lqa_rd = lq_a[lqa_ad];
    always @(posedge clk) begin
        if (lh_we) l_head[lh_wa] <= lh_wd;
    end
    always @(posedge clk) begin
        if (lt_we) l_tail[lt_wa] <= lt_wd;
    end
    always @(posedge clk) begin
        if (lqb_we) lq_b[lqb_ad] <= lqb_wd;
    end
    always @(posedge clk) begin
        if (lqa_we) lq_a[lqa_ad] <= lqa_wd;
    end

    // which levels hold orders, and the encoders that turn that into prices
    reg  [NL-1:0] bmp_b, bmp_a;            // book
    reg  [NL-1:0] wk_b, wk_a;              // working copies for walks (FOK check, market data)
    wire          b_any, a_any, wb_any, wa_any;
    wire [LVL_W-1:0] bid_lo, bid_hi, ask_lo, ask_hi, wb_hi, wa_lo;
    /* verilator lint_off PINCONNECTEMPTY */
    tm_prienc #(.W(LVL_W)) u_enc_b  (.v(bmp_b), .any(b_any),  .lo(bid_lo), .hi(bid_hi));
    tm_prienc #(.W(LVL_W)) u_enc_a  (.v(bmp_a), .any(a_any),  .lo(ask_lo), .hi(ask_hi));
    tm_prienc #(.W(LVL_W)) u_enc_wb (.v(wk_b),  .any(wb_any), .lo(),       .hi(wb_hi));
    tm_prienc #(.W(LVL_W)) u_enc_wa (.v(wk_a),  .any(wa_any), .lo(wa_lo),  .hi());
    /* verilator lint_on PINCONNECTEMPTY */

    // resting market orders, one FIFO per side (R7.1)
    reg [ID_W-1:0] mk_head_b, mk_head_a, mk_tail_b, mk_tail_a;
    reg [LQ_W-1:0] mk_qty_b, mk_qty_a;

    // ------------------------------------------------------------------ session state
    reg [2:0]         phase;
    reg [T_W-1:0]     now, vi_end, fix_until;
    reg [PRICE_W-1:0] fix_price, last_price;
    reg [LVL_W-1:0]   last_lvl;
    reg               has_last;
    wire [LVL_W-1:0]   anchor_lvl   = has_last ? last_lvl : ref_lvl;       // R6 para. 3, R7.1
    wire [PRICE_W-1:0] anchor_price = has_last ? last_price : ref_price;
    wire               call_phase   = (phase == PH_PRE) || (phase == PH_VI) || (phase == PH_CCALL);

    // ------------------------------------------------------------------ five-minute window (R8)
    reg [SQ_W-1:0]  tot_q, cur_q;          // volume in the window / in the current second
    reg [SPQ_W-1:0] tot_pq, cur_pq;        // price x volume
    reg [8:0]       bk_ptr;
    reg [SPQ_W+SQ_W-1:0] bk [0:511];
    reg             bk_we, bk_re;
    reg [8:0]       bk_wa, bk_ra;
    reg [SPQ_W+SQ_W-1:0] bk_wd, bk_q;
    always @(posedge clk) begin
        if (bk_we) bk[bk_wa] <= bk_wd;
        if (bk_re) bk_q <= bk[bk_ra];
    end
    wire [8:0] bk_ptr_nx = (bk_ptr == WIN_LAST) ? 9'd0 : bk_ptr + 9'd1;
    reg [T_W-1:0] steps;

    // reference of the order being processed, as a fraction vr_pq / vr_q, and the band test
    reg [SQ_W-1:0]  vr_q;
    reg [CW-1:0]    thr_hi, thr_lo;
    reg [SPQ_W-1:0] prod;
    localparam [PRICE_W-1:0] MIN_REF = VI_MIN_REF;
    wire            vi_on   = vi_en && (ref_price >= MIN_REF) && (phase == PH_CONT);
    wire [CW-1:0]   lhs     = {{(CW-SPQ_W){1'b0}}, prod} * {{(CW-10){1'b0}}, 10'd1000};
    wire            beyond  = vi_on && ((lhs > thr_hi) || (lhs < thr_lo));     // R8.1: strictly beyond
    wire            use_fix = (now <= fix_until);
    wire [SPQ_W-1:0] ref_pq = use_fix ? {{SQ_W{1'b0}}, fix_price}
                            : (tot_q != {SQ_W{1'b0}}) ? tot_pq : {{SQ_W{1'b0}}, anchor_price};
    wire [SQ_W-1:0]  ref_q  = (!use_fix && tot_q != {SQ_W{1'b0}}) ? tot_q : {{(SQ_W-1){1'b0}}, 1'b1};

    // ------------------------------------------------------------------ matching datapath
    reg               c_mkt;               // candidate is a resting market order
    reg [ID_W-1:0]    c_id;
    reg [LVL_W-1:0]   c_lvl;
    reg [PRICE_W-1:0] c_price;
    reg               do_vi, pg_side, pg_ccall, fk_mkt, dry;
    reg [LQ_W:0]      fk_avail;
    reg [1:0]         a_kind;

    wire opp = ~m_side;                    // side of the resting orders the incoming order can hit
    wire opp_mkt_any = opp ? (mk_qty_a != {LQ_W{1'b0}}) : (mk_qty_b != {LQ_W{1'b0}});
    wire opp_any     = opp ? a_any : b_any;
    wire [LVL_W-1:0] opp_best = opp ? ask_lo : bid_hi;
    wire crosses = m_mkt || (m_side ? (o_lvl <= opp_best) : (o_lvl >= opp_best));

    // R7.1 converted price of a resting market order, as a level; the incoming limit
    // order counts as part of the book (R7.2)
    reg [LVL_W-1:0] conv;
    always @* begin
        conv = anchor_lvl;
        if (opp) begin                     // resting market SELL: the lowest of the three
            if (b_any && bid_lo < conv) conv = bid_lo;
            if (a_any && ask_lo < conv) conv = ask_lo;
            if (!m_mkt && o_lvl < conv) conv = o_lvl;
        end else begin                     // resting market BUY: the highest
            if (b_any && bid_hi > conv) conv = bid_hi;
            if (a_any && ask_hi > conv) conv = ask_hi;
            if (!m_mkt && o_lvl > conv) conv = o_lvl;
        end
    end

    wire [QTY_W-1:0]  fill     = (o_rem < q_qty) ? o_rem : q_qty;
    wire              c_gone   = (fill == q_qty);
    wire [LQ_W-1:0]   fill_x   = {{(LQ_W-QTY_W){1'b0}}, fill};
    wire [LQ_W-1:0]   opp_lq   = opp ? lqa_rd : lqb_rd;
    wire [PRICE_W+QTY_W-1:0] fill_pq = c_price * {{PRICE_W{1'b0}}, fill};
    wire              out_free = !out_valid || out_ready;
    wire [SQ_W:0]     tot_q_nx = {1'b0, tot_q} + {{(SQ_W+1-QTY_W){1'b0}}, fill};

    // FOK walk: next level on the opposite side
    wire             fk_any   = opp ? wa_any : wb_any;
    wire [LVL_W-1:0] fk_lvl   = opp ? wa_lo : wb_hi;
    wire             fk_limit = !m_mkt && (m_side ? (fk_lvl < o_lvl) : (fk_lvl > o_lvl));

    // ------------------------------------------------------------------ auction datapath (R6)
    reg [LVL_W-1:0] sc_lvl;
    reg [LQ_W-1:0]  totb, below, cs, vmax;
    reg             a_has;
    reg [LVL_W-1:0] a_lo, a_hi, a_lvl;
    reg [LQ_W-1:0]  lo_b, lo_s, hi_b, hi_s, an_b, an_s, p_b, p_s;
    reg [PRICE_W-1:0] a_price;
    wire [LQ_W-1:0] sc_b   = totb - below;                 // buys at or above this level
    wire [LQ_W-1:0] sc_s   = cs + lqa_rd;                  // sells at or below this level
    wire [LQ_W-1:0] sc_v   = (sc_b < sc_s) ? sc_b : sc_s;  // volume if this were the price
    wire [LQ_W-1:0] sc_bgt = sc_b - lqb_rd;                // buys strictly above
    wire            sc_ok  = (sc_bgt <= sc_v) && (cs <= sc_v);     // principle 1
    wire            a_valid = (vmax != {LQ_W{1'b0}});
    wire            sc_last = (sc_lvl == last_level_idx);
    // uncross
    reg [ID_W-1:0]  u_b, u_a, ub_next;
    reg [LVL_W-1:0] u_blvl, u_alvl;
    reg [QTY_W-1:0] ub_qty, u_fill;
    reg             ua_gone;
    wire            u_more = b_any && a_any && (bid_hi >= a_lvl) && (ask_lo <= a_lvl);
    wire [QTY_W-1:0] ufill = (ub_qty < q_qty) ? ub_qty : q_qty;

    // market data walk (R9)
    reg [2:0]       md_rank;
    reg             md_sim;
    reg [LQ_W-1:0]  md_bq0, md_aq0;
    reg [NL-1:0]    mask_le, mask_ge;
    integer         i;
    always @* begin
        for (i = 0; i < NL; i = i + 1) begin
            mask_le[i] = (i[LVL_W-1:0] <= a_lvl);
            mask_ge[i] = (i[LVL_W-1:0] >= a_lvl);
        end
    end

    // ------------------------------------------------------------------ reset / initial clear
    reg [IC_W:0] init_cnt;
    wire init_done = init_cnt[IC_W];

    assign in_ready = (st == S_IDLE) && !out_valid;

    // ------------------------------------------------------------------ RAM port control
    wire [LVL_W-1:0] own_lvl = o_lvl;
    wire [ID_W-1:0]  hd_opp = l_head[{opp, opp_best}];      // head of the best opposite level
    wire [ID_W-1:0]  hd_bid = l_head[{1'b0, bid_hi}];
    wire [ID_W-1:0]  hd_ask = l_head[{1'b1, ask_lo}];
    wire [ID_W-1:0]  hd_q   = l_head[{q_side, q_lvl}];      // queue ends of the order named by the message
    wire [ID_W-1:0]  tl_q   = l_tail[{q_side, q_lvl}];
    wire [ID_W-1:0]  tl_own = l_tail[{m_side, own_lvl}];    // tail of the incoming order's level
    wire unl_only = q_mkt ? ((q_side ? mk_qty_a : mk_qty_b) == {{(LQ_W-QTY_W){1'b0}}, q_qty})
                          : ((q_side ? lqa_rd : lqb_rd) == {{(LQ_W-QTY_W){1'b0}}, q_qty});
    wire unl_head = q_mkt ? ((q_side ? mk_head_a : mk_head_b) == m_id) : (hd_q == m_id);
    wire unl_tail = q_mkt ? ((q_side ? mk_tail_a : mk_tail_b) == m_id) : (tl_q == m_id);
    wire ins_empty = m_mkt ? ((m_side ? mk_qty_a : mk_qty_b) == {LQ_W{1'b0}})
                           : !(m_side ? bmp_a[own_lvl] : bmp_b[own_lvl]);
    wire [ID_W-1:0] ins_tail = m_mkt ? (m_side ? mk_tail_a : mk_tail_b) : tl_own;
    wire [ID_W-1:0] pg_head  = pg_side ? mk_head_a : mk_head_b;
    wire            pg_any   = pg_side ? (mk_qty_a != {LQ_W{1'b0}}) : (mk_qty_b != {LQ_W{1'b0}});

    always @* begin
        case (st)
            S_INIT:              begin lqb_ad = init_cnt[LVL_W-1:0]; lqa_ad = init_cnt[LVL_W-1:0]; end
            S_M2, S_FOK2:        begin lqb_ad = c_lvl;  lqa_ad = c_lvl;  end
            S_DEC, S_UNL, S_RED: begin lqb_ad = q_lvl;  lqa_ad = q_lvl;  end
            S_A1, S_A2:          begin lqb_ad = sc_lvl; lqa_ad = sc_lvl; end
            S_U2, S_U3:          begin lqb_ad = u_blvl; lqa_ad = u_alvl; end
            S_MD2, S_MD3:        begin lqb_ad = wb_hi;  lqa_ad = wa_lo;  end
            default:             begin lqb_ad = own_lvl; lqa_ad = own_lvl; end
        endcase
    end

    always @* begin
        oi_we = 1'b0; oi_wa = m_id; oi_wd = {OI_W{1'b0}}; oi_re = 1'b0; oi_ra = m_id;
        on_we = 1'b0; on_wa = m_id; on_wd = m_id;         on_re = 1'b0; on_ra = m_id;
        op_we = 1'b0; op_wa = m_id; op_wd = m_id;         op_re = 1'b0; op_ra = m_id;
        lh_we = 1'b0; lh_wa = {m_side, own_lvl}; lh_wd = m_id;
        lt_we = 1'b0; lt_wa = {m_side, own_lvl}; lt_wd = m_id;
        lqb_we = 1'b0; lqb_wd = {LQ_W{1'b0}};
        lqa_we = 1'b0; lqa_wd = {LQ_W{1'b0}};
        bk_we = 1'b0; bk_wa = bk_ptr; bk_wd = {cur_pq, cur_q}; bk_re = 1'b0; bk_ra = bk_ptr_nx;
        case (st)
            S_INIT: begin
                oi_we = 1'b1; oi_wa = init_cnt[ID_W-1:0];
                lqb_we = 1'b1;
                lqa_we = 1'b1;
                bk_we = 1'b1; bk_wa = init_cnt[8:0]; bk_wd = {(SPQ_W+SQ_W){1'b0}};
            end
            S_IDLE: begin
                oi_re = 1'b1; oi_ra = in_id;
                on_re = 1'b1; on_ra = in_id;
                op_re = 1'b1; op_ra = in_id;
            end
            S_M0, S_FOK0: begin            // fetch the order at the head of the best opposite queue
                oi_ra = opp_mkt_any ? (opp ? mk_head_a : mk_head_b) : hd_opp;
                on_ra = oi_ra;
                oi_re = (st == S_M0);
                on_re = (st == S_M0);
            end
            S_M2: begin                    // one fill against the resting order c_id
                lh_wa = {opp, c_lvl}; lh_wd = on_q;
                oi_wa = c_id;
                oi_wd = c_gone ? {OI_W{1'b0}} : {oi_q[OI_W-1:QTY_W], q_qty - fill};
                if (!beyond && out_free) begin
                    oi_we = 1'b1;
                    if (!c_mkt) begin
                        lqb_we = !opp; lqa_we = opp;
                        lqb_wd = lqb_rd - fill_x; lqa_wd = lqa_rd - fill_x;
                        lh_we = c_gone;
                    end
                end
            end
            S_INS: begin                   // append the incoming order to its queue
                oi_we = 1'b1; oi_wa = m_id; oi_wd = {m_mkt, m_side, own_lvl, o_rem};
                op_we = 1'b1; op_wa = m_id; op_wd = ins_tail;
                on_we = !ins_empty; on_wa = ins_tail; on_wd = m_id;
                if (!m_mkt) begin
                    lh_we = ins_empty; lt_we = 1'b1;
                    lqb_we = !m_side; lqa_we = m_side;
                    lqb_wd = lqb_rd + {{(LQ_W-QTY_W){1'b0}}, o_rem};
                    lqa_wd = lqa_rd + {{(LQ_W-QTY_W){1'b0}}, o_rem};
                end
            end
            S_PG0: begin
                oi_re = 1'b1; oi_ra = pg_head;
                on_re = 1'b1; on_ra = pg_head;
            end
            S_PG1: begin
                oi_wa = pg_head; oi_we = out_free;
            end
            S_DEC, S_UNL, S_RED: begin     // the order named by the message: m_id, read in S_IDLE
                lh_wa = {q_side, q_lvl}; lt_wa = {q_side, q_lvl};
                if (st == S_UNL && out_free) begin
                    oi_we = 1'b1; oi_wa = m_id;                    // free the id
                    if (!q_mkt) begin
                        lqb_we = !q_side; lqa_we = q_side;
                        lqb_wd = lqb_rd - {{(LQ_W-QTY_W){1'b0}}, q_qty};
                        lqa_wd = lqa_rd - {{(LQ_W-QTY_W){1'b0}}, q_qty};
                    end
                    if (!unl_only) begin
                        if (unl_head) begin
                            lh_we = !q_mkt; lh_wd = on_q;
                        end else if (unl_tail) begin
                            lt_we = !q_mkt; lt_wd = op_q;
                        end else begin
                            on_we = 1'b1; on_wa = op_q; on_wd = on_q;
                            op_we = 1'b1; op_wa = on_q; op_wd = op_q;
                        end
                    end
                end
                if (st == S_RED && out_free) begin
                    oi_we = 1'b1; oi_wa = m_id; oi_wd = {oi_q[OI_W-1:QTY_W], q_qty - m_qty};
                    if (!q_mkt) begin
                        lqb_we = !q_side; lqa_we = q_side;
                        lqb_wd = lqb_rd - {{(LQ_W-QTY_W){1'b0}}, m_qty};
                        lqa_wd = lqa_rd - {{(LQ_W-QTY_W){1'b0}}, m_qty};
                    end
                end
            end
            S_U0: begin                    // read the best buy order
                oi_re = 1'b1; oi_ra = hd_bid;
                on_re = 1'b1; on_ra = hd_bid;
            end
            S_U1: begin                    // read the best sell order
                oi_re = 1'b1; oi_ra = hd_ask;
                on_re = 1'b1; on_ra = hd_ask;
            end
            S_U2: begin                    // trade; update the buy order
                lh_wa = {1'b0, u_blvl}; lh_wd = ub_next;
                oi_wa = u_b;
                oi_wd = (ufill == ub_qty) ? {OI_W{1'b0}} : {2'b00, u_blvl, ub_qty - ufill};
                if (out_free) begin
                    oi_we = 1'b1;
                    lqb_we = 1'b1; lqb_wd = lqb_rd - {{(LQ_W-QTY_W){1'b0}}, ufill};
                    lh_we = (ufill == ub_qty);
                end
            end
            S_U3: begin                    // update the sell order
                lh_wa = {1'b1, u_alvl}; lh_wd = on_q;
                oi_wa = u_a; oi_we = 1'b1;
                oi_wd = ua_gone ? {OI_W{1'b0}} : {2'b01, u_alvl, q_qty - u_fill};
                lqa_we = 1'b1; lqa_wd = lqa_rd - {{(LQ_W-QTY_W){1'b0}}, u_fill};
                lh_we = ua_gone;
            end
            S_TA1: begin
                bk_we = 1'b1; bk_re = 1'b1;
            end
            default: ;
        endcase
    end

    // ------------------------------------------------------------------ the state machine
    always @(posedge clk) begin
        start_cfg <= 1'b0;
        if (out_valid && out_ready) out_valid <= 1'b0;

        if (rst) begin
            st <= S_INIT;
            init_cnt <= {(IC_W+1){1'b0}};
            start_cfg <= 1'b1;
            ref_price <= cfg_ref_price;
            vi_en <= cfg_vi_en;
            out_valid <= 1'b0;
            phase <= PH_PRE;
            now <= TP_ACCEPT;
            vi_end <= {T_W{1'b0}};
            fix_until <= {T_W{1'b0}};
            has_last <= 1'b0;
            bmp_b <= {NL{1'b0}};
            bmp_a <= {NL{1'b0}};
            mk_qty_b <= {LQ_W{1'b0}};
            mk_qty_a <= {LQ_W{1'b0}};
            tot_q <= {SQ_W{1'b0}};
            tot_pq <= {SPQ_W{1'b0}};
            cur_q <= {SQ_W{1'b0}};
            cur_pq <= {SPQ_W{1'b0}};
            bk_ptr <= 9'd0;
            err_vwap_ovf <= 1'b0;
            do_vi <= 1'b0;
        end else case (st)
            S_INIT: begin
                if (!init_done) init_cnt <= init_cnt + 1'b1;
                else if (cfg_done && !start_cfg) st <= S_IDLE;
            end

            S_IDLE: if (in_valid && !out_valid) begin
                m_op <= in_op; m_id <= in_id; m_side <= in_side; m_mkt <= in_mkt;
                m_tif <= in_tif; m_price <= in_price; m_qty <= in_qty;
                st <= S_DEC;
            end

            // ---------------------------------------------------------- validate and dispatch
            S_DEC: begin
                out_id <= m_id; out_id2 <= {ID_W{1'b0}}; out_price <= {PRICE_W{1'b0}};
                out_qty <= 32'd0; out_type <= O_REJ; out_code <= RJ_OP;
                out_valid <= 1'b1;
                st <= S_IDLE;
                o_rem <= m_qty;
                o_lvl <= p_lvl;
                do_vi <= 1'b0;
                // R8: the reference is fixed when the order enters
                vr_q   <= ref_q;
                thr_hi <= {{(CW-SPQ_W){1'b0}}, ref_pq} * {{(CW-11){1'b0}}, BAND_HI};
                thr_lo <= {{(CW-SPQ_W){1'b0}}, ref_pq} * {{(CW-11){1'b0}}, BAND_LO};
                if (cfg_error) begin
                    out_code <= RJ_CFG;
                    if (m_op != OP_NEW && m_op != OP_CANCEL && m_op != OP_REDUCE) out_id <= {ID_W{1'b0}};
                end else case (m_op)
                    OP_NEW: begin
                        if (phase == PH_CLOSED) out_code <= RJ_CLOSED;
                        else if (m_tif == 2'd3) out_code <= RJ_OP;
                        else if (m_qty == {QTY_W{1'b0}}) out_code <= RJ_QTY;
                        else if (q_qty != {QTY_W{1'b0}}) out_code <= RJ_DUP;
                        else if (phase != PH_CONT && (m_mkt || m_tif != TIF_ROD)) out_code <= RJ_TYPE;   // R4
                        else if (!m_mkt && !p_in_range) out_code <= RJ_LIMIT;                            // R3
                        else if (!m_mkt && !p_on_tick) out_code <= RJ_TICK;                              // R2
                        else begin
                            out_type <= O_ACK; out_code <= 4'd0;
                            out_price <= m_mkt ? {PRICE_W{1'b0}} : m_price;
                            out_qty <= {{(32-QTY_W){1'b0}}, m_qty};
                            if (phase != PH_CONT) st <= S_INS;          // call phases only accumulate (R1)
                            else if (m_tif == TIF_FOK) st <= S_FOK0;
                            else st <= S_M0;
                        end
                    end
                    OP_CANCEL, OP_REDUCE: begin
                        if (phase == PH_CLOSED) out_code <= RJ_CLOSED;
                        else if (q_qty == {QTY_W{1'b0}}) out_code <= RJ_UNKNOWN;
                        else if (m_op == OP_REDUCE && m_qty == {QTY_W{1'b0}}) out_code <= RJ_QTY;
                        else begin
                            out_valid <= 1'b0;
                            st <= (m_op == OP_REDUCE && m_qty < q_qty) ? S_RED : S_UNL;
                        end
                    end
                    OP_TIME: begin
                        out_id <= {ID_W{1'b0}};
                        if (m_time < now) out_code <= RJ_TIME;
                        else begin
                            out_valid <= 1'b0;
                            st <= S_T0;
                        end
                    end
                    OP_SNAP: begin
                        out_valid <= 1'b0;
                        st <= S_MD0;
                    end
                    default: out_id <= {ID_W{1'b0}};
                endcase
            end

            // ---------------------------------------------------------- continuous matching (R7)
            S_M0: begin
                c_mkt <= opp_mkt_any;
                c_id  <= oi_ra;
                c_lvl <= opp_mkt_any ? conv : opp_best;
                c_price <= l2p(opp_mkt_any ? conv : opp_best);
                if (o_rem == {QTY_W{1'b0}}) st <= S_IDLE;
                else if (opp_mkt_any || (opp_any && crosses)) st <= S_M1;
                else st <= S_POST;
            end
            S_M1: begin
                prod <= c_price * {{(SPQ_W-SQ_W){1'b0}}, vr_q};
                st <= S_M2;
            end
            S_M2: begin
                if (beyond) st <= S_VIOL;
                else if (out_free) begin
                    out_valid <= 1'b1; out_type <= O_TRADE; out_code <= 4'd0;
                    out_id  <= m_side ? c_id : m_id;
                    out_id2 <= m_side ? m_id : c_id;
                    out_price <= c_price;
                    out_qty <= {{(32-QTY_W){1'b0}}, fill};
                    o_rem <= o_rem - fill;
                    if (c_mkt) begin
                        if (opp) begin
                            mk_qty_a <= mk_qty_a - fill_x;
                            if (c_gone) mk_head_a <= on_q;
                        end else begin
                            mk_qty_b <= mk_qty_b - fill_x;
                            if (c_gone) mk_head_b <= on_q;
                        end
                    end else if (opp_lq == fill_x) begin
                        if (opp) bmp_a[c_lvl] <= 1'b0;
                        else     bmp_b[c_lvl] <= 1'b0;
                    end
                    last_lvl <= c_lvl; last_price <= c_price; has_last <= 1'b1;
                    cur_q  <= cur_q + {{(SQ_W-QTY_W){1'b0}}, fill};
                    cur_pq <= cur_pq + {{(SPQ_W-PRICE_W-QTY_W){1'b0}}, fill_pq};
                    tot_pq <= tot_pq + {{(SPQ_W-PRICE_W-QTY_W){1'b0}}, fill_pq};
                    tot_q  <= tot_q_nx[SQ_W-1:0];
                    if (tot_q_nx[SQ_W]) err_vwap_ovf <= 1'b1;
                    st <= S_M0;
                end
            end
            S_VIOL: if (out_free) begin
                out_valid <= 1'b1; out_id <= m_id; out_id2 <= {ID_W{1'b0}};
                if (!m_mkt && m_tif == TIF_ROD) begin
                    // R8 para. 1: the rest stays in the book and matching is postponed
                    out_type <= O_VI; out_price <= c_price; out_qty <= 32'd0; out_code <= 4'd0;
                    do_vi <= 1'b1;
                    st <= S_INS;
                end else begin
                    // R8 para. 2: the part beyond the band is cancelled
                    out_type <= O_CXL; out_price <= {PRICE_W{1'b0}}; out_code <= CX_BAND;
                    out_qty <= {{(32-QTY_W){1'b0}}, o_rem};
                    st <= S_IDLE;
                end
            end
            S_POST: begin
                if (m_tif == TIF_ROD) st <= S_INS;
                else if (out_free) begin
                    out_valid <= 1'b1; out_type <= O_CXL; out_id <= m_id; out_id2 <= {ID_W{1'b0}};
                    out_price <= {PRICE_W{1'b0}}; out_qty <= {{(32-QTY_W){1'b0}}, o_rem}; out_code <= CX_IOC;
                    st <= S_IDLE;
                end
            end
            S_INS: begin
                if (m_mkt) begin
                    if (m_side) begin
                        mk_tail_a <= m_id; mk_qty_a <= mk_qty_a + {{(LQ_W-QTY_W){1'b0}}, o_rem};
                        if (ins_empty) mk_head_a <= m_id;
                    end else begin
                        mk_tail_b <= m_id; mk_qty_b <= mk_qty_b + {{(LQ_W-QTY_W){1'b0}}, o_rem};
                        if (ins_empty) mk_head_b <= m_id;
                    end
                end else begin
                    if (m_side) bmp_a[own_lvl] <= 1'b1;
                    else        bmp_b[own_lvl] <= 1'b1;
                end
                pg_side <= 1'b0; pg_ccall <= 1'b0;
                st <= do_vi ? S_PG0 : S_IDLE;
            end

            // ---------------------------------------------------------- withdraw resting market orders (R4)
            S_PG0: begin
                if (pg_any) st <= S_PG1;
                else if (!pg_side) pg_side <= 1'b1;
                else st <= S_PG2;
            end
            S_PG1: if (out_free) begin
                out_valid <= 1'b1; out_type <= O_CXL; out_id <= pg_head; out_id2 <= {ID_W{1'b0}};
                out_price <= {PRICE_W{1'b0}}; out_qty <= {{(32-QTY_W){1'b0}}, q_qty}; out_code <= CX_PURGE;
                if (pg_side) begin
                    mk_qty_a <= mk_qty_a - {{(LQ_W-QTY_W){1'b0}}, q_qty}; mk_head_a <= on_q;
                end else begin
                    mk_qty_b <= mk_qty_b - {{(LQ_W-QTY_W){1'b0}}, q_qty}; mk_head_b <= on_q;
                end
                st <= S_PG0;
            end
            S_PG2: if (out_free) begin
                out_valid <= 1'b1; out_type <= O_PHASE; out_id <= {ID_W{1'b0}}; out_id2 <= {ID_W{1'b0}};
                out_price <= {PRICE_W{1'b0}};
                do_vi <= 1'b0;
                if (pg_ccall) begin
                    phase <= PH_CCALL; out_code <= {1'b0, PH_CCALL};
                    out_qty <= {{(32-T_W){1'b0}}, TP_CCALL};
                    st <= S_T0;
                end else begin
                    phase <= PH_VI; out_code <= {1'b0, PH_VI};
                    out_qty <= {{(32-T_W){1'b0}}, now};
                    vi_end <= now + TP_HALT;
                    st <= S_IDLE;
                end
            end

            // ---------------------------------------------------------- cancel / reduce (R4.1)
            S_UNL: if (out_free) begin
                out_valid <= 1'b1; out_type <= O_CXL; out_id <= m_id; out_id2 <= {ID_W{1'b0}};
                out_price <= {PRICE_W{1'b0}}; out_qty <= {{(32-QTY_W){1'b0}}, q_qty};
                out_code <= (m_op == OP_CANCEL) ? CX_USER : CX_REDUCE;
                if (q_mkt) begin
                    if (q_side) begin
                        mk_qty_a <= mk_qty_a - {{(LQ_W-QTY_W){1'b0}}, q_qty};
                        if (unl_head) mk_head_a <= on_q; else if (unl_tail) mk_tail_a <= op_q;
                    end else begin
                        mk_qty_b <= mk_qty_b - {{(LQ_W-QTY_W){1'b0}}, q_qty};
                        if (unl_head) mk_head_b <= on_q; else if (unl_tail) mk_tail_b <= op_q;
                    end
                end else if (unl_only) begin
                    if (q_side) bmp_a[q_lvl] <= 1'b0;
                    else        bmp_b[q_lvl] <= 1'b0;
                end
                st <= S_IDLE;
            end
            S_RED: if (out_free) begin
                out_valid <= 1'b1; out_type <= O_RED; out_id <= m_id; out_id2 <= {ID_W{1'b0}};
                out_price <= {PRICE_W{1'b0}}; out_qty <= {{(32-QTY_W){1'b0}}, q_qty - m_qty}; out_code <= 4'd0;
                if (q_mkt) begin
                    if (q_side) mk_qty_a <= mk_qty_a - {{(LQ_W-QTY_W){1'b0}}, m_qty};
                    else        mk_qty_b <= mk_qty_b - {{(LQ_W-QTY_W){1'b0}}, m_qty};
                end
                st <= S_IDLE;
            end

            // ---------------------------------------------------------- FOK: can all of it trade now? (R4, R8)
            S_FOK0: begin
                fk_avail <= {(LQ_W+1){1'b0}};
                wk_b <= bmp_b; wk_a <= bmp_a;
                fk_mkt <= opp_mkt_any;
                c_lvl <= conv; c_price <= l2p(conv);
                st <= opp_mkt_any ? S_FOK1 : S_FOK3;
            end
            S_FOK1: begin
                prod <= c_price * {{(SPQ_W-SQ_W){1'b0}}, vr_q};
                st <= S_FOK2;
            end
            S_FOK2: begin
                if (beyond) st <= S_FOKF;
                else begin
                    if (fk_mkt) fk_avail <= fk_avail + {1'b0, opp ? mk_qty_a : mk_qty_b};
                    else begin
                        fk_avail <= fk_avail + {1'b0, opp_lq};
                        if (opp) wk_a[c_lvl] <= 1'b0;
                        else     wk_b[c_lvl] <= 1'b0;
                    end
                    fk_mkt <= 1'b0;
                    st <= S_FOK3;
                end
            end
            S_FOK3: begin
                c_lvl <= fk_lvl; c_price <= l2p(fk_lvl);
                if (fk_avail >= {{(LQ_W+1-QTY_W){1'b0}}, o_rem}) st <= S_M0;
                else if (!fk_any || fk_limit) st <= S_FOKF;
                else st <= S_FOK1;
            end
            S_FOKF: if (out_free) begin
                out_valid <= 1'b1; out_type <= O_CXL; out_id <= m_id; out_id2 <= {ID_W{1'b0}};
                out_price <= {PRICE_W{1'b0}}; out_qty <= {{(32-QTY_W){1'b0}}, o_rem}; out_code <= CX_FOK;
                st <= S_IDLE;
            end

            // ---------------------------------------------------------- call auction price (R6)
            S_A0: begin
                sc_lvl <= {LVL_W{1'b0}};
                totb <= {LQ_W{1'b0}};
                st <= S_A1;
            end
            S_A1: begin                    // pass 1: total buy quantity
                totb <= totb + lqb_rd;
                below <= {LQ_W{1'b0}}; cs <= {LQ_W{1'b0}}; vmax <= {LQ_W{1'b0}}; a_has <= 1'b0;
                if (sc_last) begin
                    sc_lvl <= {LVL_W{1'b0}};
                    st <= S_A2;
                end else sc_lvl <= sc_lvl + 1'b1;
            end
            S_A2: begin                    // pass 2: every tick from the lower limit up
                if (sc_v > vmax) begin
                    vmax <= sc_v; a_has <= sc_ok;
                    a_lo <= sc_lvl; lo_b <= sc_b; lo_s <= sc_s;
                    a_hi <= sc_lvl; hi_b <= sc_b; hi_s <= sc_s;
                end else if (sc_v == vmax && sc_ok && a_valid) begin
                    if (!a_has) begin
                        a_lo <= sc_lvl; lo_b <= sc_b; lo_s <= sc_s;
                    end
                    a_has <= 1'b1;
                    a_hi <= sc_lvl; hi_b <= sc_b; hi_s <= sc_s;
                end
                if (sc_lvl == anchor_lvl) begin
                    an_b <= sc_b; an_s <= sc_s;
                end
                below <= below + lqb_rd;
                cs <= sc_s;
                if (sc_last) st <= S_A3;
                else sc_lvl <= sc_lvl + 1'b1;
            end
            S_A3: begin                    // principle 3: the qualifying tick nearest the anchor
                if (anchor_lvl < a_lo) begin
                    a_lvl <= a_lo; a_price <= l2p(a_lo); p_b <= lo_b; p_s <= lo_s;
                end else if (anchor_lvl > a_hi) begin
                    a_lvl <= a_hi; a_price <= l2p(a_hi); p_b <= hi_b; p_s <= hi_s;
                end else begin
                    a_lvl <= anchor_lvl; a_price <= l2p(anchor_lvl); p_b <= an_b; p_s <= an_s;
                end
                st <= dry ? S_MD1 : S_A4;
            end
            S_A4: if (out_free) begin
                out_valid <= 1'b1; out_type <= O_AUC; out_id <= {ID_W{1'b0}}; out_id2 <= {ID_W{1'b0}};
                out_price <= a_valid ? a_price : {PRICE_W{1'b0}};
                out_qty <= {{(32-LQ_W){1'b0}}, vmax}; out_code <= {2'b00, a_kind};
                st <= a_valid ? S_U0 : S_A9;
            end
            // uncross: best buy against best sell, all at the auction price (R5, R6)
            S_U0: begin
                u_b <= hd_bid; u_blvl <= bid_hi; u_alvl <= ask_lo;
                st <= u_more ? S_U1 : S_A9;
            end
            S_U1: begin
                ub_qty <= q_qty; ub_next <= on_q;
                u_a <= hd_ask;
                st <= S_U2;
            end
            S_U2: if (out_free) begin
                out_valid <= 1'b1; out_type <= O_TRADE; out_id <= u_b; out_id2 <= u_a;
                out_price <= a_price; out_qty <= {{(32-QTY_W){1'b0}}, ufill}; out_code <= 4'd1;
                u_fill <= ufill;
                ua_gone <= (ufill == q_qty);
                if (lqb_rd == {{(LQ_W-QTY_W){1'b0}}, ufill}) bmp_b[u_blvl] <= 1'b0;
                st <= S_U3;
            end
            S_U3: begin
                if (lqa_rd == {{(LQ_W-QTY_W){1'b0}}, u_fill}) bmp_a[u_alvl] <= 1'b0;
                st <= S_U0;
            end
            S_A9: if (out_free) begin      // the auction is over: next phase
                out_valid <= 1'b1; out_type <= O_PHASE; out_id <= {ID_W{1'b0}}; out_id2 <= {ID_W{1'b0}};
                out_price <= {PRICE_W{1'b0}};
                if (a_valid) begin
                    last_lvl <= a_lvl; last_price <= a_price; has_last <= 1'b1;
                end
                case (a_kind)
                    AK_OPEN: begin         // R8 para. 7(1)
                        fix_price <= a_valid ? a_price : ref_price;
                        fix_until <= TP_OPEN + TP_FIX;
                        phase <= PH_CONT; out_code <= {1'b0, PH_CONT};
                        out_qty <= {{(32-T_W){1'b0}}, TP_OPEN};
                    end
                    AK_VI: begin           // R8 para. 7(3)
                        if (a_valid) begin
                            fix_price <= a_price;
                            fix_until <= vi_end + TP_FIX;
                        end
                        phase <= PH_CONT; out_code <= {1'b0, PH_CONT};
                        out_qty <= {{(32-T_W){1'b0}}, vi_end};
                    end
                    default: begin
                        phase <= PH_CLOSED; out_code <= {1'b0, PH_CLOSED};
                        out_qty <= {{(32-T_W){1'b0}}, TP_CLOSE};
                    end
                endcase
                st <= S_T0;
            end

            // ---------------------------------------------------------- session clock (R1)
            S_T0: begin
                dry <= 1'b0;
                pg_side <= 1'b0; pg_ccall <= 1'b1;
                if (phase == PH_PRE && m_time >= TP_OPEN) begin
                    a_kind <= AK_OPEN; st <= S_A0;
                end else if (phase == PH_CONT && m_time >= TP_CCALL) begin
                    st <= S_PG0;
                end else if (phase == PH_VI && vi_end >= TP_CCALL && m_time >= TP_CCALL) begin
                    if (out_free) begin    // R8.1: the interruption merges into the closing call
                        out_valid <= 1'b1; out_type <= O_PHASE; out_id <= {ID_W{1'b0}};
                        out_id2 <= {ID_W{1'b0}}; out_price <= {PRICE_W{1'b0}};
                        out_qty <= {{(32-T_W){1'b0}}, TP_CCALL}; out_code <= {1'b0, PH_CCALL};
                        phase <= PH_CCALL;
                    end
                end else if (phase == PH_VI && vi_end < TP_CCALL && m_time >= vi_end) begin
                    a_kind <= AK_VI; st <= S_A0;
                end else if (phase == PH_CCALL && m_time >= TP_CLOSE) begin
                    a_kind <= AK_CLOSE; st <= S_A0;
                end else begin
                    steps <= (m_time - now > TP_WIN) ? TP_WIN : (m_time - now);
                    now <= m_time;
                    st <= (m_time == now) ? S_IDLE : S_TA1;
                end
            end
            S_TA1: begin                   // one second passes: store this second's bucket ...
                bk_ptr <= bk_ptr_nx;
                st <= S_TA2;
            end
            S_TA2: begin                   // ... and drop the one that is now five minutes old
                tot_q  <= tot_q - bk_q[SQ_W-1:0];
                tot_pq <= tot_pq - bk_q[SPQ_W+SQ_W-1:SQ_W];
                cur_q  <= {SQ_W{1'b0}};
                cur_pq <= {SPQ_W{1'b0}};
                steps  <= steps - 1'b1;
                st <= (steps == {{(T_W-1){1'b0}}, 1'b1}) ? S_IDLE : S_TA1;
            end

            // ---------------------------------------------------------- best five (R9)
            S_MD0: begin
                dry <= 1'b1;
                md_sim <= 1'b0;
                wk_b <= bmp_b; wk_a <= bmp_a;
                md_rank <= 3'd0;
                vmax <= {LQ_W{1'b0}};
                st <= call_phase ? S_A0 : S_MD2;
            end
            S_MD1: begin                   // call phase: the book as it would be after the auction
                md_sim <= a_valid;
                if (a_valid) begin
                    wk_b <= bmp_b & mask_le; wk_a <= bmp_a & mask_ge;
                    wk_b[a_lvl] <= (p_b != vmax);
                    wk_a[a_lvl] <= (p_s != vmax);
                    md_bq0 <= p_b - vmax; md_aq0 <= p_s - vmax;
                end
                st <= S_MD2;
            end
            S_MD2: begin
                if (wb_any && md_rank != 3'd5) begin
                    if (out_free) begin
                        out_valid <= 1'b1; out_type <= O_MDL; out_id <= {ID_W{1'b0}}; out_id2 <= {ID_W{1'b0}};
                        out_price <= l2p(wb_hi); out_code <= {1'b0, md_rank};
                        out_qty <= {{(32-LQ_W){1'b0}}, (md_sim && wb_hi == a_lvl) ? md_bq0 : lqb_rd};
                        wk_b[wb_hi] <= 1'b0;
                        md_rank <= md_rank + 3'd1;
                    end
                end else begin
                    md_rank <= 3'd0;
                    st <= S_MD3;
                end
            end
            S_MD3: begin
                if (wa_any && md_rank != 3'd5) begin
                    if (out_free) begin
                        out_valid <= 1'b1; out_type <= O_MDL; out_id <= {ID_W{1'b0}}; out_id2 <= {ID_W{1'b0}};
                        out_price <= l2p(wa_lo); out_code <= {1'b1, md_rank};
                        out_qty <= {{(32-LQ_W){1'b0}}, (md_sim && wa_lo == a_lvl) ? md_aq0 : lqa_rd};
                        wk_a[wa_lo] <= 1'b0;
                        md_rank <= md_rank + 3'd1;
                    end
                end else st <= S_MD4;
            end
            S_MD4: begin
                if (mk_qty_b == {LQ_W{1'b0}}) st <= S_MD5;
                else if (out_free) begin
                    out_valid <= 1'b1; out_type <= O_MDL; out_id <= {ID_W{1'b0}}; out_id2 <= {ID_W{1'b0}};
                    out_price <= {PRICE_W{1'b0}}; out_qty <= {{(32-LQ_W){1'b0}}, mk_qty_b}; out_code <= 4'd7;
                    st <= S_MD5;
                end
            end
            S_MD5: begin
                if (mk_qty_a == {LQ_W{1'b0}}) st <= S_MD6;
                else if (out_free) begin
                    out_valid <= 1'b1; out_type <= O_MDL; out_id <= {ID_W{1'b0}}; out_id2 <= {ID_W{1'b0}};
                    out_price <= {PRICE_W{1'b0}}; out_qty <= {{(32-LQ_W){1'b0}}, mk_qty_a}; out_code <= 4'd15;
                    st <= S_MD6;
                end
            end
            S_MD6: if (out_free) begin
                out_valid <= 1'b1; out_type <= O_MDE; out_id <= {ID_W{1'b0}}; out_id2 <= {ID_W{1'b0}};
                out_code <= {1'b0, phase};
                if (call_phase) begin
                    out_price <= a_valid ? a_price : {PRICE_W{1'b0}};
                    out_qty <= {{(32-LQ_W){1'b0}}, vmax};
                end else begin
                    out_price <= has_last ? last_price : {PRICE_W{1'b0}};
                    out_qty <= 32'd0;
                end
                st <= S_IDLE;
            end

            default: st <= S_IDLE;
        endcase
    end
endmodule
