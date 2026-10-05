#!/usr/bin/env python3
"""Workload generator: random but plausible order flow for one or more trading days.

    python3 tools/gen.py --seed 1 --messages 200000 --profile mix --stim S --exp E

The generator runs the golden model alongside, so it knows which orders are still live
(to cancel or reduce them), where the market is, and which phase the day is in.  It writes
the stimulus and, at the same time, the outputs the golden model produced.

No real order data is used anywhere: TWSE order-level data is not freely available, and
every flow here comes from the seeded generator below.

Profiles
    day       one ordinary symbol-day: pre-open build-up, open, continuous flow, close
    mix       many short days cycling through every adversarial profile and many reference
              prices (tick-band boundaries, below TWD 1, the 256-level edge, ...)
    deep / sweep / edge / vi / ids / market   the adversarial flows on their own
"""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from model import engine as E  # noqa: E402
from model import rules  # noqa: E402

B, S = E.BUY, E.SELL

# knobs per profile: probabilities are per continuous-phase message
PROFILES = {
    # ordinary flow: mostly passive limit orders near the market, some takers
    "day":    dict(p_cancel=0.22, p_reduce=0.04, p_market=0.04, p_ioc=0.05, p_fok=0.02, p_snap=0.02,
                   p_bad=0.002, spread=6, cross=0.30, qty=40, dt=0.25, jump=0.0005, ids=4096),
    # a few levels, long queues, cancels in the middle
    "deep":   dict(p_cancel=0.35, p_reduce=0.15, p_market=0.02, p_ioc=0.03, p_fok=0.02, p_snap=0.02,
                   p_bad=0.0, spread=1, cross=0.15, qty=30, dt=0.05, jump=0.0, ids=4096),
    # thick book, then big takers that walk many levels
    "sweep":  dict(p_cancel=0.05, p_reduce=0.02, p_market=0.10, p_ioc=0.12, p_fok=0.10, p_snap=0.03,
                   p_bad=0.0, spread=25, cross=0.10, qty=3000, dt=0.5, jump=0.002, ids=4096),
    # invalid messages and limit-price corners
    "edge":   dict(p_cancel=0.15, p_reduce=0.08, p_market=0.05, p_ioc=0.05, p_fok=0.05, p_snap=0.05,
                   p_bad=0.20, spread=200, cross=0.30, qty=10, dt=2.0, jump=0.01, ids=64),
    # fast moves: volatility interruptions, halts, orders during halts
    "vi":     dict(p_cancel=0.15, p_reduce=0.03, p_market=0.06, p_ioc=0.06, p_fok=0.04, p_snap=0.04,
                   p_bad=0.0, spread=4, cross=0.35, qty=20, dt=4.0, jump=0.04, ids=1024),
    # tiny id space: constant reuse of ids the moment they are free
    "ids":    dict(p_cancel=0.30, p_reduce=0.05, p_market=0.05, p_ioc=0.10, p_fok=0.05, p_snap=0.02,
                   p_bad=0.02, spread=3, cross=0.40, qty=15, dt=0.3, jump=0.001, ids=8),
    # many market orders, so they rest in the book and trade as the resting side
    "market": dict(p_cancel=0.10, p_reduce=0.05, p_market=0.45, p_ioc=0.03, p_fok=0.05, p_snap=0.04,
                   p_bad=0.0, spread=3, cross=0.10, qty=8, dt=1.0, jump=0.003, ids=256),
}
MIX_ORDER = ["day", "deep", "sweep", "edge", "vi", "ids", "market"]

# reference prices (cents) that sit on or next to something interesting
EDGE_REFS = [1, 5, 9, 10, 57, 99, 100, 101, 909, 910, 995, 999, 1000, 1005, 4060, 4995, 5000, 5010,
             9990, 10000, 10050, 45450, 49950, 50000, 50100, 99900, 100000, 100500, 250000,
             639500, 640000, 1500000]


class Gen:
    def __init__(self, seed, stim, exp):
        self.rng = random.Random(seed)
        self.stim, self.exp = stim, exp
        self.n = 0              # lines written
        self.eng = None
        self.counts = {}

    # -- output ------------------------------------------------------------------------
    def reset(self, ref, vi=1):
        self.stim.write("R %d %d\n" % (ref, vi))
        self.eng = E.Engine(ref, vi_enable=bool(vi))
        if self.exp:
            self.exp.write("#%d\n" % self.n)
            if self.eng.cfg_error:
                self.exp.write("CFG 1 - - -\n")
            else:
                self.exp.write("CFG 0 %d %d %d\n" % (self.eng.lim_dn, self.eng.lim_up, len(self.eng.levels)))
        self.n += 1

    def send(self, msg):
        self.stim.write(" ".join(str(x) for x in msg) + "\n")
        if msg[0] == "X":
            self.eng.out = []
            self.eng._emit(E.REJ, code=E.REJ_CFG if self.eng.cfg_error else E.REJ_OP)
            out = self.eng.out
        else:
            out = self.eng.submit(msg)
        if self.exp:
            self.exp.write("#%d\n" % self.n)
            for m in out:
                self.exp.write("%d %d %d %d %d %d\n" % m)
        self.n += 1
        for m in out:
            self.counts[m[0]] = self.counts.get(m[0], 0) + 1
        return out

    # -- one trading day -----------------------------------------------------------------
    def day(self, ref, k, n_msgs, vi=1, to_close=True):
        rng, P = self.rng, E.Params
        self.reset(ref, vi)
        eng = self.eng
        levels = eng.levels if not eng.cfg_error else [ref]
        nl = len(levels)
        ids = k["ids"]
        fv = levels.index(ref) if ref in levels else nl // 2          # fair value, as a level
        maxq = min(k["qty"], (1 << P.QTY_BITS) - 1)

        def free_id():
            for _ in range(8):
                i = rng.randrange(ids)
                if i not in eng.orders:
                    return i
            return rng.randrange(ids)                                   # may be live: a duplicate

        def new(limit_only):
            side = rng.choice((B, S))
            r = rng.random()
            mkt, tif = 0, E.ROD
            if not limit_only or rng.random() < 0.02:                   # a few wrong types in call phases
                if r < k["p_market"]:
                    mkt = 1
                    tif = rng.choice((E.ROD, E.ROD, E.IOC, E.FOK))
                elif r < k["p_market"] + k["p_ioc"]:
                    tif = E.IOC
                elif r < k["p_market"] + k["p_ioc"] + k["p_fok"]:
                    tif = E.FOK
            # passive orders sit behind the fair value, aggressive ones cross it
            off = int(rng.expovariate(1.0 / max(k["spread"], 1)))
            if rng.random() < k["cross"]:
                off = -int(rng.expovariate(1.0 / max(k["spread"], 1))) - 1
            lvl = fv - off if side == B else fv + off
            if rng.random() < 0.03:
                lvl = rng.choice((0, nl - 1, rng.randrange(nl)))        # the limits themselves
            price = levels[min(max(lvl, 0), nl - 1)]
            qty = max(1, int(rng.expovariate(1.0 / maxq))) if rng.random() < 0.7 else rng.randint(1, maxq)
            qty = min(qty, (1 << P.QTY_BITS) - 1)
            if rng.random() < k["p_bad"]:
                bad = rng.randrange(6)
                if bad == 0:
                    price += 1 if rules.tick_of(price) > 1 else 0       # off tick
                elif bad == 1:
                    price = eng.lim_up + rules.tick_of(eng.lim_up) * rng.randint(1, 3)
                elif bad == 2:
                    price = max(0, eng.lim_dn - rules.tick_of(max(eng.lim_dn - 1, 1)) * rng.randint(1, 3))
                elif bad == 3:
                    qty = 0
                elif bad == 4:
                    tif = 3
                else:
                    price = rng.choice((0, 1, (1 << 24) - 1, rng.randrange(1 << 24)))
            return ("N", free_id(), side, mkt, tif, 0 if mkt and rng.random() < 0.9 else price, qty)

        def one(limit_only):
            r = rng.random()
            live = eng.orders
            if r < k["p_cancel"]:
                if live and rng.random() < 0.95:
                    return ("C", rng.choice(list(live)) if len(live) < 64 else next(iter(live)) if rng.random() < 0.3
                            else list(live)[rng.randrange(len(live))])
                return ("C", rng.randrange(ids))
            r -= k["p_cancel"]
            if r < k["p_reduce"]:
                if live and rng.random() < 0.95:
                    oid = list(live)[rng.randrange(len(live))]
                    q = live[oid].qty
                    return ("D", oid, rng.choice((1, max(1, q // 2), q, min(q + 1, 4095), 0)) if rng.random() < 0.3
                            else rng.randint(1, max(1, q)))
                return ("D", rng.randrange(ids), rng.randint(0, 5))
            r -= k["p_reduce"]
            if r < k["p_snap"]:
                return ("S",)
            r -= k["p_snap"]
            if r < k["p_bad"] / 10:
                return ("X", rng.choice((0, 6, 7)), rng.randrange(ids))
            return new(limit_only)

        def advance(dt_mean):
            nonlocal t
            if rng.random() < k["p_bad"] / 4 and t > P.T_ACCEPT:
                self.send(("T", t - rng.randint(1, 100)))               # time going backwards
                return
            r = rng.random()
            step = 0
            if r < 0.02:
                step = rng.randint(100, 700)                             # longer than the 5-minute window
            elif r < 0.5:
                step = int(rng.expovariate(1.0 / dt_mean)) + (1 if dt_mean >= 1 else 0)
            if step or rng.random() < 0.05:
                t += step
                self.send(("T", t))

        t = P.T_ACCEPT
        n_pre = int(n_msgs * rng.choice((0.0, 0.03, 0.10, 0.25)))
        n_close = int(n_msgs * rng.choice((0.0, 0.03, 0.10))) if to_close else 0
        sent0 = self.n
        while self.n - sent0 < n_pre:                                   # pre-open: orders accumulate
            if rng.random() < 0.05:
                t = min(t + rng.randint(0, 120), P.T_OPEN - 1)
                self.send(("T", t))
            self.send(one(limit_only=True))
        t = P.T_OPEN + (rng.randint(0, 3) if rng.random() < 0.3 else 0)
        self.send(("T", t))
        budget = n_msgs - n_close
        while self.n - sent0 < budget and t < P.T_CCALL + 5:            # continuous trading and halts
            if rng.random() < k["jump"]:
                fv = min(max(fv + rng.choice((-1, 1)) * rng.randint(nl // 12 + 1, nl // 4 + 2), 0), nl - 1)
            elif rng.random() < 0.2:
                fv = min(max(fv + rng.choice((-1, 0, 1)), 0), nl - 1)
            self.send(one(limit_only=eng.phase != E.CONT))
            advance(k["dt"])
        if to_close:
            if t < P.T_CCALL:
                t = P.T_CCALL + (rng.randint(0, 5) if rng.random() < 0.5 else -rng.randint(1, 119))
                self.send(("T", max(t, eng.now)))
                t = max(t, eng.now)
            while self.n - sent0 < n_msgs and t < P.T_CLOSE:            # closing call
                self.send(one(limit_only=True))
                if rng.random() < 0.1:
                    t += rng.randint(0, 20)
                    self.send(("T", t))
            self.send(("T", max(t, P.T_CLOSE + rng.randint(0, 2))))
            for _ in range(rng.randint(1, 6)):                          # after the close: all rejected
                self.send(one(limit_only=rng.random() < 0.5))
            self.send(("S",))


def pick_ref(rng):
    if rng.random() < 0.4:
        return rng.choice(EDGE_REFS)
    p = int(10 ** rng.uniform(0.0, 5.8))
    p -= p % rules.tick_of(p)
    return max(p, 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--messages", type=int, default=100000, help="approximate number of messages")
    ap.add_argument("--profile", default="mix", choices=["mix"] + list(PROFILES))
    ap.add_argument("--ref", type=int, default=58300, help="reference price in cents for a single-profile run")
    ap.add_argument("--day-size", type=int, default=3000, help="messages per day in the mix profile")
    ap.add_argument("--stim", required=True)
    ap.add_argument("--exp", default=None, help="also write the golden model's outputs here")
    a = ap.parse_args()
    stim = open(a.stim, "w")
    exp = open(a.exp, "w") if a.exp else None
    g = Gen(a.seed, stim, exp)
    if a.profile == "mix":
        d = 0
        while g.n < a.messages:
            name = MIX_ORDER[d % len(MIX_ORDER)]
            ref = pick_ref(g.rng)
            size = max(50, int(a.day_size * g.rng.choice((0.1, 0.5, 1.0, 2.0))))
            g.day(ref, PROFILES[name], size, vi=0 if g.rng.random() < 0.1 else 1,
                  to_close=g.rng.random() < 0.7)
            d += 1
    else:
        g.day(a.ref, PROFILES[a.profile], a.messages)
    stim.close()
    if exp:
        exp.close()
    names = {E.ACK: "acks", E.REJ: "rejects", E.TRADE: "trades", E.CXL: "cancels", E.VI: "interruptions",
             E.AUC: "auctions", E.MDE: "snapshots"}
    print("seed %d profile %s: %d lines; " % (a.seed, a.profile, g.n)
          + ", ".join("%s %d" % (names[t], g.counts.get(t, 0)) for t in names))


if __name__ == "__main__":
    main()
