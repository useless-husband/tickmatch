// The price grid of one trading day, derived from the reference price.
//
// TWSE rules: the daily limits are 10% either side of the reference price,
// rounded inwards to a valid tick, and the tick size depends on the price
// band (docs/RULES.md R2, R3).  This block walks down from the reference
// price one tick at a time until the next tick would be more than 10% away,
// then walks up from there to the upper limit, counting levels.  No divider
// is needed, and it runs once per day (a few hundred cycles).
//
// Result: every valid price of the day is  level 0 .. n_levels-1, with
//   price(level) = lim_dn    + level * tick_lo              for level <  brk_lvl
//                = brk_price + (level - brk_lvl) * tick_hi  for level >= brk_lvl
// A +-10% range can contain at most one tick-band boundary, so one break is
// enough.  If there is none, brk_lvl is 2**LVL_W (never reached).
module tm_daycfg #(
    parameter PRICE_W   = 24,
    parameter LVL_W     = 8,
    parameter LIMIT_PCT = 10
) (
    input  wire               clk,
    input  wire               start,       // one-cycle pulse, ref_price valid
    input  wire [PRICE_W-1:0] ref_price,   // cents, must be a valid tick
    output reg                done,
    output reg                error,       // more than 2**LVL_W levels, or ref_price = 0
    output reg  [PRICE_W-1:0] lim_dn,
    output reg  [PRICE_W-1:0] lim_up,
    output reg  [LVL_W:0]     n_levels,
    output reg  [LVL_W-1:0]   ref_lvl,
    output reg  [LVL_W:0]     brk_lvl,
    output reg  [PRICE_W-1:0] brk_price,
    output reg  [9:0]         tick_lo,
    output reg  [9:0]         tick_hi
);
    // R2: tick size, in cents, of the band that contains price p
    function [9:0] tick_of(input [PRICE_W-1:0] p);
        begin
            if (p < 1000) tick_of = 10'd1;
            else if (p < 5000) tick_of = 10'd5;
            else if (p < 10000) tick_of = 10'd10;
            else if (p < 50000) tick_of = 10'd50;
            else if (p < 100000) tick_of = 10'd100;
            else tick_of = 10'd500;
        end
    endfunction

    localparam S_IDLE = 2'd0, S_DOWN = 2'd1, S_UP = 2'd2;
    localparam MW = PRICE_W + 8;
    localparam [7:0] PCT_UP = 8'd100 + LIMIT_PCT[7:0];
    localparam [7:0] PCT_DN = 8'd100 - LIMIT_PCT[7:0];

    reg [1:0]         st;
    reg [PRICE_W-1:0] refp, p;
    reg [LVL_W:0]     idx;
    reg               first, brk_set;

    wire [9:0]         t_below = tick_of(p - 1'b1);
    wire [9:0]         t_here  = tick_of(p);
    wire [PRICE_W-1:0] p_dn    = p - {{(PRICE_W-10){1'b0}}, t_below};
    wire [PRICE_W-1:0] p_up    = p + {{(PRICE_W-10){1'b0}}, t_here};
    wire [MW-1:0]      ref_lo  = {8'd0, refp} * {{(MW-8){1'b0}}, PCT_DN};
    wire [MW-1:0]      ref_hi  = {8'd0, refp} * {{(MW-8){1'b0}}, PCT_UP};
    wire [MW-1:0]      dn100   = {8'd0, p_dn} * {{(MW-7){1'b0}}, 7'd100};
    wire [MW-1:0]      up100   = {8'd0, p_up} * {{(MW-7){1'b0}}, 7'd100};
    // R3: one step is always allowed (minimum-tick clause), never below one cent
    wire can_dn = (p > {{(PRICE_W-10){1'b0}}, t_below}) && (first || dn100 >= ref_lo);
    wire can_up = (p == refp) || (up100 <= ref_hi);
    wire full   = idx[LVL_W];

    always @(posedge clk) begin
        if (start) begin
            st      <= S_DOWN;
            done    <= 1'b0;
            error   <= (ref_price == {PRICE_W{1'b0}});
            refp    <= ref_price;
            p       <= ref_price;
            idx     <= {(LVL_W+1){1'b0}};
            first   <= 1'b1;
            brk_set <= 1'b0;
            brk_lvl <= {1'b1, {LVL_W{1'b0}}};
            brk_price <= {PRICE_W{1'b1}};
        end else case (st)
            S_DOWN: begin
                first <= 1'b0;
                if (error || full) begin
                    error <= 1'b1;
                    done  <= 1'b1;
                    st    <= S_IDLE;
                end else if (can_dn) begin
                    p   <= p_dn;
                    idx <= idx + 1'b1;
                end else begin
                    lim_dn  <= p;
                    tick_lo <= t_here;
                    tick_hi <= t_here;
                    idx     <= {(LVL_W+1){1'b0}};
                    st      <= S_UP;
                end
            end
            S_UP: begin
                if (full) begin
                    error <= 1'b1;
                    done  <= 1'b1;
                    st    <= S_IDLE;
                end else begin
                    if (t_here != tick_lo && !brk_set) begin
                        brk_set   <= 1'b1;
                        brk_lvl   <= idx;
                        brk_price <= p;
                        tick_hi   <= t_here;
                    end
                    if (p == refp) ref_lvl <= idx[LVL_W-1:0];
                    if (can_up) begin
                        p   <= p_up;
                        idx <= idx + 1'b1;
                    end else begin
                        lim_up   <= p;
                        n_levels <= idx + 1'b1;
                        done     <= 1'b1;
                        st       <= S_IDLE;
                    end
                end
            end
            default: ;
        endcase
    end
endmodule
