#!/usr/bin/env python3
"""Directed boundary scenarios, written from the rule text (no TWSE worked example exists
for these).  Each is one trading day; the RTL must match the golden model on all of them.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from model.engine import BUY as B, SELL as S, ROD, IOC, FOK, hms  # noqa: E402


def N(oid, side, price, qty, tif=ROD, mkt=0):
    return ("N", oid, side, mkt, tif, price, qty)


def trade(oid, price, qty):
    return [N(oid, S, price, qty), N(oid + 1, B, price, qty)]


def open_at(price):
    return [N(1, B, price, 1), N(2, S, price, 1), ("T", hms(9, 0))]


SCENARIOS = []


def scenario(name, ref, msgs):
    SCENARIOS.append((name, ref, msgs))


# R8.1: the fixed reference still applies at exactly open + 300 s, the average one second later
for at in (hms(9, 5, 0), hms(9, 5, 1)):
    scenario("fixed reference boundary %d" % at, 10000,
             open_at(10000) + [("T", hms(9, 4))] + trade(10, 10300, 50)
             + [("T", at), N(20, S, 10400, 1), N(21, B, 10400, 1), ("S",)])
# R8.1: fixed reference after an interruption lasts until halt end + 300 s inclusive
for at in (hms(9, 8, 0), hms(9, 8, 1)):
    scenario("post-interruption reference boundary %d" % at, 10000,
             open_at(10000) + [("T", hms(9, 1)), N(10, S, 10400, 2), N(11, B, 10400, 1), N(12, S, 10400, 1),
                               ("T", hms(9, 3)), ("C", 10), ("C", 12)] + trade(20, 10400, 1) + trade(30, 10700, 40)
             + [("T", at), N(40, S, 10900, 1), N(41, B, 10900, 1), ("S",)])
# R8.1: an interruption ending exactly at 13:25 joins the closing call; one second earlier it is matched first
for at in (hms(13, 23, 0), hms(13, 22, 59)):
    scenario("interruption ending at %d" % (at + 120), 10000,
             open_at(10000) + [("T", at), N(10, S, 10500, 3), N(11, B, 10500, 2),
                               ("T", hms(13, 24, 58)), ("S",), ("T", hms(13, 24, 59)), ("S",), ("T", hms(13, 25, 0)),
                               N(12, B, 10500, 5, IOC), N(13, B, 10500, 2), ("S",), ("T", hms(13, 30, 0)), ("S",)])
# R8: exactly 3.5% away trades, one tick more does not; both directions
scenario("band edges", 10000,
         open_at(10000) + [N(10, S, 10350, 1), N(11, B, 10350, 1), N(12, B, 9650, 1), N(13, S, 9650, 1),
                           N(14, B, 9600, 1), N(15, S, 9600, 1), ("S",)])
# R4/R8: FOK that could fill only by reaching a price beyond the band; FOK for exactly what is there
scenario("fok exact and beyond", 10000,
         open_at(10000) + [N(10, S, 10100, 2), N(11, S, 10200, 3), N(12, S, 10400, 4),
                           N(13, B, 10400, 6, FOK), N(14, B, 10200, 5, FOK), N(15, S, 10400, 9, FOK), ("S",)])
# R7.1: resting market orders on both ends of the day; withdrawn at the closing call
scenario("market orders rest and are withdrawn", 5000,
         open_at(5000) + [N(10, B, 0, 3, mkt=1), N(11, B, 0, 2, mkt=1), ("D", 10, 1), ("S",),
                          N(12, S, 5100, 3), ("S",), N(13, S, 0, 4, mkt=1), ("C", 11), ("S",),
                          N(14, S, 0, 5, mkt=1), N(15, S, 0, 1, mkt=1), ("C", 14), ("T", hms(13, 25)), ("S",)])
# time: a single TIME message that crosses every phase of the day at once
scenario("one message from pre-open to close", 2000,
         [N(1, B, 2100, 5), N(2, S, 1900, 3), N(3, S, 2050, 4), ("S",), ("T", hms(14, 0)), ("S",), N(4, B, 2000, 1)])
# R3: limit prices themselves trade; one tick outside is rejected; tick-band boundary inside the range
scenario("limits and band boundary", 1000,
         [N(1, B, 1100, 1), N(2, S, 900, 1), N(3, B, 1105, 1), N(4, S, 899, 1), N(5, B, 1001, 1), N(6, B, 1005, 2),
          N(7, S, 999, 2), ("S",), ("T", hms(9, 0)), ("S",)])


def main():
    for name, ref, msgs in SCENARIOS:
        print("R %d 1" % ref)
        for m in msgs:
            print(" ".join(str(x) for x in m))


if __name__ == "__main__":
    main()
