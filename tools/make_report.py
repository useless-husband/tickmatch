#!/usr/bin/env python3
"""Build build/report.html: one simulated trading day, replayable, plus latency and synthesis.

Inputs (all produced by `make day`, optionally `make bench` and `make synth`):
  build/day/day.stim   the day's input messages (seeded generator, no real order data)
  build/day/day.out    what the RTL answered -- `make day` has already checked it is identical
                       to the golden model's answer, message for message
  build/day/day.stats  latency histograms from the same RTL run
  build/bench.json, synth/summary.json   if present

The page is one static HTML file with the data inlined; it needs no network and no
WebAssembly, and works from file://.
"""
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from model import engine as E  # noqa: E402
from tools.golden import parse  # noqa: E402
from tools.bench import read_stats, summary  # noqa: E402

PHASES = ["Pre-open call (開盤前集合競價)", "Continuous (逐筆交易)", "Volatility interruption (瞬間價格穩定措施)",
          "Closing call (收盤集合競價)", "Closed (收盤)"]
CXL = ["cancelled by user", "reduced to zero", "IOC remainder cancelled", "FOK cancelled",
       "cancelled: beyond the 3.5% band", "market order withdrawn (call phase)"]
REJ = ["", "market closed", "order type not allowed in this phase", "outside the daily limits", "off tick",
       "bad quantity", "id still live", "unknown id", "time went backwards", "bad message", "day not configured"]
AUC = ["Opening auction", "Interruption auction", "Closing auction"]


def px(c):
    return "%.2f" % (c / 100.0)


def clock(t):
    return "%02d:%02d:%02d" % (t // 3600, t // 60 % 60, t % 60)


def describe_in(m):
    if m[0] == "N":
        _, oid, side, mkt, tif, price, qty = m
        return "NEW #%d %s %d lot%s %s %s" % (oid, "buy" if side == 0 else "sell", qty, "" if qty == 1 else "s",
                                             "at market" if mkt else "@ " + px(price), ["ROD", "IOC", "FOK", "?"][tif])
    if m[0] == "C":
        return "CANCEL #%d" % m[1]
    if m[0] == "D":
        return "REDUCE #%d by %d" % (m[1], m[2])
    if m[0] == "T":
        return "TIME %s" % clock(m[1])
    return "SNAPSHOT request"


def describe_out(o):
    typ, i1, i2, price, qty, code = o
    if typ == E.ACK:
        return "accepted #%d" % i1
    if typ == E.REJ:
        return "REJECTED: " + REJ[code]
    if typ == E.TRADE:
        return "TRADE %s x %d  (buy #%d, sell #%d)" % (px(price), qty, i1, i2)
    if typ == E.CXL:
        return "#%d: %d lot%s %s" % (i1, qty, "" if qty == 1 else "s", CXL[code])
    if typ == E.RED:
        return "#%d reduced, %d left" % (i1, qty)
    if typ == E.VI:
        return "INTERRUPTION: next fill would be at %s, more than 3.5%% from the reference" % px(price)
    if typ == E.AUC:
        return "%s: %s" % (AUC[code], ("%s x %d lots" % (px(price), qty)) if qty else "no match")
    if typ == E.PHASE:
        return "phase -> " + PHASES[code].split(" (")[0]
    return None


def main():
    stim = [x for x in open("build/day/day.stim") if x.strip() and x[0] != "#"]
    ref = int(stim[0].split()[1])
    msgs = [parse(x) for x in stim[1:]]

    # pass 1: where the interesting moments are
    eng = E.Engine(ref)
    marks = []                                  # (index, label)
    n_trades = n_vol = 0
    counts = dict(orders=0, trades=0, lots=0, cancels=0, rejects=0, interruptions=0, auctions=0)
    minutes = {}
    halts = []
    for i, m in enumerate(msgs):
        ph0 = eng.phase
        out = eng.submit(m)
        for o in out:
            if o[0] == E.TRADE:
                counts["trades"] += 1
                counts["lots"] += o[4]
                mm = minutes.setdefault(max(eng.now, E.Params.T_OPEN) // 60, [o[3], o[3], o[3], 0])
                mm[0], mm[1], mm[2] = max(mm[0], o[3]), min(mm[1], o[3]), o[3]
                mm[3] += o[4]
            elif o[0] == E.ACK:
                counts["orders"] += 1
            elif o[0] == E.CXL:
                counts["cancels"] += 1
            elif o[0] == E.REJ:
                counts["rejects"] += 1
            elif o[0] == E.VI:
                counts["interruptions"] += 1
                marks.append((i, "Interruption %d" % counts["interruptions"]))
                halts.append([eng.now, eng.now + E.Params.VI_HALT_S])
            elif o[0] == E.AUC:
                counts["auctions"] += 1
                marks.append((i, AUC[o[5]] + (" %d" % len(halts) if o[5] == 1 else "")))
        if ph0 == E.CONT and eng.phase == E.CLOSE_CALL:
            marks.append((i, "Closing call starts"))
    lim_dn, lim_up = eng.lim_dn, eng.lim_up

    # pass 2: frames.  Every message near a marked moment, a thin sample elsewhere.
    keep = set(range(0, len(msgs), max(1, len(msgs) // 260)))
    shown_marks = [mk for mk in marks if not mk[1].startswith("Interruption") or int(mk[1].split()[-1]) <= 3]
    shown_marks = [mk for mk in shown_marks if not mk[1].startswith("Interruption auction") or int(mk[1].split()[-1]) <= 3]
    for i, _ in shown_marks:
        keep.update(range(max(0, i - 25), min(len(msgs), i + 26)))
    eng = E.Engine(ref)
    frames = []
    index_of = {}
    for i, m in enumerate(msgs):
        out = eng.submit(m)
        if i not in keep:
            continue
        ind = None
        if eng.phase in (E.PRE_OPEN, E.VI_HALT, E.CLOSE_CALL):
            p, v = eng.auction_price()
            ind = [p or 0, v]
        lines = [d for d in (describe_out(o) for o in out) if d]
        if len(lines) > 12:
            lines = lines[:11] + ["... and %d more lines" % (len(lines) - 11)]
        index_of[i] = len(frames)
        frames.append(dict(i=i, t=eng.now, ph=eng.phase, m=describe_in(m), o=lines,
                           b=eng.depth(E.BUY), a=eng.depth(E.SELL), mb=eng.market_qty(E.BUY),
                           ms=eng.market_qty(E.SELL), last=eng.last or 0, ind=ind))
    stats = read_stats("build/day/day.stats")
    lat = {k: dict(summary(v), hist=sorted(v.items())) for k, v in sorted(stats["classes"].items())}
    data = dict(
        ref=ref, lim_dn=lim_dn, lim_up=lim_up, counts=counts, messages=len(msgs),
        minutes=[[k * 60] + v for k, v in sorted(minutes.items())], halts=halts,
        marks=[[index_of[i], label] for i, label in shown_marks if i in index_of],
        frames=frames, phases=PHASES, lat=lat,
        cycles=stats["cycles"], cpm=round(stats["cycles"] / stats["messages"], 2),
        bench=json.load(open("build/bench.json")) if os.path.exists("build/bench.json") else None,
        synth=json.load(open("synth/summary.json")) if os.path.exists("synth/summary.json") else None,
        t_open=E.Params.T_OPEN, t_close=E.Params.T_CLOSE)
    html = open("tools/report_template.html", encoding="utf-8").read()
    html = html.replace("/*DATA*/null", json.dumps(data, separators=(",", ":")))
    os.makedirs("build", exist_ok=True)
    open("build/report.html", "w", encoding="utf-8").write(html)
    print("build/report.html: %d frames, %d KB; day had %d messages, %d trades, %d interruptions"
          % (len(frames), len(html) // 1024, len(msgs), counts["trades"], counts["interruptions"]))


if __name__ == "__main__":
    main()
