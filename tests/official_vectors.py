"""Worked examples from TWSE's own material, transcribed as test vectors.

Sources are abbreviated as in docs/RULES.md (TM-zh, TM-en, DECK, IMG).  Prices
are written in TWD as in the source and converted to cents.  Each vector is
one trading session:

    ref      auction reference price of the day
    last     if set, the session opens with a 1-lot opening auction at this
             price, which makes it "the most recent trade price" and the
             volatility reference (R8 para. 7(1)) for the first five minutes
    book     resting orders entered just after the open, in time order:
             (side, price or "MKT", qty)
    preopen  orders entered before the open instead (call-auction examples)
    order    the order the example is about: (side, price or "MKT", tif, qty)
    trades   expected fills as (price, qty), consecutive fills at one price merged
    bids/asks  expected book after, best first; mkt = resting market qty (buy, sell)
    cancelled  quantity of `order` cancelled by the system
    vi       True if the example says the interruption starts
    vi_enable  False for the one slide that leaves the interruption out of the picture
"""
B, S = 0, 1
ROD, IOC, FOK = 0, 1, 2
MKT = "MKT"

VECTORS = [
    # ---------------------------------------------------------------- call auction (R6)
    dict(name="A1", src="TM-zh 圖例1.1/1.2 (IMG trading-1.jpg, trading-2.jpg)", ref=104.0,
         # The cumulative columns of 圖例1.1 imply 100 lots of buys above 108.00 and 20 lots of
         # sells below 102.50 that the figure does not list; they are placed one tick outside.
         preopen=[(B, 108.5, 100), (B, 108.0, 10), (B, 107.5, 20), (B, 107.0, 10), (B, 106.5, 20), (B, 106.0, 25),
                  (B, 105.5, 10), (B, 104.5, 50), (B, 103.5, 30), (B, 103.0, 10), (B, 102.5, 10),
                  (S, 102.0, 20), (S, 103.0, 10), (S, 103.5, 20), (S, 104.0, 10), (S, 104.5, 50), (S, 105.0, 40),
                  (S, 105.5, 22), (S, 106.0, 20), (S, 106.5, 25), (S, 107.0, 10), (S, 107.5, 10), (S, 108.0, 20)],
         auction=(106.0, 185),
         bids=[(105.5, 10), (104.5, 50), (103.5, 30), (103.0, 10), (102.5, 10)],
         asks=[(106.0, 7), (106.5, 25), (107.0, 10), (107.5, 10), (108.0, 20)]),
    dict(name="A2", src="DECK slide 7 成交價決定-集合競價", ref=100.0,
         preopen=[(B, 102, 50), (B, 99, 10), (B, 98, 20), (B, 97, 30),
                  (S, 100, 10), (S, 101, 20), (S, 102, 30), (S, 103, 40)],
         auction=(102.0, 50),
         bids=[(99, 10), (98, 20), (97, 30)], asks=[(102, 10), (103, 40)]),
    # ---------------------------------------------------------------- continuous (R7)
    dict(name="C1", src="TM-zh 圖例3.1 (IMG trading-11.jpg)", ref=103.5,
         book=[(S, 103.0, 10), (S, 103.5, 20), (S, 104.0, 30), (S, 104.5, 40), (S, 105.0, 50)],
         order=(B, 104.0, ROD, 50), trades=[(103.0, 10), (103.5, 20), (104.0, 20)],
         bids=[], asks=[(104.0, 10), (104.5, 40), (105.0, 50)]),
    dict(name="C2", src="TM-zh 圖例3.2 (IMG trading-12.jpg)", ref=103.0,
         # the figure prints 101.50 twice in the price column; the lower row is taken as 101.00
         book=[(B, 103.0, 10), (B, 102.5, 20), (B, 102.0, 30), (B, 101.5, 40), (B, 101.0, 50),
               (S, 103.5, 10), (S, 104.0, 30), (S, 104.5, 30), (S, 105.0, 40)],
         order=(S, 102.0, ROD, 50), trades=[(103.0, 10), (102.5, 20), (102.0, 20)],
         bids=[(102.0, 10), (101.5, 40), (101.0, 50)], asks=[(103.5, 10), (104.0, 30), (104.5, 30), (105.0, 40)]),
    dict(name="C3", src="DECK slide 8 成交價決定-逐筆交易", ref=100.0,
         book=[(B, 99, 10), (B, 98, 20), (B, 97, 30), (S, 100, 10), (S, 101, 20), (S, 102, 30), (S, 103, 40)],
         order=(B, 102, ROD, 50), trades=[(100, 10), (101, 20), (102, 20)],
         bids=[(99, 10), (98, 20), (97, 30)], asks=[(102, 10), (103, 40)]),
    # ---------------------------------------------------------------- ROD / IOC / FOK (R4)
    dict(name="O1", src="DECK slide 15 委託比較 (ROD)", ref=101.0, book=[(B, 102, 1), (B, 101, 2)],
         order=(S, 100, ROD, 5), trades=[(102, 1), (101, 2)], bids=[], asks=[(100, 2)]),
    dict(name="O2", src="DECK slide 15 委託比較 (IOC)", ref=101.0, book=[(B, 102, 1), (B, 101, 2)],
         order=(S, 100, IOC, 5), trades=[(102, 1), (101, 2)], bids=[], asks=[], cancelled=2),
    dict(name="O3", src="DECK slide 15 委託比較 (FOK)", ref=101.0, book=[(B, 102, 1), (B, 101, 2)],
         order=(S, 100, FOK, 5), trades=[], bids=[(102, 1), (101, 2)], asks=[], cancelled=5),
    dict(name="O4", src="TM-en IOC example (IMG continuous-trading-3-en.png)", ref=101.0,
         book=[(S, 100, 10), (S, 101, 20)],
         order=(B, 101, IOC, 50), trades=[(100, 10), (101, 20)], bids=[], asks=[], cancelled=20),
    dict(name="O5", src="TM-en FOK example (IMG continuous-trading-4-en.png)", ref=101.0,
         book=[(S, 100, 10), (S, 101, 20)],
         order=(B, 101, FOK, 50), trades=[], bids=[], asks=[(100, 10), (101, 20)], cancelled=50),
    # ---------------------------------------------------------------- market orders (R7.1)
    dict(name="M1", src="TM-en market order example (IMG continuous-trading-1/2-en.png)", ref=100.0, last=100,
         book=[(S, MKT, 3), (S, 99, 1), (S, 100, 1)],
         order=(B, MKT, ROD, 3), trades=[(99, 3)], bids=[], asks=[(99, 1), (100, 1)], mkt=(0, 0)),
    dict(name="M2", src="DECK slide 17 市價委託(2/8) 新進市價 VS 限價", ref=100.0, last=100,
         book=[(S, 103, 1), (S, 102, 1), (B, 101, 5), (B, 100, 2), (B, 99, 1)],
         order=(S, MKT, ROD, 10), trades=[(101, 5), (100, 2), (99, 1)],
         bids=[], asks=[(102, 1), (103, 1)], mkt=(0, 2)),
    dict(name="M3", src="DECK slide 18 市價委託(3/8) 新進市價 VS 市價", ref=100.0, last=100,
         book=[(B, MKT, 1)],
         order=(S, MKT, ROD, 1), trades=[(100, 1)], bids=[], asks=[], mkt=(0, 0)),
    dict(name="M4", src="DECK slide 19 市價委託(4/8) 上漲趨勢", ref=100.0, last=107,
         book=[(B, MKT, 1), (B, 110, 5), (B, 109, 5)],
         order=(S, MKT, ROD, 3), trades=[(110, 3)], bids=[(110, 3), (109, 5)], asks=[], mkt=(0, 0)),
    dict(name="M5", src="DECK slide 20 市價委託(5/8) 下跌趨勢", ref=100.0, last=93,
         book=[(B, MKT, 1), (B, 91, 2), (B, 90, 3)],
         order=(S, MKT, ROD, 3), trades=[(93, 1), (91, 2)], bids=[(90, 3)], asks=[], mkt=(0, 0)),
    dict(name="M6", src="DECK slide 21 市價委託(6/8) 新進限價 VS 市價與限價", ref=100.0, last=97,
         book=[(B, MKT, 1), (B, 95, 1), (B, 94, 1), (B, 90, 1)],
         order=(S, 90, ROD, 3), trades=[(97, 1), (95, 1), (94, 1)], bids=[(90, 1)], asks=[], mkt=(0, 0)),
    dict(name="M7", src="DECK slide 22 市價委託(7/8) 無最近一次成交價，開盤競價基準104元", ref=104.0, vi_enable=False,
         # The slide trades at 109 and 110 against a reference of 104; it illustrates the price
         # conversion only and leaves the volatility interruption out, so the vector does too.
         book=[(S, MKT, 1), (S, 110, 1), (S, 109, 1), (S, 105, 1)],
         order=(B, MKT, ROD, 5), trades=[(104, 1), (105, 1), (109, 1), (110, 1)],
         bids=[], asks=[], mkt=(1, 0)),
    # ---------------------------------------------------------------- volatility interruption (R8)
    # DECK slides 31-40 and TM-en figures 5-12: reference 100, band 96.5 .. 103.5
    dict(name="V4", src="DECK slide 31 範例1-1 一般限價; TM-en IMG continuous-trading-5/6-en.png", ref=100.0, last=100,
         book=[(S, 101, 1), (S, 102, 2), (S, 104, 3)],
         order=(B, 105, ROD, 10), trades=[(101, 1), (102, 2)], vi=True, bids=[(105, 7)], asks=[(104, 3)]),
    dict(name="V5", src="DECK slide 32 範例1-2 一般限價", ref=100.0, last=100,
         book=[(S, 95, 1), (S, 96, 2), (S, 97, 3)],
         order=(B, 98, ROD, 10), trades=[], vi=True, bids=[(98, 10)], asks=[(95, 1), (96, 2), (97, 3)]),
    dict(name="V6", src="DECK slide 33 範例2-1 FOK; TM-en IMG continuous-trading-12-en.png", ref=100.0, last=100,
         book=[(S, 101, 1), (S, 102, 2), (S, 104, 3)],
         order=(B, 105, FOK, 6), trades=[], cancelled=6, bids=[], asks=[(101, 1), (102, 2), (104, 3)]),
    dict(name="V6b", src="TM-en FOK example, volume 5 (IMG continuous-trading-12-en.png)", ref=100.0, last=100,
         book=[(S, 101, 1), (S, 102, 2), (S, 104, 3)],
         order=(B, 105, FOK, 5), trades=[], cancelled=5, bids=[], asks=[(101, 1), (102, 2), (104, 3)]),
    dict(name="V7", src="DECK slide 34 範例2-2 FOK", ref=100.0, last=100,
         book=[(S, 95, 1), (S, 96, 2), (S, 97, 2), (S, 98, 3)],
         order=(B, 99, FOK, 6), trades=[], cancelled=6, bids=[], asks=[(95, 1), (96, 2), (97, 2), (98, 3)]),
    dict(name="V8", src="DECK slide 35 範例3-1 IOC; TM-en IMG continuous-trading-9/10-en.png", ref=100.0, last=100,
         book=[(S, 101, 1), (S, 102, 2), (S, 104, 3)],
         order=(B, 105, IOC, 10), trades=[(101, 1), (102, 2)], cancelled=7, bids=[], asks=[(104, 3)]),
    dict(name="V9", src="DECK slide 36 範例3-2 IOC", ref=100.0, last=100,
         book=[(S, 95, 1), (S, 96, 2), (S, 97, 5), (S, 98, 5)],
         order=(B, 99, IOC, 10), trades=[], cancelled=10, bids=[], asks=[(95, 1), (96, 2), (97, 5), (98, 5)]),
    dict(name="V10", src="DECK slide 37 範例4-1 市價; TM-en IMG continuous-trading-7/8-en.png", ref=100.0, last=100,
         book=[(S, 101, 1), (S, 102, 2), (S, 104, 3)],
         # the slide draws an incoming quantity of 6 and says 3 are cancelled; TM-en uses 10 and 7
         order=(B, MKT, ROD, 6), trades=[(101, 1), (102, 2)], cancelled=3, bids=[], asks=[(104, 3)], mkt=(0, 0)),
    dict(name="V11", src="DECK slide 38 範例4-2 市價", ref=100.0, last=100,
         book=[(S, 95, 1), (S, 96, 1), (S, 97, 1), (S, 98, 1)],
         order=(B, MKT, ROD, 6), trades=[], cancelled=6, bids=[],
         asks=[(95, 1), (96, 1), (97, 1), (98, 1)], mkt=(0, 0)),
    dict(name="V12", src="DECK slide 39 範例4-3 市價 (resting market sell converted to 96)", ref=100.0, last=100,
         book=[(S, MKT, 1), (S, 96, 1), (S, 97, 1), (S, 98, 1)],
         order=(B, MKT, ROD, 6), trades=[], cancelled=6, bids=[], asks=[(96, 1), (97, 1), (98, 1)], mkt=(0, 1)),
    dict(name="V13", src="DECK slide 40 範例4-4 (resting market sell deleted, broker notified)", ref=100.0, last=100,
         book=[(S, MKT, 1), (S, 96, 1), (S, 97, 1), (S, 98, 1)],
         order=(B, 99, ROD, 6), trades=[], vi=True, bids=[(99, 6)], asks=[(96, 1), (97, 1), (98, 1)], mkt=(0, 0)),
]


def cents(x):
    return int(round(x * 100))


def messages(v):
    """The vector as (messages before the example's order, the order's messages)."""
    T_OPEN = 9 * 3600
    nid = [0]

    def new(side, price, qty, tif=ROD):
        nid[0] += 1
        mkt = price == MKT
        return ("N", nid[0], side, 1 if mkt else 0, tif, 0 if mkt else cents(price), qty)

    setup = []
    if v.get("last"):
        setup += [new(B, v["last"], 1), new(S, v["last"], 1)]
    for side, price, qty in v.get("preopen", []):
        setup.append(new(side, price, qty))
    if "auction" in v:
        return setup, [("T", T_OPEN)]
    setup.append(("T", T_OPEN))
    for side, price, qty in v.get("book", []):
        setup.append(new(side, price, qty))
    side, price, tif, qty = v["order"]
    return setup, [new(side, price, qty, tif)]
