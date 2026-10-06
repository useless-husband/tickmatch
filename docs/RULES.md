# The rulebook this engine implements

Every rule below was taken from Taiwan Stock Exchange (TWSE) material fetched on **2026-10-06** and is quoted or
paraphrased next to its source. Nothing here was written from memory. Where the sources are silent or contradict each
other, the section says so, states the interpretation used, and names the parameter that carries it.

This is an educational model of published rules. It is not affiliated with or endorsed by TWSE and is not a certified
implementation; the sources below are authoritative, not this file.

## Sources

| Tag | Document | URL |
| --- | --- | --- |
| OR | Operating Rules of the Taiwan Stock Exchange Corporation (English text "amended on 2025.10.20"; Chinese text 修正日期 115.09.24, which amends Article 76 only) | https://twse-regulation.twse.com.tw/ENG/EN/law/DAT0201.aspx?FLCODE=FL007304 and https://twse-regulation.twse.com.tw/TW/law/DAT0201.aspx?FLCODE=FL007304 |
| TM-en | "Trading Mechanism Introduction", section 1 Regular Trading | https://www.twse.com.tw/en/products/system/trading.html |
| TM-zh | 集中市場交易制度介紹, 普通交易 | https://www.twse.com.tw/zh/products/system/trading.html |
| CT | 盤中全面逐筆交易專區 | https://accessibility.twse.com.tw/zh/products/system/continuous-trading.html |
| DECK | 108.04.19 逐筆交易種子講師課程簡報-全面逐筆交易規劃內容(交易部), 55 slides | https://www.twse.com.tw/downloads/zh/trading/information/info13promo10.pdf |
| IMG | Worked-example figures embedded in TM-zh / TM-en | `https://www.twse.com.tw/res/img/products/system/trading-{1,2,11,12}.jpg`, `.../continuous-trading-{1..12}-en.png` |

Slide numbers are the ones printed on the slides (PDF page minus one). DECK carries its own disclaimer (slide 1): it was teaching material for the 2020 launch and "若有錯誤，以臺灣證券交易所及主管
機關之法規及公告為準". Where DECK and the current web pages differ (R8.1), the web pages and OR win.

Scope: **ordinary board-lot trading of a listed stock that has a daily price limit.** Out of scope, and not modelled:
odd lots (intraday and after-hours), after-hours fixed-price trading, block trades, auctions/tenders, warrants, ETFs,
bonds and convertible bonds (they have their own tick tables and limit formulas), newly listed stocks in their first
five no-limit days, disposition / altered-trading-method securities (periodic call auction), short-sale price
restrictions, margin and settlement, broker-side order quotas.

---

## R1 Sessions

TM-en, "Matching methods on regular trading period":

| Period | Matching |
| --- | --- |
| Opening session 8:30 - 9:00 (or first matching after 9:02 in case of postponement) | Call auction |
| Intraday trading session 9:00 - 13:25 | Continuous trading |
| Intraday Volatility Interruption | Call auction |
| 13:25 - 13:30 (or 13:33 in case of postponement of market close) | Call auction |

OR Art. 58-3 ¶1: "The first matching in the current session shall be done by call auction, and subsequent matching
shall be done by continuous trading until a period of time before market closing, during which all the trading quotes
shall be accumulated and matched by call auction." OR Art. 58 ¶2: orders "may be keyed-in 30 minutes prior to the
opening". OR Art. 58 ¶1: an order is valid only on the day it is placed.

Engine: phases `PRE_OPEN → CONT ⇄ VI → CLOSE_CALL → CLOSED`, driven by `TIME` messages with one-second resolution.
Parameters `T_ACCEPT=08:30:00`, `T_OPEN=09:00:00`, `T_CCALL=13:25:00`, `T_CLOSE=13:30:00`.

**R1.1 Not modelled: postponed open / postponed close.** OR Art. 58-3 ¶8 and TM-zh (暫緩開盤 / 暫緩收盤配套措施) postpone
the opening or closing match by two minutes when a simulated match price in the last minute moves more than 3.5% from
the previous simulated price (or, for the open, when cancels/changes in the last minute reach 30% of pre-open orders).
The engine always opens at `T_OPEN` and closes at `T_CLOSE`. This is a stated limitation.

## R2 Trading unit, price unit, tick sizes

OR Art. 60: "The trading unit of stocks shall be 1,000 shares." Art. 61: prices are per share. Quantities in the
engine are whole board lots (張).

OR Art. 62 ¶1 (Chinese text): "股票每股市價未滿十元者為一分，十元至未滿五十元者為五分，五十元至未滿一百元者為一角，一百元至未滿
五百元者為五角，五百元至未滿一千元者為一元，一千元以上者為五元。" The equity column of the tick table on TM-en agrees:

| Price P (TWD) | Tick |
| --- | --- |
| P < 10 | 0.01 |
| 10 ≤ P < 50 | 0.05 |
| 50 ≤ P < 100 | 0.10 |
| 100 ≤ P < 500 | 0.50 |
| 500 ≤ P < 1000 | 1.00 |
| P ≥ 1000 | 5.00 |

(The English translation of Art. 62 reads "1 dollar if the price is from 500 dollars to less than 100 dollars"; the
Chinese text and the table show this is a typo for 1,000.)

Engine: prices are integers in cents (0.01 TWD). A price is valid when it is a multiple of the tick of its own band.

## R3 Daily price limits and how they are rounded

OR Art. 63 ¶1: "The daily price fluctuation limits of securities ... shall be 10 percent above and below the auction
reference price at market opening of the current trading session for stocks ...; provided, however, that if the price
fluctuation limit is less than the minimum tick size, the minimum tick size shall be the price fluctuation limit, and
the price may not fall lower than the minimum tick size." OR Art. 58 ¶4: order prices must be within the limit.

Rounding — TM-zh, 每日有價證券漲跌停價格計算範例: reference 40.60 → 40.60 × 110% = 44.66, 40.60 × 90% = 36.54; with a 0.05
tick the candidates are 44.65/44.70 and 36.50/36.55; "若分別選擇44.7元及36.5元，將超逾升降幅度10％限制 ... 該股票當日漲停價格
應為44.65元，跌停價格應為36.55元". So: **limit-up = largest valid tick ≤ ref × 1.10, limit-down = smallest valid tick
≥ ref × 0.90**, the tick being that of the band the result falls in.

Engine: `LIMIT_PCT=10`. Minimum-tick clause: if no tick fits inside 10% (reference below 0.10 TWD), the limit is one
tick away, and the limit-down never goes below 0.01.

**R3.1 The auction reference price** (開盤競價基準) is an input to the engine. OR Art. 58-3 ¶4: normally the previous
day's closing price; if there was none, the previous day's highest buy at close if above that day's reference, else
the lowest sell at close if below it, else the previous reference. The golden model has this as a helper
(`next_reference_price`) checked against the three TM-zh examples; the RTL takes the reference price as given.

**R3.2 A discrepancy in the source.** TM-zh example 圖例2 states "第二日漲停價106.7元(97元x 1.1 =106.7元)". 106.70 is not a
valid price under R2 (tick 0.50 between 100 and 500); R2 + R3 give 106.50. The other five limit prices in those three
examples (111, 90.9, 87.3, 110, 90) agree with R2 + R3. The test suite records this as a known mismatch with the
source rather than as a pass.

## R4 Order types and when they are accepted

OR Art. 58-8 ¶1–2: an order is a limit order or a market order; its validity is "valid on the current day" (ROD),
"canceled if not immediately satisfied" (IOC: the unfilled remainder is cancelled) or "canceled if not all order is
immediately satisfied" (FOK: cancelled entirely unless fully filled).

OR Art. 58-8 ¶3: "A trading order that is a market order, or [IOC or FOK] may only be entered during the period allowed
for continuous trading under Article 58-3. During the period for call auction, the TWSE withdraws trading orders
previously entered as market orders that are valid on the current day."

TM-en table of the six combinations:

| Price | Duration | Applicable session |
| --- | --- | --- |
| Limit | ROD | Opening and closing session, continuous trading session, during intra-day volatility interruption |
| Limit | IOC | Continuous trading session |
| Limit | FOK | Continuous trading session |
| Market | ROD | Continuous trading session |
| Market | IOC | Continuous trading session |
| Market | FOK | Continuous trading session |

TM-zh: in the opening and closing sessions "使用市價單、IOC及FOK委託將會被退單". TM-en: "When the intraday volatility
interruption takes place or during the closing session, all market orders remaining in the order book will be
automatically deleted by the TWSE and securities firms will be notified of the deletion."

Engine: anything but limit-ROD is rejected outside `CONT`; resting market orders are cancelled (with a notice per order)
when a volatility interruption starts and when the closing call starts.

**R4.1 Changing an order.** OR Art. 58 ¶6: changes are made by cancelling and re-entering, except "Reduction of the
volume in the order" and "Change of the price of a limit order, in which case the time of the order shall be the time
of entry of the updated order". TM-en adds that the price change "basically combines two actions, 'cancel an order' and
'place a new order', into one". Engine: `CANCEL` and `REDUCE` (keeps its place in the queue) are messages; a price
change is sent as `CANCEL` + `NEW`, which is what the rule says it is. A `REDUCE` by the whole remaining quantity or
more removes the order (a design choice, not a TWSE rule).

## R5 Priority

OR Art. 58-2: "higher-priced buy orders shall have priority over lower-priced buy orders. Lower-priced sell orders
shall have priority over higher-priced sell orders. Where orders are placed at the same price, priority shall be based
on time priority principle. ... For orders placed prior to the opening of the market, priority shall be determined
randomly based on computer arrangement. For orders placed after the opening of the market, priority shall be made on
the order in which the orders were placed." TM-zh adds "市價申報優先於限價申報" and "開市前輸入之申報優先於開市後輸入之申報".

Engine: one FIFO per price level per side, plus one FIFO per side for market orders, which is served before any level.
**R5.1** The pre-open random arrangement is outside the engine: it takes pre-open orders in the order they are
presented, and the workload generator shuffles them before sending.

## R6 Call auction price (opening, closing, volatility-interruption auction)

OR Art. 58-3 ¶2: "Trade prices of call auction shall be determined based on the following principles:
1. Satisfying the maximum trade volume such that buy orders with prices higher than the determined price and sell
   orders with prices lower than the determined price shall be all satisfied.
2. Where there are buy and sell orders with prices equal to the determined price, at least one side shall be all
   satisfied.
3. Where two or more prices conform to the principles set forth in the preceding two subparagraphs, the price closest
   to the most recent traded price in the current session shall be used. If there is not yet any traded price in the
   current session, the price closest to the auction reference price at market opening of the current session."

TM-zh: 集合競價 "在當市漲跌停價格範圍內，以能滿足最大成交量的價位成交".

Engine: candidates are all valid ticks inside the daily limits, whether or not an order sits there. All trades of one
auction execute at the one price, in priority order (R5). Unfilled orders stay in the book (OR Art. 58-3 ¶5).

**R6.1 Equidistant candidates.** The rule does not say what happens if two qualifying prices are equally close to the
anchor. This cannot occur: the qualifying prices form a contiguous run of ticks and the anchor (a past trade price or
the reference price) is itself a tick inside the limits, so it is either inside the run (distance 0, unique) or on one
side of it (nearest end, unique). `docs/DESIGN.md` gives the argument and `make prove` checks it exhaustively on small
books. The golden model asserts that the nearest qualifying price is unique, so a counterexample would stop the tests.

**R6.2 Opening and closing price.** OR Art. 58-3 ¶5: the opening price is the price of the first matched trade; the
closing price is the closing auction price, or "Where unexecuted, the closing price shall be the last traded price
during the current session."

## R7 Continuous trading

OR Art. 58-3 ¶3: "When the price of a buy order entered is higher than or equal to the minimum price of a sell order
previously entered, orders shall be satisfied from the lowest to the highest price of sell order, in that order, until
orders are all satisfied or until the price of the buy order currently entered is lower than the price of the
unsatisfied sell order", and symmetrically for sells. TM-en: "transactions are taking place at the price offered by the
counterparty". Each fill is therefore at the resting order's price.

**R7.1 Market orders.** OR Art. 58-8 ¶1(2): "Prior to matching a trade, a market order shall have its reference price
converted ..., with the converted price as its order price: For a buy order, the converted reference price shall be the
most recent trade price of the security (or opening auction reference price in the absence of a most recent trade
price), maximum limit buy order, or maximum limit sell order, whichever is higher. ... For a sell order, ... minimum
limit buy order, or minimum limit sell order, whichever is lower." TM-en: "If, after the matching, there are market
orders remaining in the order book, they will always be listed as the top priority."

So an unfilled market-ROD order rests in the book ahead of every limit order, and when it later trades as the resting
side, the trade price is its converted price (DECK slides 18–22, TM-en example).

**R7.2 Ambiguity: does the incoming order count as "in the book" for the conversion?** DECK slide 21 (新進限價 VS 市價與
限價) converts a resting market buy with "最高賣單限價：90" where the only sell is the incoming limit sell at 90: yes, it
counts. DECK slide 40 lists "最低買單限價：-" for a resting market sell although a limit buy is entering, but there the
answer does not change the result. Interpretation: **the incoming limit order is included** (a fixed
interpretation; the other reading is not implemented). The other reading would leave a resting market sell unmatched
against an incoming bid below the last price, which contradicts "market orders have priority".

Consequence used by the engine: a resting market order always matches an incoming opposite order, and an incoming
market order always matches any resting opposite order; only the trade price needs the conversion.

## R8 Intraday volatility interruption (瞬間價格穩定措施)

OR Art. 58-3 ¶6: "Except [no-limit new listings] and securities for which the opening auction reference price is lower
than NT$1, during the period from the first matched trade of a security during the current session until the period of
time prior to market closing, if any execution price as test-calculated prior to each matching fluctuates beyond 3.5%
of the reference price:
1. If a trading order entered is a limit order and is valid for the current day, except that an order whose execution
   price as test-calculated is within the price range and shall be satisfied immediately, the TWSE shall at the same
   time postpone the matching of the security for two minutes, and continue to accept entries, cancellations, and
   changes of trading orders for that security. Matching will then proceed by call auction at the conclusion of the
   postponement period.
2. If a trading order entered is [limit IOC, market ROD or market IOC], except that an order whose execution price as
   test-calculated is within the price range and shall be satisfied immediately, the remaining unsatisfied volume of
   the trading order entered is canceled.
3. If a trading order entered is [limit FOK or market FOK], the trading order entered is all canceled."

DECK slide 30 summarises: 一般限價 — part inside the band trades, the rest stays in the book, interruption starts;
FOK — whole order cancelled, no trade; IOC and 一般市價 — part inside trades, rest cancelled; only limit-ROD starts an
interruption. CT: during the interruption "仍可輸入一般限價委託，不接受市價委託、IOC、FOK委託。進入瞬間價格穩定措施時，本公司
系統自動刪除已存在之市價委託".

Reference price — OR Art. 58-3 ¶7:
1. "Within five minutes after the first matching for the current session, the reference price is the price of the first
   matched trade. In the absence of a trade price in the first matching, the opening auction [reference] price shall be
   the reference price."
2. "After the first five minutes ..., a weighted average price is calculated based on the price and volume of all
   trades for the five-minute period immediately before the entry time of the order. In the absence of a trade price in
   the five minutes, the most recent trade price shall be used. In the absence of a most recent trade price, however,
   the opening auction [reference] price shall be the reference price."
3. "... in the event of a postponement ..., within the five minutes after the conclusion of the postponement period,
   the trade price of the call auction during the session is the reference price. In the absence of a trade price
   during the session, however, the reference price shall be calculated in accordance with the preceding subparagraph."

TM-en: "only trade prices of continuous trading are being considered, not the opening price generated by call auction".
Exceptions (TM-en / CT): the first matching after 9:00; reference price below TWD 1; warrants; no-limit new listings.

Engine parameters: `VI_BAND_PERMILLE=35`, `VI_HALT_S=120`, `VI_FIX_S=300`, `VI_WIN_S=300`, `VI_MIN_REF=1.00 TWD`.

**R8.1 Interpretations.**
- *"Beyond 3.5%"* (超過): a price exactly 3.5% away does not trigger. The comparison is exact
  (`1000·P·ΣQ > 1035·ΣPQ`), no rounding of the average.
- *Window.* Time has one-second resolution; the window for an order entered at second `t` is the trades of continuous
  matching with timestamps in `(t−300, t]`, not counting the order's own fills. The reference is fixed for the whole
  order ("before the entry time of the order").
- *Auction trades are not in the window.* TM-en says so for the opening price; the engine also leaves out the
  interruption auction's trades (the sources are silent). They do count as "the most recent trade price".
- *"Within five minutes"* includes the boundary second: TM-en's example treats 9:05:01 as the first second past it.
- *End of the applicable period.* TM-zh and CT say the measure runs to 13:25; DECK slide 27 (2019) says 13:20. The
  engine uses 13:25 (`T_CCALL`).
- *An interruption that would end at or after 13:25.* Not covered by the sources. Interpretation: the stock is already
  accumulating orders, so it goes straight into the closing call and is matched once, at 13:30 (a fixed
  interpretation; the alternative is not implemented).

## R9 Market data

OR Art. 58 ¶5: before the open and before the close TWSE discloses "the computed execution prices and volumes, and the
computed prices and volumes of the five highest unexecuted buy orders and five lowest unexecuted sell orders"; during
trading hours "the executed trade prices and volumes, and the prices and volumes of the five highest unexecuted buy
orders and five lowest unexecuted sell orders". TM-zh: "即時交易資訊" and a "5秒行情快照"; during an interruption the
simulated price, volume and best five are disclosed every five seconds, as at the open and close (CT).

Engine: a `SNAP` request returns the best five levels per side (and the resting market-order quantity per side). In
call phases it returns the simulated auction price and volume and the best five as they would stand after that match.
When to ask (every 5 s, every message) is the host's choice.

---

## Worked examples transcribed into test vectors

`tests/official_vectors.py` holds each of these with its source. "Engine" means both the golden model and the RTL.

| # | Source | What it shows |
| --- | --- | --- |
| A1 | TM-zh 圖例1.1 → 1.2 (IMG trading-1/2.jpg) | Call auction: 106.00, 185 lots, leftovers 買 105.50×10 / 賣 106.00×7, best five |
| A2 | DECK slide 7 成交價決定-集合競價 | Call auction: 102, 50 lots, time priority at the price |
| C1 | TM-zh 圖例3.1 (IMG trading-11.jpg) | Buy 50 @104.00 sweeps 103.00×10, 103.50×20, 104.00×20 |
| C2 | TM-zh 圖例3.2 (IMG trading-12.jpg) | Sell 50 @102.00 sweeps 103.00×10, 102.50×20, 102.00×20 |
| C3 | DECK slide 8 成交價決定-逐筆交易 | Buy 50 @102: 100×10, 101×20, 102×20 |
| O1–O3 | DECK slide 15 委託比較 | Sell 5 @100 as ROD / IOC / FOK against 102×1, 101×2 |
| O4 | TM-en IOC example (IMG ct-3) | IOC buy 50 @101: 100×10, 101×20, 20 cancelled |
| O5 | TM-en FOK example (IMG ct-4) | FOK buy 50 @101 against 30 lots: nothing trades |
| M1 | TM-en market example (IMG ct-1/2) | Market buy 3 vs resting market sell 3: 99×3 |
| M2–M7 | DECK slides 17–22 市價委託 2/8–7/8 | Converted reference prices in six situations |
| L1 | TM-zh 漲跌停價格計算範例 | 40.60 → 44.65 / 36.55 |
| L2–L4 | TM-zh 當日開盤競價基準 圖例1–3 | Next-day reference price and limits (one figure disagrees with R2, see R3.2) |
| V1 | TM-en reference-price example | VWAP 101.4, a trade at 105 triggers; 104 becomes the reference for five minutes |
| V2–V3 | DECK slides 28–29 | 9:00–9:05 fixed reference; rolling average after |
| V4–V13 | DECK slides 31–40 (1-1, 1-2, 2-1, 2-2, 3-1, 3-2, 4-1 … 4-4), TM-en IMG ct-5…12 | Each order type meeting the band |
| V6b | TM-en FOK example in the interruption section (IMG ct-12), volume 5 | FOK killed without starting an interruption |

Rules with no worked example found in the sources: the closing auction specifically (it uses R6), the tie-break by
closeness to the last price (R6 ¶3), `REDUCE` keeping priority, the minimum-tick clause of R3, and R8's "no trades in
the window" fallbacks. Those are covered by tests written from the rule text only.
