#!/usr/bin/env python3
"""Exhaustive checks on small configurations (make prove).

  P1  day configuration: for EVERY valid reference price from TWD 0.01 to 7000.00 the RTL's
      limit-down, limit-up and level count equal the rule (R2, R3), and the engine refuses
      exactly the references that need more than 256 levels
  P2  price grid: for a sample of reference prices that includes every one whose range holds a
      tick-band boundary (every 3rd of those), each valid tick is accepted and maps back to the
      same price, and its off-tick and out-of-limit neighbours are rejected with the right code
  P3  auction price: for ALL 19,683 books of 4 adjacent price levels with 0..2 lots per side
      per level, with the anchor below, inside and above them, the RTL's price and volume
      equal a literal reading of R6, and the set of qualifying prices is one contiguous run
      of ticks (the argument behind R6.1)

    python3 tools/prove.py [--quick]      # --quick: a tenth of P1/P2, for CI
"""
import itertools
import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from model import engine as E  # noqa: E402
from model import rules  # noqa: E402

SIM = os.environ.get("TM_SIM", os.path.join("build", "sim_tm"))    # mutate.py points this at a mutant


def run_sim(stim_text):
    r = subprocess.run([SIM], input=stim_text, capture_output=True, text=True, cwd=ROOT)
    if r.returncode:
        sys.exit("simulator failed: " + r.stderr)
    groups, cur = [], None
    for line in r.stdout.splitlines():
        if line[0] == "#":
            cur = []
            groups.append(cur)
        else:
            cur.append(line)
    return groups


def all_refs(limit_cents, step=1):
    p, k = 1, 0
    while p <= limit_cents:
        if k % step == 0:
            yield p
        p += rules.tick_of(p)
        k += 1


def p1(quick):
    refs = list(all_refs(700000, 10 if quick else 1))
    groups = run_sim("".join("R %d 1\n" % r for r in refs))
    refused = 0
    for ref, g in zip(refs, groups):
        levels = rules.price_levels(ref)
        f = g[0].split()
        if len(levels) > 256:
            assert f[1] == "1", "reference %d should be refused" % ref
            refused += 1
        else:
            dn, up = rules.daily_limits(ref)
            assert f[1:] == ["0", str(dn), str(up), str(len(levels))], (ref, f, dn, up, len(levels))
    print("P1 ok: %d reference prices, limits and level count equal the rule; %d refused (> 256 levels)"
          % (len(refs), refused))


def p2(quick):
    with_boundary = [r for r in all_refs(700000) if len({rules.tick_of(x) for x in rules.price_levels(r)}) == 2]
    plain = [r for r in all_refs(639500, 97)]
    refs = with_boundary[::30 if quick else 3] + plain[::10 if quick else 1]
    stim, expect = [], []
    n_prices = 0
    for ref in refs:
        eng = E.Engine(ref)
        if eng.cfg_error:
            continue
        stim.append("R %d 1" % ref)
        expect.append(None)

        def send(msg):
            stim.append(" ".join(str(x) for x in msg))
            expect.append([E.format_out(m) for m in eng.submit(msg)])

        t_dn, t_up = rules.tick_of(max(eng.lim_dn - 1, 1)), rules.tick_of(eng.lim_up)
        for bad in (eng.lim_dn - t_dn, eng.lim_dn - 1, eng.lim_up + t_up, eng.lim_up + 1, 0):
            if bad >= 0:
                send(("N", 1, E.SELL, 0, E.ROD, bad, 1))
        for p in eng.levels:
            send(("N", 1, E.SELL, 0, E.ROD, p, 1))
            send(("S",))                    # the snapshot converts the level back to a price
            send(("C", 1))
            if rules.tick_of(p) > 1:
                send(("N", 1, E.BUY, 0, E.ROD, p + 1, 1))
                send(("N", 1, E.BUY, 0, E.ROD, p + rules.tick_of(p) - 1, 1))
            n_prices += 1
    groups = run_sim("\n".join(stim) + "\n")
    assert len(groups) == len(expect)
    for i, (g, e) in enumerate(zip(groups, expect)):
        if e is not None:
            assert g == e, "P2 mismatch at %r: rtl %r, rule %r" % (stim[i], g, e)
    print("P2 ok: %d reference prices (%d with a tick-band boundary inside the limits), %d price levels "
          "accepted and mapped back, neighbours rejected" % (len(refs), len(with_boundary[::30 if quick else 3]), n_prices))


def p3():
    ref = 10000                                         # tick 0.50 above, 0.10 below: anchor at level index 100
    eng0 = E.Engine(ref)
    lv = eng0.levels
    a = lv.index(ref)
    total = 0
    for name, base in (("anchor below", a + 2), ("anchor inside", a - 2), ("anchor above", a - 6)):
        prices = lv[base:base + 4]
        eng = E.Engine(ref)
        stim = ["R %d 1" % ref]
        expect = [None]
        for book in itertools.product(range(3), repeat=8):
            msgs = []
            for k, q in enumerate(book):
                if q:
                    msgs.append(("N", k, k // 4, 0, E.ROD, prices[k % 4], q))
            msgs.append(("S",))
            msgs += [("C", k) for k, q in enumerate(book) if q]
            for m in msgs:
                if m[0] == "S":
                    contiguous(eng, lv)
                stim.append(" ".join(str(x) for x in m))
                expect.append([E.format_out(o) for o in eng.submit(m)])
            total += 1
        groups = run_sim("\n".join(stim) + "\n")
        for i, (g, e) in enumerate(zip(groups, expect)):
            if e is not None:
                assert g == e, "P3 (%s) mismatch at %r: rtl %r, rule %r" % (name, stim[i], g, e)
    print("P3 ok: %d books (4 levels x 2 sides x 0..2 lots, anchor below/inside/above): auction price, "
          "volume and simulated best five equal the literal rule; qualifying prices always contiguous" % total)


def contiguous(eng, lv):
    """R6.1: the prices satisfying principles 1 and 2 at maximum volume are adjacent ticks."""
    bids = {p: eng._level_qty(E.BUY, p) for p in eng.book[E.BUY]}
    asks = {p: eng._level_qty(E.SELL, p) for p in eng.book[E.SELL]}
    rows = []
    for i, px in enumerate(lv):
        b = sum(q for p, q in bids.items() if p >= px)
        s = sum(q for p, q in asks.items() if p <= px)
        v = min(b, s)
        rows.append((i, v, b - bids.get(px, 0) <= v and s - asks.get(px, 0) <= v))
    vmax = max(r[1] for r in rows)
    if vmax:
        good = [i for i, v, ok in rows if v == vmax and ok]
        assert good and good == list(range(good[0], good[-1] + 1)), "qualifying prices not contiguous"


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    p1(quick)
    p2(quick)
    p3()
