"""The golden model must reproduce every worked example in TWSE's material."""
import unittest

from model import engine as E
from model import rules
from tests.official_vectors import VECTORS, cents, messages


def merged_trades(out):
    """Fills as (price, qty), consecutive fills at one price merged, as the sources print them."""
    res = []
    for typ, _, _, price, qty, _ in out:
        if typ == E.TRADE:
            if res and res[-1][0] == price:
                res[-1][1] += qty
            else:
                res.append([price, qty])
    return [tuple(r) for r in res]


def run_vector(v):
    eng = E.Engine(cents(v["ref"]), vi_enable=v.get("vi_enable", True))
    setup, action = messages(v)
    for m in setup:
        out = eng.submit(m)
        assert not any(o[0] == E.REJ for o in out), (v["name"], m, out)
    out = []
    for m in action:
        out += eng.submit(m)
    return eng, out


class OfficialExamples(unittest.TestCase):
    def test_vectors(self):
        for v in VECTORS:
            with self.subTest(v["name"], src=v["src"]):
                eng, out = run_vector(v)
                px = lambda rows: [(cents(p), q) for p, q in rows]
                if "auction" in v:
                    auc = [o for o in out if o[0] == E.AUC][0]
                    self.assertEqual((auc[3], auc[4]), (cents(v["auction"][0]), v["auction"][1]))
                    self.assertEqual(sum(o[4] for o in out if o[0] == E.TRADE), v["auction"][1])
                else:
                    self.assertEqual(merged_trades(out), px(v["trades"]))
                    cancelled = sum(o[4] for o in out if o[0] == E.CXL and o[5] != E.CXL_PURGE)
                    self.assertEqual(cancelled, v.get("cancelled", 0))
                    self.assertEqual(any(o[0] == E.VI for o in out), v.get("vi", False))
                    self.assertEqual(eng.phase, E.VI_HALT if v.get("vi") else E.CONT)
                self.assertEqual(eng.depth(E.BUY), px(v["bids"]))
                self.assertEqual(eng.depth(E.SELL), px(v["asks"]))
                if "mkt" in v:
                    self.assertEqual((eng.market_qty(E.BUY), eng.market_qty(E.SELL)), v["mkt"])

    def test_A1_best_five_disclosure(self):
        """TM-zh: the five best unfilled levels disclosed after the auction of 圖例1.2 (R9)."""
        v = [x for x in VECTORS if x["name"] == "A1"][0]
        eng, _ = run_vector(v)
        md = eng.submit(("S",))
        bids = [(o[3], o[4]) for o in md if o[0] == E.MDL and o[5] < 5]
        asks = [(o[3], o[4]) for o in md if o[0] == E.MDL and 8 <= o[5] < 13]
        self.assertEqual(bids, [(10550, 10), (10450, 50), (10350, 30), (10300, 10), (10250, 10)])
        self.assertEqual(asks, [(10600, 7), (10650, 25), (10700, 10), (10750, 10), (10800, 20)])

    def test_L1_limit_rounding(self):
        """TM-zh 每日有價證券漲跌停價格計算範例: 40.60 -> 44.65 / 36.55 (R3)."""
        self.assertEqual(rules.daily_limits(4060), (3655, 4465))

    def test_L2_L4_next_reference(self):
        """TM-zh 當日開盤競價基準 圖例1-3 (R3.1)."""
        self.assertEqual(rules.next_reference_price(10000, None, 10100, 10200), 10100)
        self.assertEqual(rules.next_reference_price(10000, None, 9600, 9700), 9700)
        self.assertEqual(rules.next_reference_price(10000, None, 9900, 10100), 10000)
        self.assertEqual(rules.daily_limits(10000), (9000, 11000))
        self.assertEqual(rules.daily_limits(10100), (9090, 11100))
        self.assertEqual(rules.daily_limits(9700)[0], 8730)

    def test_L3_known_mismatch_with_source(self):
        """R3.2: the source prints a limit-up of 106.7 for a reference of 97, which is off-tick.

        Recorded as a mismatch with the source, not as a pass: the tick rule (R2) and the
        rounding rule (R3) stated by the same page give 106.50.
        """
        self.assertFalse(rules.is_valid_price(10670))
        self.assertEqual(rules.daily_limits(9700)[1], 10650)

    # -- reference price of the volatility interruption ------------------------------------
    def _open_at_100(self):
        eng = E.Engine(10000)
        eng.submit(("N", 1, E.BUY, 0, E.ROD, 10000, 1))
        eng.submit(("N", 2, E.SELL, 0, E.ROD, 10000, 1))
        eng.submit(("T", E.hms(9, 0)))
        self.assertEqual(eng.open_price, 10000)
        return eng

    def _trade(self, eng, oid, price, qty):
        eng.submit(("N", oid, E.SELL, 0, E.ROD, price, qty))
        return eng.submit(("N", oid + 1, E.BUY, 0, E.ROD, price, qty))

    def test_V1_reference_price_example(self):
        """TM-en: open 100; 100x1, 101x4, 102x5; at 9:05:01 the average is 101.4 and 105 triggers;
        matched by call auction at 9:07:01; an auction price of 104 is the reference until 9:12:01."""
        eng = self._open_at_100()
        for i, (t, price, qty) in enumerate([(E.hms(9, 1), 10000, 1), (E.hms(9, 2), 10100, 4),
                                             (E.hms(9, 3), 10200, 5)]):
            eng.submit(("T", t))
            out = self._trade(eng, 10 + 2 * i, price, qty)
            self.assertEqual(merged_trades(out), [(price, qty)])
        eng.submit(("T", E.hms(9, 5, 1)))
        pq, q = eng._vi_reference()
        self.assertEqual((pq, q), (101400, 10))            # 101.4
        eng.submit(("N", 30, E.SELL, 0, E.ROD, 10500, 1))
        out = eng.submit(("N", 31, E.BUY, 0, E.ROD, 10500, 1))
        self.assertTrue(any(o[0] == E.VI for o in out))
        self.assertEqual(eng.vi_end, E.hms(9, 7, 1))
        eng.submit(("N", 32, E.SELL, 0, E.ROD, 10400, 5))  # makes the auction price 104
        eng.submit(("T", E.hms(9, 7, 0)))
        self.assertEqual(eng.phase, E.VI_HALT)
        out = eng.submit(("T", E.hms(9, 7, 1)))
        self.assertEqual([(o[3], o[4]) for o in out if o[0] == E.AUC], [(10400, 1)])
        self.assertEqual(eng.phase, E.CONT)
        eng.submit(("T", E.hms(9, 12, 1)))
        self.assertEqual(eng._vi_reference(), (10400, 1))
        eng.submit(("T", E.hms(9, 12, 2)))
        self.assertNotEqual(eng.fix_until, eng.now)
        self.assertGreater(eng.now, eng.fix_until)

    def test_V2_first_five_minutes(self):
        """DECK slide 28: open 100; 101 at 9:00:01 trades; 104 at 9:02:02 is beyond 3.5%;
        call auction at 9:04:02."""
        eng = self._open_at_100()
        eng.submit(("T", E.hms(9, 0, 1)))
        self.assertEqual(merged_trades(self._trade(eng, 10, 10100, 1)), [(10100, 1)])
        eng.submit(("T", E.hms(9, 2, 2)))
        self.assertEqual(eng._vi_reference(), (10000, 1))
        out = self._trade(eng, 20, 10400, 1)
        self.assertEqual(merged_trades(out), [])
        self.assertTrue(any(o[0] == E.VI for o in out))
        self.assertEqual(eng.vi_end, E.hms(9, 4, 2))

    def test_V3_rolling_average(self):
        """DECK slide 29: average 100 at 9:05:01, 101 trades; average 100.5 at 9:05:02, 105 is
        beyond 3.5%; call auction at 9:07:02."""
        eng = self._open_at_100()
        eng.submit(("T", E.hms(9, 1)))
        self._trade(eng, 10, 10000, 1)
        eng.submit(("T", E.hms(9, 5, 1)))
        self.assertEqual(eng._vi_reference(), (10000, 1))
        self.assertEqual(merged_trades(self._trade(eng, 20, 10100, 1)), [(10100, 1)])
        eng.submit(("T", E.hms(9, 5, 2)))
        pq, q = eng._vi_reference()
        self.assertEqual(pq * 10, 1005 * 100 * q)          # 100.5
        out = self._trade(eng, 30, 10500, 1)
        self.assertTrue(any(o[0] == E.VI for o in out))
        self.assertEqual(eng.vi_end, E.hms(9, 7, 2))


if __name__ == "__main__":
    unittest.main()
