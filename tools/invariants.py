#!/usr/bin/env python3
"""Check an output log against invariants that hold whatever the order flow.

    python3 tools/invariants.py stimulus outputs

It rebuilds the book from the messages alone (it shares no code with the golden model's
matching) and fails on the first violation of any of these:

  I1  price-time priority: the resting side of every trade is the order at the front of
      the best queue on its side (market orders first, then best price, oldest first)
  I2  every trade price is a valid tick inside the daily limits, and no worse than the
      limit of either order
  I3  quantity is conserved: fills, reductions and cancels never exceed an order's size,
      a cancel notice carries exactly what was left, FOK orders fill completely or not at all
  I4  an order that was cancelled, fully filled or rejected never trades again; IOC, FOK
      and (in call phases) market orders never rest
  I5  during continuous trading the book is never crossed, and a resting market order
      never coexists with any resting order on the other side
  I6  each auction price satisfies R6: maximum volume, all better-priced orders filled,
      nearest qualifying tick to the anchor; the auction's fills add up to its volume
  I7  best-five snapshots agree with the rebuilt book (continuous phase), and with the
      auction computed from it (call phases)
  I8  no trades and no accepted orders after the close; only limit-ROD accepted in call phases
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from model import rules  # noqa: E402

ACK, REJ, TRADE, CXL, RED, VI, AUC, PHASE, MDL, MDE = range(1, 11)
PRE, CONT, HALT, CCALL, CLOSED = range(5)


class Violation(Exception):
    pass


class Shadow:
    def __init__(self, ref, lim_dn, lim_up):
        self.ref, self.lim_dn, self.lim_up = ref, lim_dn, lim_up
        self.ord = {}                   # id -> dict(side, mkt, price, rem)
        self.q = ({}, {})               # side -> price -> [ids]
        self.mq = ([], [])              # side -> [ids] resting market orders
        self.phase = PRE
        self.last = None
        self.ticks = None

    def need(self, cond, what):
        if not cond:
            raise Violation(what)

    def best(self, side):
        b = self.q[side]
        return (max(b) if side == 0 else min(b)) if b else None

    def front(self, side):
        """id that must trade next on `side` (I1)."""
        if self.mq[side]:
            return self.mq[side][0]
        p = self.best(side)
        return None if p is None else self.q[side][p][0]

    def rest(self, oid):
        o = self.ord[oid]
        if o["mkt"]:
            self.mq[o["side"]].append(oid)
        else:
            self.q[o["side"]].setdefault(o["price"], []).append(oid)
        o["resting"] = True

    def drop(self, oid):
        o = self.ord.pop(oid)
        if o.get("resting"):
            if o["mkt"]:
                self.mq[o["side"]].remove(oid)
            else:
                lv = self.q[o["side"]][o["price"]]
                lv.remove(oid)
                if not lv:
                    del self.q[o["side"]][o["price"]]

    def check_price(self, price):
        self.need(self.lim_dn <= price <= self.lim_up and rules.is_valid_price(price),
                  "I2: trade price %d outside the limits or off tick" % price)

    def fill(self, oid, qty, price):
        o = self.ord.get(oid)
        self.need(o is not None, "I4: order %d trades but is not live" % oid)
        self.need(0 < qty <= o["rem"], "I3: order %d overfilled" % oid)
        if not o["mkt"]:
            ok = price <= o["price"] if o["side"] == 0 else price >= o["price"]
            self.need(ok, "I2: order %d traded at %d, worse than its limit %d" % (oid, price, o["price"]))
        o["rem"] -= qty
        o["filled"] = o.get("filled", 0) + qty
        if o["rem"] == 0 and o.get("resting"):
            self.drop(oid)

    def levels(self, side):
        return {p: sum(self.ord[i]["rem"] for i in ids) for p, ids in self.q[side].items()}

    def auction(self):
        """R6 from the rebuilt book: (price, volume), written independently of the model."""
        if self.ticks is None:
            self.ticks = rules.price_levels(self.ref)
        bids, asks = self.levels(0), self.levels(1)
        rows = []
        for px in self.ticks:
            b = sum(q for p, q in bids.items() if p >= px)
            s = sum(q for p, q in asks.items() if p <= px)
            rows.append((px, min(b, s), b - bids.get(px, 0), s - asks.get(px, 0)))
        vmax = max(r[1] for r in rows)
        if vmax == 0:
            return 0, 0
        good = [px for px, v, bgt, slt in rows if v == vmax and bgt <= v and slt <= v]
        anchor = self.last if self.last is not None else self.ref
        return min(good, key=lambda px: (abs(px - anchor), px)), vmax

    def after_message(self):
        if self.phase == CONT:
            bb, ba = self.best(0), self.best(1)
            self.need(bb is None or ba is None or bb < ba, "I5: book crossed in continuous trading")
            self.need(not (self.mq[0] and (self.q[1] or self.mq[1])), "I5: market buy rests beside sell orders")
            self.need(not (self.mq[1] and (self.q[0] or self.mq[0])), "I5: market sell rests beside buy orders")
        if self.phase in (PRE, HALT, CCALL):
            self.need(not self.mq[0] and not self.mq[1], "I4: market order resting in a call phase")


def check(stim_lines, out_groups):
    sh = None
    n_days = n_checked = 0
    for line, outs in zip(stim_lines, out_groups):
        f = line.split()
        op = f[0]
        a = [int(x) for x in f[1:]]
        try:
            if op == "R":
                cfg = outs[0].split()
                if cfg[1] == "1":
                    sh = None
                else:
                    sh = Shadow(a[0], int(cfg[2]), int(cfg[3]))
                    lim = rules.daily_limits(a[0])
                    if lim != (sh.lim_dn, sh.lim_up):
                        raise Violation("limits %s differ from R3 %s" % ((sh.lim_dn, sh.lim_up), lim))
                n_days += 1
                continue
            msgs = [tuple(int(x) for x in o.split()) for o in outs]
            if sh is None:
                if not (len(msgs) == 1 and msgs[0][0] == REJ):
                    raise Violation("message accepted on a day the engine refused")
                continue
            one(sh, op, a, msgs)
            sh.after_message()
            n_checked += 1
        except Violation as e:
            raise Violation("%s\n  at stimulus: %s  outputs: %s" % (e, line.strip(), outs))
    return n_days, n_checked


def one(sh, op, a, msgs):
    need = sh.need
    inc = None
    tif = 0
    if op == "N":
        oid, side, mkt, tif, price, qty = a
        if msgs[0][0] == ACK:
            need(sh.phase != CLOSED, "I8: order accepted after the close")
            need(sh.phase == CONT or (not mkt and tif == 0), "I8: only limit ROD in call phases")
            need(oid not in sh.ord, "I4: id %d accepted while still live" % oid)
            need(qty > 0, "I3: zero quantity accepted")
            if not mkt:
                sh.check_price(price)
            sh.ord[oid] = dict(side=side, mkt=bool(mkt), price=price, rem=qty, qty=qty)
            inc = oid
        else:
            need(len(msgs) == 1 and msgs[0][0] == REJ, "reject must be the only output")
    auc = None
    auc_done = 0
    for typ, i1, i2, price, qty, code in msgs:
        if typ == TRADE:
            need(sh.phase != CLOSED, "I8: trade after the close")
            sh.check_price(price)
            if code == 0:                                   # continuous: incoming against the front
                need(inc is not None and inc in (i1, i2), "trade without an incoming order")
                rest = i2 if inc == i1 else i1
                side = sh.ord[inc]["side"]
                need((i1 == inc) == (side == 0), "buy/sell ids swapped")
                need(sh.front(1 - side) == rest, "I1: order %d traded out of turn" % rest)
                if not sh.ord[rest]["mkt"]:
                    need(price == sh.ord[rest]["price"], "R7: fill not at the resting order's price")
                sh.fill(rest, qty, price)
                sh.fill(inc, qty, price)
            else:                                           # auction: front of both sides
                need(auc is not None and price == auc[0], "I6: auction fill at another price")
                need(sh.front(0) == i1 and sh.front(1) == i2, "I1: auction fill out of turn")
                sh.fill(i1, qty, price)
                sh.fill(i2, qty, price)
                auc_done += qty
            sh.last = price
        elif typ == CXL:
            o = sh.ord.get(i1)
            need(o is not None, "I4: cancel notice for an order that is not live")
            need(qty == o["rem"], "I3: cancel notice for %d lots, %d were left" % (qty, o["rem"]))
            if code == 5:
                need(o["mkt"] and o.get("resting"), "only resting market orders are withdrawn")
            if code == 3:
                need(o.get("filled", 0) == 0, "I3: FOK order partly filled")
            sh.drop(i1)
        elif typ == RED:
            o = sh.ord.get(i1)
            need(o is not None and op == "D", "reduce notice out of place")
            need(0 < qty == o["rem"] - a[1], "I3: reduce arithmetic")
            o["rem"] = qty
        elif typ == VI:
            need(sh.phase == CONT and inc is not None and tif == 0 and not sh.ord[inc]["mkt"],
                 "R8: only a limit ROD order in continuous trading starts an interruption")
        elif typ == AUC:
            exp = sh.auction()
            need((price, qty) == exp, "I6: auction says %s, the book gives %s" % ((price, qty), exp))
            auc = (price, qty)
            auc_done = 0
        elif typ == PHASE:
            if auc is not None:
                need(auc_done == auc[1], "I6: auction fills %d, volume %d" % (auc_done, auc[1]))
                bb, ba = sh.best(0), sh.best(1)
                need(bb is None or ba is None or bb < ba, "I6: book still crossed after the auction")
                auc = None
            if inc is not None and code == HALT and sh.ord.get(inc) and not sh.ord[inc].get("resting"):
                sh.rest(inc)                                # the triggering order stays in the book
            sh.phase = code
    if op == "S":
        need(msgs[-1][0] == MDE, "snapshot must end with MDE")
        got_b = [(m[3], m[4]) for m in msgs if m[0] == MDL and m[5] < 5]
        got_a = [(m[3], m[4]) for m in msgs if m[0] == MDL and 8 <= m[5] < 13]
        bids, asks = sh.levels(0), sh.levels(1)
        if sh.phase in (PRE, HALT, CCALL):
            ap, av = sh.auction()
            need((msgs[-1][3], msgs[-1][4]) == (ap, av), "I7: simulated auction in the snapshot")
            if av:
                left = av
                for p in sorted(bids, reverse=True):
                    take = min(left, bids[p])
                    bids[p] -= take
                    left -= take
                left = av
                for p in sorted(asks):
                    take = min(left, asks[p])
                    asks[p] -= take
                    left -= take
        else:
            need(msgs[-1][3] == (sh.last or 0), "I7: last price in the snapshot")
        need(got_b == [(p, bids[p]) for p in sorted(bids, reverse=True) if bids[p]][:5], "I7: best five bids")
        need(got_a == [(p, asks[p]) for p in sorted(asks) if asks[p]][:5], "I7: best five asks")
        mk = {m[5]: m[4] for m in msgs if m[0] == MDL and m[5] in (7, 15)}
        for side, c in ((0, 7), (1, 15)):
            need(mk.get(c, 0) == sum(sh.ord[i]["rem"] for i in sh.mq[side]), "I7: market quantity")
    if inc is not None and inc in sh.ord:
        o = sh.ord[inc]
        if o["rem"] == 0:
            del sh.ord[inc]
        elif not o.get("resting"):
            need(tif == 0, "I4: IOC/FOK order left in the book")
            sh.rest(inc)
    if op == "N" and tif == 2 and inc is not None:
        need(inc not in sh.ord, "I3: FOK order neither filled nor cancelled")


def read_groups(path):
    cur = None
    with open(path) as f:
        for line in f:
            if line[0] == "#":
                if cur is not None:
                    yield cur
                cur = []
            else:
                cur.append(line.rstrip("\n"))
    if cur is not None:
        yield cur


def main():
    with open(sys.argv[1]) as f:
        stim = [x for x in f if x.strip() and x[0] != "#"]
    try:
        days, n = check(stim, read_groups(sys.argv[2]))
    except Violation as e:
        print("INVARIANT VIOLATED:", e)
        return 1
    print("invariants hold: %d days, %d messages" % (days, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
