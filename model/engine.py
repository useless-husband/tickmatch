"""Golden model of one symbol's trading day under TWSE rules.

Written to be read next to docs/RULES.md: each method names the rule it
implements (R1..R9).  It works on prices in cents and plain Python
containers; the RTL works on level indices and linked lists.  The two share
only the message format, so agreeing on every output message is evidence
and not a tautology.

Input messages are tuples:
    ("N", id, side, is_market, tif, price, qty)   new order
    ("C", id)                                      cancel
    ("D", id, qty)                                 reduce by qty
    ("T", seconds_of_day)                          time moves forward
    ("S",)                                         best-five snapshot request
Output messages are tuples (type, id, id2, price, qty, code); see the
constants below and docs/DESIGN.md.
"""
from collections import OrderedDict, deque

from . import rules

BUY, SELL = 0, 1
ROD, IOC, FOK = 0, 1, 2

# output message types
ACK, REJ, TRADE, CXL, RED, VI, AUC, PHASE, MDL, MDE = range(1, 11)
# phases (R1)
PRE_OPEN, CONT, VI_HALT, CLOSE_CALL, CLOSED = range(5)
# reject codes
(REJ_CLOSED, REJ_TYPE, REJ_LIMIT, REJ_TICK, REJ_QTY, REJ_DUP, REJ_UNKNOWN,
 REJ_TIME, REJ_OP, REJ_CFG) = range(1, 11)
# cancel reasons
CXL_USER, CXL_REDUCE, CXL_IOC, CXL_FOK, CXL_BAND, CXL_PURGE = range(6)
# auction kinds
AUC_OPEN, AUC_VI, AUC_CLOSE = range(3)

TYPE_NAMES = {ACK: "ACK", REJ: "REJ", TRADE: "TRADE", CXL: "CXL", RED: "RED", VI: "VI",
              AUC: "AUC", PHASE: "PHASE", MDL: "MDL", MDE: "MDE"}


def hms(h, m, s=0):
    return h * 3600 + m * 60 + s


class Params:
    """Every number the rules leave to a parameter (docs/RULES.md)."""
    T_ACCEPT = hms(8, 30)       # R1
    T_OPEN = hms(9, 0)          # R1
    T_CCALL = hms(13, 25)       # R1
    T_CLOSE = hms(13, 30)       # R1
    LIMIT_PCT = 10              # R3
    VI_BAND_PERMILLE = 35       # R8
    VI_HALT_S = 120             # R8
    VI_FIX_S = 300              # R8
    VI_WIN_S = 300              # R8
    VI_MIN_REF = 100            # R8: no interruption below TWD 1.00
    ID_BITS = 12                # capacity: live order ids are 0 .. 2**ID_BITS-1
    LVL_BITS = 8                # capacity: at most 2**LVL_BITS price levels
    QTY_BITS = 12               # capacity: order quantity 1 .. 2**QTY_BITS-1 lots


class Order:
    __slots__ = ("id", "side", "market", "price", "qty")

    def __init__(self, oid, side, market, price, qty):
        self.id, self.side, self.market, self.price, self.qty = oid, side, market, price, qty


class Engine:
    def __init__(self, ref_price, params=Params, vi_enable=True):
        self.p = params
        self.vi_enable = vi_enable      # per-symbol switch: TWSE exempts some securities (R8)
        self.ref = ref_price
        self.lim_dn, self.lim_up = rules.daily_limits(ref_price, params.LIMIT_PCT)      # R3
        self.levels = rules.price_levels(ref_price, params.LIMIT_PCT)
        self.cfg_error = len(self.levels) > (1 << params.LVL_BITS)
        self.phase = PRE_OPEN
        self.now = params.T_ACCEPT
        self.orders = {}                                    # id -> Order (live orders only)
        self.book = ({}, {})                                # side -> {price: OrderedDict(id -> Order)}
        self.mkt = (OrderedDict(), OrderedDict())           # side -> resting market orders, FIFO (R7.1)
        self.last = None                                    # most recent trade price of the session
        self.open_price = None                              # R6.2
        self.window = deque()                               # (second, price, qty) of continuous trades (R8)
        self.fix_ref = None                                 # R8 fixed reference ...
        self.fix_until = -1                                 # ... valid while now <= fix_until
        self.vi_end = None
        self.out = []

    # ------------------------------------------------------------------ helpers
    def _emit(self, typ, oid=0, oid2=0, price=0, qty=0, code=0):
        self.out.append((typ, oid, oid2, price, qty, code))

    def _best(self, side):
        """R5 price priority: highest buy / lowest sell limit price, or None."""
        b = self.book[side]
        if not b:
            return None
        return max(b) if side == BUY else min(b)

    def _level_qty(self, side, price):
        return sum(o.qty for o in self.book[side].get(price, {}).values())

    def _rest(self, o):
        """Put an order at the back of its queue (R5 time priority)."""
        self.orders[o.id] = o
        if o.market:
            self.mkt[o.side][o.id] = o
        else:
            self.book[o.side].setdefault(o.price, OrderedDict())[o.id] = o

    def _remove(self, o):
        del self.orders[o.id]
        if o.market:
            del self.mkt[o.side][o.id]
        else:
            lvl = self.book[o.side][o.price]
            del lvl[o.id]
            if not lvl:
                del self.book[o.side][o.price]

    def _anchor(self):
        """R6 para. 3 / R7.1: most recent trade price, else the reference price."""
        return self.last if self.last is not None else self.ref

    # ------------------------------------------------------------------ R7.1
    def _converted(self, side, incoming):
        """Converted reference price of a market order on `side` (R7.1).

        The incoming limit order counts as part of the book (R7.2).
        """
        buys = list(self.book[BUY])
        sells = list(self.book[SELL])
        if incoming is not None and not incoming.market:
            (buys if incoming.side == BUY else sells).append(incoming.price)
        cands = [self._anchor()]
        if side == BUY:
            if buys:
                cands.append(max(buys))
            if sells:
                cands.append(max(sells))
            return max(cands)
        if buys:
            cands.append(min(buys))
        if sells:
            cands.append(min(sells))
        return min(cands)

    # ------------------------------------------------------------------ R8
    def _vi_reference(self):
        """(numerator, denominator) of the reference price for an order entered now (R8)."""
        if self.now <= self.fix_until:
            return self.fix_ref, 1
        lo = self.now - self.p.VI_WIN_S
        pq = sum(p * q for t, p, q in self.window if t > lo)
        q = sum(q for t, p, q in self.window if t > lo)
        if q:
            return pq, q
        return self._anchor(), 1

    def _vi_applies(self):
        return self.phase == CONT and self.vi_enable and self.ref >= self.p.VI_MIN_REF

    def _beyond_band(self, price, vref):
        """R8.1: strictly more than 3.5% away from the reference, compared exactly."""
        num, den = vref
        k = self.p.VI_BAND_PERMILLE
        return 1000 * price * den > (1000 + k) * num or 1000 * price * den < (1000 - k) * num

    # ------------------------------------------------------------------ R7 continuous matching
    def _candidate(self, inc):
        """The resting order the incoming order would trade with next, and the price.

        Returns (order, price) or None.  Resting market orders come first (R7.1),
        then the best level (R5).  A trade happens at the resting order's price (R7).
        """
        opp = 1 - inc.side
        if self.mkt[opp]:
            resting = next(iter(self.mkt[opp].values()))
            rest_px = self._converted(opp, inc)
        else:
            best = self._best(opp)
            if best is None:
                return None
            resting = next(iter(self.book[opp][best].values()))
            rest_px = best
        inc_px = self._converted(inc.side, inc) if inc.market else inc.price
        buy_px, sell_px = (inc_px, rest_px) if inc.side == BUY else (rest_px, inc_px)
        if buy_px < sell_px:
            return None
        return resting, rest_px

    def _fok_fillable(self, inc, vref):
        """R4/R8: can the whole order trade right now, every fill inside the band?"""
        opp = 1 - inc.side
        avail = 0
        if self.mkt[opp]:
            px = self._converted(opp, inc)
            if self._vi_applies() and self._beyond_band(px, vref):
                return False
            avail += sum(o.qty for o in self.mkt[opp].values())
        prices = sorted(self.book[opp], reverse=(opp == BUY))
        for px in prices:
            if avail >= inc.qty:
                break
            if not inc.market and (px > inc.price if inc.side == BUY else px < inc.price):
                break
            if self._vi_applies() and self._beyond_band(px, vref):
                break
            avail += self._level_qty(opp, px)
        return avail >= inc.qty

    def _fill(self, buy, sell, price, qty, auction):
        self._emit(TRADE, buy.id, sell.id, price, qty, 1 if auction else 0)
        self.last = price
        if not auction:
            self.window.append((self.now, price, qty))          # R8.1: only continuous trades
        for o in (buy, sell):
            o.qty -= qty
            if o.qty == 0 and o.id in self.orders:
                self._remove(o)

    def _purge_market_orders(self):
        """R4: resting market orders are withdrawn when a call phase begins."""
        for side in (BUY, SELL):
            for o in list(self.mkt[side].values()):
                self._emit(CXL, o.id, qty=o.qty, code=CXL_PURGE)
                self._remove(o)

    def _new_continuous(self, inc, tif):
        vref = self._vi_reference()
        while self.window and self.window[0][0] <= self.now - self.p.VI_WIN_S:
            self.window.popleft()
        if tif == FOK and not self._fok_fillable(inc, vref):
            self._emit(CXL, inc.id, qty=inc.qty, code=CXL_FOK)
            return
        while inc.qty > 0:
            cand = self._candidate(inc)
            if cand is None:
                break
            resting, px = cand
            if self._vi_applies() and self._beyond_band(px, vref):
                if not inc.market and tif == ROD:
                    # R8 para. 1: the rest stays in the book, matching is postponed
                    self._emit(VI, inc.id, price=px)
                    self._rest(inc)
                    self._purge_market_orders()
                    self.phase = VI_HALT
                    self.vi_end = self.now + self.p.VI_HALT_S
                    self._emit(PHASE, qty=self.now, code=VI_HALT)
                else:
                    # R8 para. 2: the part beyond the band is cancelled
                    self._emit(CXL, inc.id, qty=inc.qty, code=CXL_BAND)
                return
            qty = min(inc.qty, resting.qty)
            buy, sell = (inc, resting) if inc.side == BUY else (resting, inc)
            self._fill(buy, sell, px, qty, auction=False)
        if inc.qty > 0:
            if tif == ROD:
                self._rest(inc)                 # R4; a market ROD order rests at top priority (R7.1)
            else:
                self._emit(CXL, inc.id, qty=inc.qty, code=CXL_IOC)

    # ------------------------------------------------------------------ R6 call auction
    def auction_price(self):
        """R6: (price, volume) of a call auction over the current book, or (None, 0).

        A literal reading of the three principles over every valid tick
        inside the daily limits.
        """
        bids = {p: self._level_qty(BUY, p) for p in self.book[BUY]}
        asks = {p: self._level_qty(SELL, p) for p in self.book[SELL]}
        best_v, good = 0, []
        for px in self.levels:
            buy_ge = sum(q for p, q in bids.items() if p >= px)
            sell_le = sum(q for p, q in asks.items() if p <= px)
            vol = min(buy_ge, sell_le)
            buy_gt = buy_ge - bids.get(px, 0)
            sell_lt = sell_le - asks.get(px, 0)
            principle1 = buy_gt <= vol and sell_lt <= vol       # better-priced orders all satisfied
            principle2 = vol - buy_gt == bids.get(px, 0) or vol - sell_lt == asks.get(px, 0)
            if not (principle1 and principle2):
                continue
            if vol > best_v:
                best_v, good = vol, [px]
            elif vol == best_v:
                good.append(px)
        if best_v == 0:
            return None, 0
        anchor = self._anchor()
        dist = min(abs(px - anchor) for px in good)
        nearest = [px for px in good if abs(px - anchor) == dist]
        assert len(nearest) == 1, "R6.1: equidistant candidates should be impossible"
        return nearest[0], best_v

    def _run_auction(self, kind):
        price, vol = self.auction_price()
        self._emit(AUC, price=price or 0, qty=vol, code=kind)
        done = 0
        while price is not None:
            bb, ba = self._best(BUY), self._best(SELL)
            if bb is None or ba is None or bb < price or ba > price:
                break
            buy = next(iter(self.book[BUY][bb].values()))
            sell = next(iter(self.book[SELL][ba].values()))
            qty = min(buy.qty, sell.qty)
            self._fill(buy, sell, price, qty, auction=True)
            done += qty
        assert done == vol
        return price

    # ------------------------------------------------------------------ R1 session clock
    def _advance(self, t):
        p = self.p
        while True:
            if self.phase == PRE_OPEN and t >= p.T_OPEN:
                price = self._run_auction(AUC_OPEN)
                self.open_price = price
                self.fix_ref = price if price is not None else self.ref      # R8 para. 7(1)
                self.fix_until = p.T_OPEN + p.VI_FIX_S
                self.phase = CONT
                self._emit(PHASE, qty=p.T_OPEN, code=CONT)
            elif self.phase == CONT and t >= p.T_CCALL:
                self._purge_market_orders()                                  # R4
                self.phase = CLOSE_CALL
                self._emit(PHASE, qty=p.T_CCALL, code=CLOSE_CALL)
            elif self.phase == VI_HALT and self.vi_end >= p.T_CCALL and t >= p.T_CCALL:
                self.phase = CLOSE_CALL                                      # R8.1: merges into the close
                self._emit(PHASE, qty=p.T_CCALL, code=CLOSE_CALL)
            elif self.phase == VI_HALT and self.vi_end < p.T_CCALL and t >= self.vi_end:
                price = self._run_auction(AUC_VI)
                if price is not None:                                        # R8 para. 7(3)
                    self.fix_ref = price
                    self.fix_until = self.vi_end + p.VI_FIX_S
                self.phase = CONT
                self._emit(PHASE, qty=self.vi_end, code=CONT)
            elif self.phase == CLOSE_CALL and t >= p.T_CLOSE:
                self._run_auction(AUC_CLOSE)
                self.phase = CLOSED
                self._emit(PHASE, qty=p.T_CLOSE, code=CLOSED)
            else:
                break
        self.now = t

    # ------------------------------------------------------------------ R9 market data
    def _snapshot(self):
        bids = {p: self._level_qty(BUY, p) for p in self.book[BUY]}
        asks = {p: self._level_qty(SELL, p) for p in self.book[SELL]}
        price, vol = 0, 0
        if self.phase in (PRE_OPEN, VI_HALT, CLOSE_CALL):
            # simulated match: what the book would look like after the auction
            ap, vol = self.auction_price()
            if ap is not None:
                price = ap
                left = vol
                for p in sorted(bids, reverse=True):
                    take = min(left, bids[p]) if p >= ap else 0
                    bids[p] -= take
                    left -= take
                left = vol
                for p in sorted(asks):
                    take = min(left, asks[p]) if p <= ap else 0
                    asks[p] -= take
                    left -= take
        else:
            price = self.last or 0
        for rank, p in enumerate([p for p in sorted(bids, reverse=True) if bids[p]][:5]):
            self._emit(MDL, price=p, qty=bids[p], code=rank)
        for rank, p in enumerate([p for p in sorted(asks) if asks[p]][:5]):
            self._emit(MDL, price=p, qty=asks[p], code=8 | rank)
        for side in (BUY, SELL):
            q = sum(o.qty for o in self.mkt[side].values())
            if q:
                self._emit(MDL, qty=q, code=side * 8 + 7)
        self._emit(MDE, price=price, qty=vol, code=self.phase)

    # ------------------------------------------------------------------ message entry
    def submit(self, msg):
        """Process one input message; returns the list of output messages."""
        self.out = []
        op = msg[0]
        oid = msg[1] if op in "NCD" else 0
        if self.cfg_error:
            self._emit(REJ, oid, code=REJ_CFG)
        elif op == "N":
            self._new(*msg[1:])
        elif op == "C":
            self._cancel(oid)
        elif op == "D":
            self._reduce(oid, msg[2])
        elif op == "T":
            if msg[1] < self.now:
                self._emit(REJ, code=REJ_TIME)
            else:
                self._advance(msg[1])
        elif op == "S":
            self._snapshot()
        else:
            self._emit(REJ, code=REJ_OP)
        return self.out

    def _new(self, oid, side, market, tif, price, qty):
        assert 0 <= oid < (1 << self.p.ID_BITS) and 0 <= qty < (1 << self.p.QTY_BITS)
        if self.phase == CLOSED:
            return self._emit(REJ, oid, code=REJ_CLOSED)
        if tif not in (ROD, IOC, FOK):
            return self._emit(REJ, oid, code=REJ_OP)
        if qty == 0:
            return self._emit(REJ, oid, code=REJ_QTY)
        if oid in self.orders:
            return self._emit(REJ, oid, code=REJ_DUP)
        if self.phase != CONT and (market or tif != ROD):                 # R4
            return self._emit(REJ, oid, code=REJ_TYPE)
        if market:
            price = 0
        else:
            if not self.lim_dn <= price <= self.lim_up:                   # R3
                return self._emit(REJ, oid, code=REJ_LIMIT)
            if not rules.is_valid_price(price):                           # R2
                return self._emit(REJ, oid, code=REJ_TICK)
        self._emit(ACK, oid, price=price, qty=qty)
        order = Order(oid, side, bool(market), price, qty)
        if self.phase == CONT:
            self._new_continuous(order, tif)
        else:
            self._rest(order)                                             # call phases only accumulate (R1)

    def _cancel(self, oid):
        if self.phase == CLOSED:
            return self._emit(REJ, oid, code=REJ_CLOSED)
        o = self.orders.get(oid)
        if o is None:
            return self._emit(REJ, oid, code=REJ_UNKNOWN)
        self._emit(CXL, oid, qty=o.qty, code=CXL_USER)
        self._remove(o)

    def _reduce(self, oid, dec):
        """R4.1: a reduction keeps the order's place in the queue."""
        if self.phase == CLOSED:
            return self._emit(REJ, oid, code=REJ_CLOSED)
        o = self.orders.get(oid)
        if o is None:
            return self._emit(REJ, oid, code=REJ_UNKNOWN)
        if dec == 0:
            return self._emit(REJ, oid, code=REJ_QTY)
        if dec >= o.qty:
            self._emit(CXL, oid, qty=o.qty, code=CXL_REDUCE)
            self._remove(o)
        else:
            o.qty -= dec
            self._emit(RED, oid, qty=o.qty)

    # ------------------------------------------------------------------ views for tests and the report
    def depth(self, side, n=5):
        prices = sorted(self.book[side], reverse=(side == BUY))[:n]
        return [(p, self._level_qty(side, p)) for p in prices]

    def market_qty(self, side):
        return sum(o.qty for o in self.mkt[side].values())


def format_out(m):
    """Canonical text form of an output message (shared with the RTL harness)."""
    return "%d %d %d %d %d %d" % m
