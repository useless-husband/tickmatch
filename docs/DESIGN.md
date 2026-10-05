# Design

`docs/RULES.md` says what the engine must do and where each rule comes from. This file says how the hardware does it,
what was hard, and what was tried and rejected.

## 1. Shape of the problem

A US-style matching engine keeps a sorted book over an open-ended price range and does one thing all day: continuous
price-time matching. A TWSE engine has a different shape:

- Prices are a small finite set. The daily limit is ±10% of the reference price and the tick grows with the price,
  so one stock on one day has at most a few hundred valid prices.
- The day has phases (R1): orders accumulate before 9:00 and are matched once, by a call auction; then continuous
  matching; then, at any time, a volatility interruption stops matching for two minutes and ends in another auction;
  then five minutes of accumulation and a closing auction.
- Market orders can rest in the book (R7.1) and get their price from the book at the moment they trade.
- Whether an order may trade depends on a five-minute volume-weighted average price (R8).

So the hardware is not a sorted structure plus a matcher. It is an indexed table, two procedures over it (match,
auction), a clock, and a running average.

```
              +--------------------------- tm_engine ----------------------------+
 in_* ------> | decode   price -> level (divide by tick)     reject reasons      |
 (valid/ready)|    |                                                             |
              |    v                                                             |
              | +---------+   best bid/ask    +----------------+                 |
              | | bitmaps |<----------------->| priority       |  tm_prienc x4   |
              | | 2 x 256 |                   | encoders       |                 |
              | +---------+                   +----------------+                 |
              | +-----------------+  +---------------------------+               |
              | | level tables    |  | order RAMs (block RAM)    |               |
              | | head/tail/qty   |  | info, next, prev by id    |               |
              | | (LUT RAM)       |  +---------------------------+               |
              | +-----------------+                                              |
              |   match loop | FOK walk | auction scan + uncross | snapshot walk |
              |   session clock (TIME)  | 300-bucket window (R8) | tm_daycfg     | ---> out_*
              +------------------------------------------------------------------+   (valid/ready)
```

Files: `rtl/tm_engine.sv` (everything above), `rtl/tm_daycfg.sv` (the day's price grid), `rtl/tm_prienc.sv`
(find first / last set bit).

## 2. Price levels as a directly indexed array

**The idea evaluated.** Because the daily limit bounds the range and the tick table makes it a finite set, give every
valid price of the day an index ("level") and keep, per side, a bitmap of non-empty levels. Best bid = highest set bit
of the bid bitmap; best ask = lowest set bit of the ask bitmap; a priority encoder gives either in one combinational
step. No heap, no tree, no shifting array.

**How many levels?** Computed, not assumed (`tests/test_rules.py::test_level_count_bound`, `make prove` P1):

- for every reference price below TWD 1000 the limits contain at most **182** valid prices (worst case 9.10: 8.19 to
  10.00);
- at TWD 1000 and above the tick stays at 5.00, so the count grows with the price: `ref/25 + 1`. 256 levels cover
  every reference price up to **TWD 6,395**; 6,400 needs 257.

`LVL_W` (default 8, i.e. 256 levels) is a parameter. If the reference price needs more levels than the build has,
`tm_daycfg` raises `cfg_error` and the engine rejects every message with `RJ_CFG`: it refuses the day rather than
truncate the range.

**The tick-band boundary.** A range may straddle a band boundary: reference 9.50 gives 8.55 … 9.99 in steps of 0.01,
then 10.00 … 10.45 in steps of 0.05. Since consecutive boundaries are at least a factor 2 apart and the range spans a
factor 1.22, **at most one boundary falls inside any day's range** (checked for every reference price in
`test_one_band_boundary_at_most`). So two tick sizes and one break point describe any day:

```
price(level) = lim_dn    + level * tick_lo                 level <  brk_lvl
             = brk_price + (level - brk_lvl) * tick_hi     level >= brk_lvl
```

`tm_daycfg` derives `lim_dn, lim_up, n_levels, ref_lvl, brk_lvl, brk_price, tick_lo, tick_hi` from the reference price
by walking: down from the reference one tick at a time while the next tick is still within 10%, then up from there to
the upper limit, counting. It needs adders and comparators only, takes at most a few hundred cycles, and runs once per
day. The rounding rule of R3 (inwards) and the minimum-tick clause fall out of the walk's stop condition.

Incoming prices arrive in cents and are converted to a level by dividing by `tick_lo` or `tick_hi`: an 8-step
restoring divider, split over two clock cycles. A non-zero remainder is "off tick" (R2); outside `lim_dn..lim_up` is
"outside the limits" (R3). After that the engine works in level indices; cents reappear only in output messages and in
the R8 average.

**Verdict: adopted**, with the level count made a checked parameter instead of a constant. What it buys beyond the
best-price lookup: the auction (section 4) becomes a single linear scan over at most 256 entries, and market-order
price conversion (R7.1) is a min/max of 8-bit indices.

## 3. Order queues

Each level and side has a FIFO of orders (R5 time priority). Orders are nodes of doubly linked lists threaded through
three block RAMs indexed by order id:

| RAM | Contents | Why separate |
| --- | --- | --- |
| `o_info[id]` | market flag, side, level, remaining quantity (0 = id free) | one write per fill |
| `o_next[id]` | next order in the queue | unlinking from the middle writes `next[prev]` and `prev[next]` in the same cycle |
| `o_prev[id]` | previous order in the queue | |

Per level and side, in LUT RAM (asynchronous read): head id, tail id, total quantity. Two more head/tail/quantity sets
in registers hold the resting market orders of each side, which are served before any level (R7.1).

The order id is the RAM address. The client (a gateway, in a real system) picks an id that is not live; the engine
rejects a NEW whose id is live (`RJ_DUP`) and a CANCEL/REDUCE whose id is not (`RJ_UNKNOWN`). There is no hash table
and no free list in the engine, and cancel is three states regardless of queue length.

Lists are terminated by the level's tail pointer and emptiness by the bitmap bit (or a zero total), not by null
pointers, so `next` of the last order and `prev` of the first are never read.

**Capacity limits.**

| Limit | Default | When exceeded |
| --- | --- | --- |
| live orders | 4,096 ids (`ID_W=12`) | cannot be exceeded: the id is `ID_W` bits wide, a live id is rejected with `RJ_DUP` |
| price levels | 256 (`LVL_W=8`) | the day is refused: `cfg_error`, every message rejected with `RJ_CFG` |
| order quantity | 4,095 lots (`QTY_W=12`) | cannot be exceeded (field width); 0 is rejected with `RJ_QTY` |
| quantity per level | 24 bits = `ID_W+QTY_W` | cannot overflow: every order at full size at one level fits |
| volume in the 5-minute window | 2^32 lots | sticky `err_vwap_ovf` output; the R8 reference is wrong from then on |
| time | seconds of one day, 18 bits | TIME going backwards is rejected with `RJ_TIME` |

## 4. The call auction in one pass

R6 asks for the price that (1) maximises volume with all better-priced orders filled, (2) fills one side completely
at the price, and (3) among several such prices is closest to the last trade (or the reference price).

Let `B(p)` be the buy quantity at or above `p` and `S(p)` the sell quantity at or below `p`. The volume at `p` is
`V(p) = min(B(p), S(p))`. "All better-priced orders filled" means `B(>p) <= V(p)` and `S(<p) <= V(p)`; given that,
principle 2 always holds (whichever of `B(p)`, `S(p)` is the minimum is filled completely).

`B` falls and `S` rises with `p`, so the prices with maximum volume are a contiguous run of ticks, and inside it the
two conditions of principle 1 hold on a contiguous sub-run `[lo, hi]`. The anchor of principle 3 is itself a valid
tick inside the limits. Therefore the answer is simply

```
price = clamp(anchor, lo, hi)
```

and two prices can never be equally close (R6.1). The hardware does one scan to total the buy side, then one ascending
scan over all levels (occupied or not, since an empty tick can be the answer) keeping the running maximum and the
`lo`/`hi` of the qualifying run. About `2 * n_levels` cycles. Uncrossing then pops the best buy and best sell
repeatedly at that one price until the book no longer crosses at it.

The golden model does not use this argument: it evaluates the three principles literally for every tick and picks the
closest by price distance, with an assertion that the closest is unique. `make prove` P3 checks RTL against it on all
19,683 books of four levels with 0–2 lots per side per level, with the anchor below, inside and above, and checks that
the qualifying set is contiguous; the random runs do the same on large books.

The same scan, without the uncross, serves the snapshot in call phases (R9): it yields the simulated price and
volume, and the best five "after the match" are the levels at or beyond that price with the quantity at the price
itself replaced by `B(p) - V` or `S(p) - V`.

## 5. Continuous matching and market orders

One fill per pass through four states: pick the candidate (head of the opposite market queue if any, else head of the
best opposite level), fetch it, test the R8 band, trade. The trade is at the resting order's price; for a resting
market order that price is its converted reference price (R7.1), computed on the spot as a min or max of level
indices: last trade level, best/worst bid, best/worst ask, and the incoming limit order's level (R7.2). That is why
each bitmap has both a lowest-bit and a highest-bit encoder.

A consequence of R7.1 that the RTL relies on and the golden model does not: a resting market order always crosses an
incoming opposite order, and an incoming market order always crosses whatever rests opposite. The golden model
computes both converted prices and compares them, as the rule is written; the equivalence runs would expose a case
where the shortcut fails.

FOK needs to know before the first fill whether the whole order can trade, every fill inside the band (R4, R8). A
walk over the opposite levels (market queue first) adds up quantity until the order is covered, the limit price is
passed, or a level lies beyond the band. No state changes during the walk.

## 6. The five-minute average

R8's reference is a volume-weighted average over the trades of the last five minutes, so the engine needs
`sum(price*qty)` and `sum(qty)` over a sliding window. Time has one-second resolution, so the window is exactly 300
one-second buckets in a block RAM plus running totals: when a second passes, the current bucket is stored and the one
that is now 300 seconds old is subtracted. A TIME message that skips `d` seconds costs `2*min(d, 300)` cycles.

No division: "price P is more than 3.5% from the average" is tested as `1000*P*sumQ > 1035*sumPQ` (and `< 965`),
exactly. The fixed references of R8 (opening price for five minutes, auction price for five minutes after an
interruption, last price when the window is empty) use the same comparator with `sumQ = 1`.

## 7. Message interface

One input stream and one output stream, each valid/ready. One input message is processed at a time; `in_ready` is high
only when the engine is idle and every output of the previous input has been taken, so outputs are attributable to
inputs without tags.

| Input `in_op` | Fields | Meaning |
| --- | --- | --- |
| 1 NEW | id, side, mkt, tif (0 ROD, 1 IOC, 2 FOK), price (cents), qty (lots) | new order |
| 2 CANCEL | id | cancel what is left |
| 3 REDUCE | id, qty | take `qty` lots off; keeps queue position; removes the order if nothing is left |
| 4 TIME | price field = seconds of the day | the clock moves forward |
| 5 SNAP | | request best five |

| Output `out_type` | id | id2 | price | qty | code |
| --- | --- | --- | --- | --- | --- |
| 1 ACK | order | | price (0 = market) | qty | |
| 2 REJ | order or 0 | | | | 1 closed, 2 type not allowed in phase, 3 outside limits, 4 off tick, 5 quantity, 6 id live, 7 id unknown, 8 time backwards, 9 bad message, 10 day refused |
| 3 TRADE | buy order | sell order | price | qty | 0 continuous, 1 auction |
| 4 CXL | order | | | qty removed | 0 user, 1 reduced to zero, 2 IOC remainder, 3 FOK, 4 beyond the R8 band, 5 market order withdrawn |
| 5 RED | order | | | qty left | |
| 6 VI | triggering order | | price that was beyond the band | | |
| 7 AUC | | | auction price (0 = none) | volume | 0 open, 1 interruption, 2 close |
| 8 PHASE | | | | time of the change | 0 pre-open, 1 continuous, 2 interruption, 3 closing call, 4 closed |
| 9 MDL | | | level price (0 for the market queue) | quantity | rank 0–4 bid, 8–12 ask; 7 / 15 market buy / sell |
| 10 MDE | | | last price, or simulated auction price | simulated volume | phase |

## 8. Verification

| Layer | What it shows | Command |
| --- | --- | --- |
| Official examples | the golden model reproduces TWSE's own worked examples; the RTL gives the same messages | `make unit`, `make vectors` |
| Directed boundaries | exact-second and exact-price boundaries no official example covers | `make vectors` |
| Equivalence | RTL output = golden output, every message, in order, on seeded random and adversarial flows; half the seeds with random stalls on both handshakes | `make equiv`, `make equiv-long` |
| Invariants | a third, independent bookkeeper replays the RTL's log and checks I1–I8 (`tools/invariants.py`) | same targets |
| Exhaustive | day configuration for every reference price to TWD 7,000; price grid; auction price on all small books | `make prove` |
| Mutation | 42 planted RTL bugs, each must be caught | `make mutants` |

The golden model and the RTL are different programs on purpose: prices in cents and Python dicts against level indices
and linked lists; a literal reading of R6 against the clamp; the full R7.1 comparison against the shortcut. The
invariant checker shares neither's matching code.

What the first mutation run found: two survivors, both off-by-one-second boundaries in R8 (the last second of a fixed
reference; an interruption ending at exactly 13:25:00). Random flows almost never land on those seconds. The fix was
`tools/directed_stim.py` plus making the generator jump to those seconds on purpose.

## 9. Synthesis, "Detected loop" warnings and the critical path

Early synthesis runs printed tens of thousands of Yosys `Detected loop` warnings and a longest path of 208 cells.
Both were artefacts of how the path was measured, not loops in the design:

- `ltp -noff` only skips Yosys's internal flip-flop types. After `synth_xilinx` the registers are `FDRE`/`FDSE`
  cells, which `ltp` treats as combinational, so every state register closed a "loop" (the first run: 3,128 warnings,
  e.g. at `u_cfg.error` and the state-machine nets).
- With the flip-flops deleted, the LUT-RAM cells that hold the level tables (`RAM64M`, `RAM256X1S`) remained. `ltp`
  sees a path from their write-data and write-address pins to their read output, and the head pointer read from the
  table feeds the logic that computes the next write. That is a loop through a clocked write port (36,578 warnings,
  208-cell "path"). DSP48 cells that absorbed pipeline registers cause the same kind of false path.

How this was settled: `make loopcheck` runs `yosys check -assert` on the flattened RTL netlist before any technology
mapping, where registers and memory write ports are explicit clocked cells. It reports 0 problems, and it fails on a
planted two-gate loop, so it would fail the build on a real one; CI runs it. The depth figure now comes from a second
run with `-nodsp -nolutram`, registers and block RAMs deleted, which leaves only LUTs, carry chains and muxes; that run
reports no loops at all.

The real critical path (from `synth/report.md`): the bid/ask bitmaps, through a priority encoder, through the
market-order price conversion (R7.1, a chain of min/max comparisons of level indices), into the level-to-price
multiplication, into `c_price`. Cutting it would mean one more state per fill (register the converted level, then
multiply); not done, because the clock figure is an estimate in any case.

## 10. Alternatives rejected

- **Sorted array or heap of price levels.** The usual software answer. With at most 256 possible prices it buys
  nothing and costs an insertion network or pointer chasing.
- **Lazy cancel (mark dead, skip at match time).** One RAM instead of three, but the match loop's latency then depends
  on how many dead orders it has to skip, and the per-level quantity needed for market data and auctions would need
  separate upkeep anyway.
- **Hashing external order ids inside the engine.** Left to the gateway; the id is the address.
- **Exact timestamps with a trade history buffer for R8.** Exact to the millisecond but unbounded in size. One-second
  buckets are exact at the resolution the engine's clock has, and the resolution is a stated parameter.
- **Computing the R8 band as a pair of level indices once per order.** Needs a division by the window volume. The
  cross-multiplied comparison per fill needs one multiplier and no rounding rule.
- **Best five after every message.** The walk costs up to 13 output messages; TWSE itself publishes both real-time
  data and 5-second snapshots, so the request is a message and the host decides how often.
- **Pipelining messages.** Rules like "market orders are withdrawn the moment an interruption starts" make every
  message depend on the complete effect of the previous one. The engine is a multi-cycle state machine.

## 11. Known limits

Listed in the README. In short: simulation only; one symbol per instance; time in whole seconds; no postponed
open/close (R1.1); pre-open priority is arrival order (R5.1); no self-trade prevention, no account or risk checks;
the price change message is sent as cancel + new (R4.1).
