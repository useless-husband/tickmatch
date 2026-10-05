#!/usr/bin/env python3
"""Mutation check: plant one bug at a time in the RTL and make sure the tests notice.

Each mutant is a one-line change to a copy of rtl/.  It is built and run against
  1. TWSE's worked examples and the directed boundary scenarios   (make vectors)
  2. two random/adversarial flows vs the golden   (as make equiv, 40,000 messages each)
  3. the exhaustive small-configuration checks    (make prove, --quick)
and counts as caught when any of them reports a difference or the simulator hangs.
A mutant that survives is a hole in the tests; the script exits non-zero.

    python3 tools/mutate.py [-j 4] [--only N]
"""
import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
os.chdir(ROOT)
PY = sys.executable

E, D, PE = "rtl/tm_engine.sv", "rtl/tm_daycfg.sv", "rtl/tm_prienc.sv"
# (file, text to find (must occur exactly once), replacement, what breaks)
MUTANTS = [
    (PE, "for (g = GN - 1; g >= 0; g = g - 1) if (grp[g]) glo", "for (g = 0; g < GN; g = g + 1) if (grp[g]) glo",
     "priority encoder: lowest group scan runs the wrong way"),
    (PE, "for (b = 0; b < GS; b = b + 1) if (shi[b]) bhi", "for (b = GS - 1; b >= 0; b = b - 1) if (shi[b]) bhi",
     "priority encoder: highest bit inside the group is the lowest"),
    (D, "(p == refp) || (up100 <= ref_hi)", "(p == refp) || (up100 < ref_hi)", "limit-up excludes exactly +10%"),
    (D, "(first || dn100 >= ref_lo)", "(first || dn100 > ref_lo)", "limit-down excludes exactly -10%"),
    (D, "else if (p < 5000) tick_of = 10'd5;", "else if (p <= 5000) tick_of = 10'd5;", "tick band boundary at 50.00 off by one"),
    (D, "wire can_up = (p == refp) ||", "wire can_up = 1'b0 ||", "minimum-tick clause missing on the way up"),
    (E, "(m_side ? (o_lvl <= opp_best) : (o_lvl >= opp_best))", "(m_side ? (o_lvl <= opp_best) : (o_lvl > opp_best))",
     "a buy at exactly the best ask does not trade"),
    (E, "                    out_price <= c_price;\n                    out_qty <= {{(32-QTY_W){1'b0}}, fill};",
     "                    out_price <= m_mkt ? c_price : m_price;\n                    out_qty <= {{(32-QTY_W){1'b0}}, fill};",
     "fills reported at the incoming order's price"),
    (E, "if (!m_mkt && o_lvl < conv) conv = o_lvl;", "", "R7.2: incoming limit buy not counted in the conversion"),
    (E, "if (b_any && bid_hi > conv) conv = bid_hi;", "if (b_any && bid_lo > conv) conv = bid_lo;",
     "R7.1: market buy converted with the lowest bid"),
    (E, "c_lvl <= opp_mkt_any ? conv : opp_best;", "c_lvl <= opp_best;", "resting market order priced at the best limit level"),
    (E, "((lhs > thr_hi) || (lhs < thr_lo))", "((lhs >= thr_hi) || (lhs < thr_lo))", "R8.1: exactly +3.5% triggers"),
    (E, "((lhs > thr_hi) || (lhs < thr_lo))", "(lhs > thr_hi)", "R8: lower side of the band not checked"),
    (E, "                if (!m_mkt && m_tif == TIF_ROD) begin\n                    // R8 para. 1",
     "                if (!m_mkt) begin\n                    // R8 para. 1", "R8: a limit IOC order starts an interruption"),
    (E, "wire            use_fix = (now <= fix_until);", "wire            use_fix = (now < fix_until);",
     "R8.1: fixed reference ends one second early"),
    (E, "fix_until <= vi_end + TP_FIX;", "fix_until <= vi_end + TP_HALT;", "R8: fixed reference after an interruption too short"),
    (E, "tot_q  <= tot_q - bk_q[SQ_W-1:0];", "tot_q  <= tot_q;", "R8: volume never leaves the five-minute window"),
    (E, "tot_pq <= tot_pq + {{(SPQ_W-PRICE_W-QTY_W){1'b0}}, fill_pq};", "tot_pq <= tot_pq + {{(SPQ_W-PRICE_W){1'b0}}, c_price};",
     "R8: average not weighted by volume"),
    (E, "wire            sc_ok  = (sc_bgt <= sc_v) && (cs <= sc_v);", "wire            sc_ok  = (sc_bgt <= sc_v);",
     "R6 principle 1: cheaper sell orders may be left unfilled"),
    (E, "if (anchor_lvl < a_lo) begin", "if (anchor_lvl <= a_hi) begin", "R6 principle 3: always the lowest qualifying price"),
    (E, "if (sc_v > vmax) begin", "if (sc_v >= vmax) begin", "R6: a later price with equal volume replaces the run"),
    (E, "(bid_hi >= a_lvl) && (ask_lo <= a_lvl)", "(bid_hi > a_lvl) && (ask_lo <= a_lvl)", "auction: bids at the price are left out"),
    (E, "lt_we = !q_mkt; lt_wd = op_q;", "lt_we = !q_mkt; lt_wd = on_q;", "cancel of the last order in a queue corrupts the tail"),
    (E, "op_we = 1'b1; op_wa = on_q; op_wd = op_q;", "op_we = 1'b0; op_wa = on_q; op_wd = op_q;",
     "cancel in the middle of a queue leaves a stale back pointer"),
    (E, "lqb_wd = lqb_rd - {{(LQ_W-QTY_W){1'b0}}, m_qty};", "lqb_wd = lqb_rd;", "REDUCE does not lower the level's quantity (buy side)"),
    (E, "wire             fk_limit = !m_mkt && ", "wire             fk_limit = 1'b0 && ", "FOK feasibility ignores the order's limit price"),
    (E, "if (fk_avail >= {{(LQ_W+1-QTY_W){1'b0}}, o_rem}) st <= S_M0;", "if (fk_avail > {{(LQ_W+1-QTY_W){1'b0}}, o_rem}) st <= S_M0;",
     "FOK for exactly the available quantity is killed"),
    (E, "else if (phase != PH_CONT && (m_mkt || m_tif != TIF_ROD)) out_code <= RJ_TYPE;",
     "else if (phase != PH_CONT && m_mkt) out_code <= RJ_TYPE;", "R4: IOC/FOK accepted in call phases"),
    (E, "else if (!pg_side) pg_side <= 1'b1;", "else if (1'b0) pg_side <= 1'b1;", "R4: resting market sells survive into a call phase"),
    (E, "(md_sim && wb_hi == a_lvl) ? md_bq0 : lqb_rd", "lqb_rd", "R9: simulated best bid shows the unmatched quantity"),
    (E, "if (wa_any && md_rank != 3'd5) begin", "if (wa_any && md_rank != 3'd4) begin", "R9: only four ask levels disclosed"),
    (E, "else if (!m_mkt && !p_on_tick) out_code <= RJ_TICK;", "", "R2: off-tick prices accepted"),
    (E, "else if (!m_mkt && !p_in_range) out_code <= RJ_LIMIT;", "else if (!m_mkt && m_price > cfg_lim_up) out_code <= RJ_LIMIT;",
     "R3: prices below limit-down accepted"),
    (E, "else if (q_qty != {QTY_W{1'b0}}) out_code <= RJ_DUP;", "", "an id that is still live is accepted again"),
    (E, "last_lvl <= c_lvl; last_price <= c_price; has_last <= 1'b1;\n                    cur_q", "last_price <= c_price; has_last <= 1'b1;\n                    cur_q",
     "last trade level not updated by continuous fills"),
    (E, "end else if (phase == PH_VI && vi_end >= TP_CCALL && m_time >= TP_CCALL) begin",
     "end else if (phase == PH_VI && vi_end > TP_CCALL && m_time >= TP_CCALL) begin",
     "R8.1: an interruption ending exactly at 13:25 never resolves"),
    (E, "if (m_time < now) out_code <= RJ_TIME;", "if (1'b0) out_code <= RJ_TIME;", "time may run backwards"),
    (E, "                end else if (unl_only) begin\n                    if (q_side) bmp_a[q_lvl] <= 1'b0;",
     "                end else if (unl_only) begin\n                    if (1'b0) bmp_a[q_lvl] <= 1'b0;",
     "cancelling the only sell order at a level leaves the level marked"),
    (E, "fix_price <= a_valid ? a_price : ref_price;", "fix_price <= ref_price;", "R8: first five minutes use the reference, not the opening price"),
    (E, "if (ins_empty) mk_head_a <= m_id;", "", "first resting market sell never becomes the queue head"),
    (E, "oi_wd = c_gone ? {OI_W{1'b0}} : {oi_q[OI_W-1:QTY_W], q_qty - fill};", "oi_wd = c_gone ? {OI_W{1'b0}} : {oi_q[OI_W-1:QTY_W], q_qty};",
     "a partly filled resting order keeps its full quantity"),
    (E, "steps  <= steps - 1'b1;\n                st <= (steps == {{(T_W-1){1'b0}}, 1'b1}) ? S_IDLE : S_TA1;",
     "steps  <= steps - 1'b1;\n                st <= S_IDLE;", "R8: the window advances one second however much time passed"),
]

VDEFS = "-DVM_SC=0 -DVM_TIMING=0 -DVM_TRACE=0 -DVM_TRACE_FST=0 -DVM_TRACE_VCD=0 -DVM_TRACE_SAIF=0 -DVM_COVERAGE=0".split()


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def build(d):
    """Verilate and compile the RTL in d/rtl; returns the simulator path or None."""
    r = sh(["verilator", "--cc", "-O3", "--x-assign", "fast", "--x-initial", "fast", "--noassert", "-I" + d + "/rtl",
            "-Wno-fatal", "-Wno-lint", "-Wno-style", "--top-module", "tm_engine", "-Mdir", d + "/vm",
            d + "/rtl/tm_engine.sv", d + "/rtl/tm_daycfg.sv", d + "/rtl/tm_prienc.sv"])
    if r.returncode:
        return None
    vroot = sh(["verilator", "--getenv", "VERILATOR_ROOT"]).stdout.strip()
    srcs = [os.path.join(d, "vm", f) for f in os.listdir(d + "/vm") if f.endswith(".cpp")]
    r = sh([os.environ.get("CXX", "c++"), "-std=c++17", "-O1", "-w"] + VDEFS +
           ["-I" + d + "/vm", "-I" + vroot + "/include", "-I" + vroot + "/include/vltstd"] + srcs +
           [vroot + "/include/verilated.cpp", vroot + "/include/verilated_threads.cpp", "sim/sim_tm.cpp",
            "-o", d + "/sim_tm", "-lpthread"])
    return d + "/sim_tm" if r.returncode == 0 else None


def detect(sim, d):
    """Name of the first test that catches this simulator, or None."""
    for name in ("vec", "dir", "eq1", "eq2"):
        with open("build/mut/%s.stim" % name) as f:
            try:
                r = subprocess.run([sim] + (["--stall", "25"] if name == "eq2" else []), stdin=f,
                                   stdout=open(d + "/out", "w"), stderr=subprocess.PIPE, timeout=600)
            except subprocess.TimeoutExpired:
                return name + " (hang)"
        if r.returncode:
            return name + " (simulator stopped: %s)" % r.stderr.decode().strip()[:40]
        if sh([PY, "tools/compare.py", "build/mut/%s.exp" % name, d + "/out"]).returncode:
            return {"vec": "official examples", "dir": "directed boundary scenarios", "eq1": "random flow",
                    "eq2": "random flow with stalls"}[name]
    r = sh([PY, "tools/prove.py", "--quick"], env=dict(os.environ, TM_SIM=sim))
    if r.returncode:
        return "exhaustive checks"
    return None


def one(k):
    path, old, new, what = MUTANTS[k]
    d = "build/mut/m%02d" % k
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree("rtl", d + "/rtl")
    src = open(d + "/" + path).read()
    if src.count(old) != 1:
        return k, "INVALID (pattern occurs %d times)" % src.count(old)
    open(d + "/" + path, "w").write(src.replace(old, new))
    sim = build(d)
    if sim is None:
        return k, "INVALID (does not build)"
    res = detect(sim, d)
    shutil.rmtree(d, ignore_errors=True)
    return k, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-j", type=int, default=4)
    ap.add_argument("--only", type=int, default=None)
    a = ap.parse_args()
    os.makedirs("build/mut", exist_ok=True)
    for name, tool in (("vec", "tools/vectors_stim.py"), ("dir", "tools/directed_stim.py")):
        with open("build/mut/%s.stim" % name, "w") as f:
            subprocess.run([PY, tool], stdout=f, check=True)
        with open("build/mut/%s.stim" % name) as fi, open("build/mut/%s.exp" % name, "w") as fo:
            subprocess.run([PY, "tools/golden.py"], stdin=fi, stdout=fo, check=True)
    for name, seed in (("eq1", 101), ("eq2", 102)):
        subprocess.run([PY, "tools/gen.py", "--seed", str(seed), "--messages", "40000", "--stim",
                        "build/mut/%s.stim" % name, "--exp", "build/mut/%s.exp" % name],
                       check=True, stdout=subprocess.DEVNULL)
    # the unmodified RTL must pass, or "caught" means nothing
    shutil.rmtree("build/mut/base", ignore_errors=True)
    shutil.copytree("rtl", "build/mut/base/rtl")
    base = build("build/mut/base")
    if base is None or detect(base, "build/mut/base") is not None:
        sys.exit("the unmodified RTL does not pass the detection suite")
    todo = [a.only] if a.only is not None else list(range(len(MUTANTS)))
    caught = bad = 0
    with concurrent.futures.ThreadPoolExecutor(a.j) as ex:
        for k, res in ex.map(one, todo):
            what = MUTANTS[k][3]
            if res is None:
                print("  SURVIVED  m%02d  %s" % (k, what))
            elif res.startswith("INVALID"):
                print("  %s  m%02d  %s" % (res, k, what))
                bad += 1
            else:
                caught += 1
                print("  caught    m%02d  %-62s <- %s" % (k, what, res))
            sys.stdout.flush()
    print("mutation score: %d of %d planted bugs caught" % (caught, len(todo)))
    sys.exit(0 if caught == len(todo) and not bad else 1)


if __name__ == "__main__":
    main()
