"""Rules that TWSE's material states but gives no worked example for (docs/RULES.md, end)."""
import unittest

from model import engine as E
from model import rules

B, S = E.BUY, E.SELL


def new(oid, side, price, qty, tif=E.ROD, mkt=0):
    return ("N", oid, side, mkt, tif, price, qty)


def types(out):
    return [o[0] for o in out]


class PriceGrid(unittest.TestCase):
    def test_tick_table(self):                                  # R2
        for price, tick in [(1, 1), (999, 1), (1000, 5), (4995, 5), (5000, 10), (9990, 10), (10000, 50),
                            (49950, 50), (50000, 100), (99900, 100), (100000, 500), (1234500, 500)]:
            self.assertEqual(rules.tick_of(price), tick)
        self.assertFalse(rules.is_valid_price(1001))
        self.assertTrue(rules.is_valid_price(1005))

    def test_limits_are_inside_ten_percent_and_tight(self):     # R3, every reference up to TWD 1500
        p = 10
        while p <= 150000:
            dn, up = rules.daily_limits(p)
            self.assertTrue(rules.is_valid_price(dn) and rules.is_valid_price(up))
            self.assertLessEqual(up * 10, p * 11)
            self.assertGreaterEqual(dn * 10, p * 9)
            self.assertGreater((up + rules.tick_of(up)) * 10, p * 11)       # the next tick would be too far
            self.assertLess((dn - rules.tick_of(dn - 1)) * 10, p * 9)
            p += rules.tick_of(p)

    def test_minimum_tick_clause(self):                         # R3 (OR Art. 63 proviso)
        self.assertEqual(rules.daily_limits(5), (4, 6))         # 10% of 0.05 is less than a tick
        self.assertEqual(rules.daily_limits(1), (1, 2))         # never below one cent
        self.assertEqual(rules.daily_limits(10), (9, 11))

    def test_level_count_bound(self):
        """At most 182 price levels fit inside the limits for any reference below TWD 1000,
        and 256 levels cover every reference up to TWD 6395 (255 levels; 6400 needs 257) (docs/DESIGN.md)."""
        most, p = 0, 1
        while p < 100000:
            most = max(most, len(rules.price_levels(p)))
            p += rules.tick_of(p)
        self.assertEqual(most, 182)
        self.assertEqual(len(rules.price_levels(639500)), 255)
        self.assertEqual(len(rules.price_levels(640000)), 257)
        self.assertTrue(E.Engine(640000).cfg_error)

    def test_one_band_boundary_at_most(self):
        """A +-10% range never holds two tick-band boundaries, so two tick sizes describe a day."""
        p = 1
        while p < 200000:
            lv = rules.price_levels(p)
            self.assertLessEqual(len({rules.tick_of(x) for x in lv}), 2)
            p += rules.tick_of(p)


class Session(unittest.TestCase):
    def setUp(self):
        self.e = E.Engine(10000)

    def test_order_types_by_phase(self):                        # R4
        e = self.e
        self.assertEqual(e.submit(new(1, B, 10000, 1, mkt=1))[0][5], E.REJ_TYPE)
        self.assertEqual(e.submit(new(1, B, 10000, 1, tif=E.IOC))[0][5], E.REJ_TYPE)
        self.assertEqual(e.submit(new(1, B, 10000, 1, tif=E.FOK))[0][5], E.REJ_TYPE)
        self.assertEqual(types(e.submit(new(1, B, 10000, 1))), [E.ACK])
        e.submit(("T", E.hms(9, 0)))
        for i, (tif, mkt) in enumerate([(E.ROD, 0), (E.IOC, 0), (E.FOK, 0), (E.ROD, 1), (E.IOC, 1), (E.FOK, 1)]):
            self.assertEqual(e.submit(new(10 + i, S, 10500, 1, tif=tif, mkt=mkt))[0][0], E.ACK)
        e.submit(("T", E.hms(13, 25)))
        self.assertEqual(e.submit(new(30, B, 10000, 1, mkt=1))[0][5], E.REJ_TYPE)
        e.submit(("T", E.hms(13, 30)))
        self.assertEqual(e.submit(new(31, B, 10000, 1))[0][5], E.REJ_CLOSED)

    def test_price_checks(self):                                # R2, R3
        e = self.e
        self.assertEqual(e.submit(new(1, B, 11050, 1))[0][5], E.REJ_LIMIT)
        self.assertEqual(e.submit(new(1, B, 8950, 1))[0][5], E.REJ_LIMIT)
        self.assertEqual(e.submit(new(1, B, 10010, 1))[0][5], E.REJ_TICK)
        self.assertEqual(e.submit(new(1, B, 9990, 1))[0][0], E.ACK)      # tick is 0.10 below 100
        self.assertEqual(e.submit(new(2, B, 11000, 1))[0][0], E.ACK)

    def test_reduce_keeps_priority_reprice_loses_it(self):      # R4.1, R5
        e = self.e
        e.submit(("T", E.hms(9, 0)))
        e.submit(new(1, B, 10000, 5))
        e.submit(new(2, B, 10000, 5))
        self.assertEqual(e.submit(("D", 1, 3)), [(E.RED, 1, 0, 0, 2, 0)])
        out = e.submit(new(3, S, 10000, 3))
        self.assertEqual([(o[1], o[4]) for o in out if o[0] == E.TRADE], [(1, 2), (2, 1)])
        # a price change is cancel + new: order 2 goes behind order 4
        e.submit(new(4, B, 10000, 1))
        e.submit(("C", 2))
        e.submit(new(2, B, 10000, 4))
        out = e.submit(new(5, S, 10000, 2))
        self.assertEqual([(o[1], o[4]) for o in out if o[0] == E.TRADE], [(4, 1), (2, 1)])

    def test_closing_auction_and_closing_price(self):           # R1, R6, R6.2
        e = self.e
        e.submit(("T", E.hms(9, 0)))
        e.submit(new(1, S, 10100, 2))
        e.submit(new(2, B, 10100, 1))                           # last trade 101
        e.submit(new(3, S, 10000, 1, mkt=0))
        e.submit(("C", 3))
        e.submit(new(4, B, 9900, 1, mkt=1))                     # market buy takes the 101 that is left
        e.submit(new(5, S, 0, 1, mkt=1))                        # nothing to hit: rests
        self.assertEqual(e.market_qty(S), 1)
        out = e.submit(("T", E.hms(13, 25)))
        self.assertEqual([(o[0], o[1], o[5]) for o in out], [(E.CXL, 5, E.CXL_PURGE), (E.PHASE, 0, E.CLOSE_CALL)])
        e.submit(new(6, B, 10300, 4))
        e.submit(new(7, S, 10200, 3))
        md = e.submit(("S",))
        self.assertEqual(md[-1][3:5], (10300, 3))               # 4 lots bid above 102: only 103 fills them all
        self.assertEqual([(o[3], o[4]) for o in md if o[0] == E.MDL], [(10300, 1)])
        out = e.submit(("T", E.hms(13, 30)))
        self.assertEqual([o[3:6] for o in out if o[0] == E.AUC], [(10300, 3, E.AUC_CLOSE)])
        self.assertEqual(e.phase, E.CLOSED)
        self.assertEqual(e.last, 10300)

    def test_tie_break_nearest_to_last_trade(self):             # R6 principle 3
        for last, want in [(9500, 9800), (10000, 10000), (10600, 10200)]:
            e = E.Engine(10000)
            e.submit(new(1, B, last, 1))
            e.submit(new(2, S, last, 1))
            e.submit(("T", E.hms(9, 0)))                        # opens at `last`
            e.submit(("T", E.hms(13, 25)))
            e.submit(new(5, B, 10200, 7))
            e.submit(new(6, S, 9800, 7))                        # every tick 98..102 trades 7
            self.assertEqual(e.auction_price(), (want, 7))

    def test_no_trade_in_window_falls_back(self):               # R8 para. 7(2)
        e = E.Engine(10000)
        e.submit(("T", E.hms(9, 0)))                            # no opening trade
        self.assertEqual(e._vi_reference(), (10000, 1))         # reference price
        e.submit(("T", E.hms(9, 10)))
        self.assertEqual(e._vi_reference(), (10000, 1))
        e.submit(new(1, S, 10200, 1))
        e.submit(new(2, B, 10200, 1))
        e.submit(("T", E.hms(9, 20)))                           # that trade left the window
        self.assertEqual(e._vi_reference(), (10200, 1))         # most recent trade price

    def test_exactly_at_the_band_does_not_trigger(self):        # R8.1
        e = E.Engine(10000)
        e.submit(new(1, B, 10000, 1))
        e.submit(new(2, S, 10000, 1))
        e.submit(("T", E.hms(9, 0)))
        e.submit(new(3, S, 10350, 1))
        e.submit(new(4, S, 10400, 1))
        self.assertEqual(types(e.submit(new(5, B, 10350, 1))), [E.ACK, E.TRADE])
        self.assertIn(E.VI, types(e.submit(new(6, B, 10400, 1))))

    def test_no_interruption_below_one_dollar(self):            # R8 exception
        e = E.Engine(50)
        e.submit(("T", E.hms(9, 0)))
        e.submit(new(1, S, 55, 1))
        self.assertEqual(types(e.submit(new(2, B, 55, 1))), [E.ACK, E.TRADE])

    def test_interruption_late_in_the_day_joins_the_close(self):    # R8.1
        e = E.Engine(10000)
        e.submit(("T", E.hms(13, 24)))
        e.submit(new(1, S, 10500, 1))
        self.assertIn(E.VI, types(e.submit(new(2, B, 10500, 1))))
        out = e.submit(("T", E.hms(13, 27)))
        self.assertEqual([(o[0], o[5]) for o in out], [(E.PHASE, E.CLOSE_CALL)])
        out = e.submit(("T", E.hms(13, 30)))
        self.assertEqual([o[3:6] for o in out if o[0] == E.AUC], [(10500, 1, E.AUC_CLOSE)])


if __name__ == "__main__":
    unittest.main()
