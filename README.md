# tickmatch

A stock-exchange matching engine in SystemVerilog that follows the **Taiwan Stock Exchange (TWSE) rulebook** instead of
the usual US-style continuous book: opening and closing call auctions with TWSE's price rule, ±10% daily limits on a
banded tick table, limit/market × ROD/IOC/FOK orders allowed only in the right sessions, market orders that rest at
top priority with a converted price, and the intraday volatility interruption (瞬間價格穩定措施) that halts matching
for two minutes and restarts it with an auction. One engine instance trades one symbol for one day.

It is verified three ways: against worked examples taken from TWSE's own published material, against a separate
software model of the rules on more than three million seeded random and adversarial messages (every output message
compared, in order), and by an independent invariant checker. A mutation script plants 42 bugs in the RTL; all are
caught.

> **Read this first.** tickmatch is an educational model of published rules. It is not affiliated with or endorsed
> by TWSE and is not a certified implementation. The rules are as of **2026-10-06**, the date the sources in
> [`docs/RULES.md`](docs/RULES.md) were fetched. No real order data is used anywhere (TWSE order-level data is not
> freely available); every order flow is generated from a fixed seed. This is not investment software.
> Everything runs in simulation: there is no FPGA board, and the clock figure below is a synthesis estimate.

中文說明：[README.zh-TW.md](README.zh-TW.md) · 給初學者的導讀：[docs/導讀.zh-TW.md](docs/導讀.zh-TW.md)

## Demo

`./跑跑看.command` (or `make report`) generates one trading day for a stock with reference price 583.00, runs it
through the Verilated RTL, compares every answer with the golden model, checks the invariants and opens a static
replay page:

```
$ make report
python3 tools/gen.py --seed 2332 --profile day --ref 58300 --messages 200000 --stim build/day/day.stim --exp build/day/day.exp
seed 2332 profile day: 123571 lines; acks 72131, rejects 2007, trades 38922, cancels 32084, interruptions 3, auctions 5, snapshots 2161
./build/sim_tm --stats build/day/day.stats < build/day/day.stim > build/day/day.out
python3 tools/compare.py build/day/day.exp build/day/day.out build/day/day.stim
identical: 123571 input messages, 171029 output messages
python3 tools/invariants.py build/day/day.stim build/day/day.out
invariants hold: 1 days, 123570 messages
python3 tools/make_report.py
build/report.html: 628 frames, 196 KB; day had 123570 messages, 38922 trades, 3 interruptions
report: build/report.html
```

The page (`build/report.html`, one file, no network, no WebAssembly) steps through the day message by message: the
best-five ladder, each input and every output it caused, the opening auction uncrossing, a volatility interruption
firing and its auction, the closing call, a per-minute price chart, latency histograms and the synthesis numbers.

## What the engine implements

Each rule is quoted with its source in [`docs/RULES.md`](docs/RULES.md); section numbers below refer to it.

| Rule | |
| --- | --- |
| R1 sessions | 08:30 pre-open accumulation → 09:00 opening auction → continuous trading → 13:25 closing call → 13:30 closing auction, driven by TIME messages |
| R2, R3 price grid | the TWSE equity tick table; limits ±10% of the reference price rounded inwards to a valid tick, minimum-tick clause |
| R4 order types | limit/market × ROD/IOC/FOK in continuous trading; limit ROD only in call phases; resting market orders withdrawn when a call phase starts; cancel; reduce keeping queue position |
| R5 priority | market orders first, then price, then time |
| R6 call auction | maximum volume, all better-priced orders filled, one side at the price filled, nearest to the last trade (or reference) price |
| R7 continuous | fills at the resting order's price; market-order converted reference price, including for resting market orders |
| R8 volatility interruption | reference = opening price for 5 min, then the 5-minute VWAP, then the interruption auction price for 5 min; >3.5% away: limit ROD halts the stock 2 min and ends in an auction, IOC/market cancel the rest, FOK is killed; exempt below TWD 1 |
| R9 market data | best five levels per side on request; in call phases the simulated price, volume and best five after the match |

Out of scope: odd lots, after-hours fixed-price, block trades, warrants/ETFs/bonds, newly listed stocks without limits,
disposition stocks, short-sale restrictions, postponed open/close (R1.1).

## How it works

TWSE's rules make the set of prices one stock can trade at in one day small and finite, and the hardware is built
around that. Details and rejected alternatives: [`docs/DESIGN.md`](docs/DESIGN.md).

- **Price levels are an indexed array, not a sorted structure.** Every valid price of the day gets a level number
  (at most 182 for any stock below TWD 1,000; 256 levels cover every reference price up to TWD 6,395). Per side, a
  bitmap marks non-empty levels and a priority encoder gives the best bid or ask in one step. A day's range holds at
  most one tick-band boundary (checked for every price), so two tick sizes and one break point describe it;
  `rtl/tm_daycfg.sv` derives them from the reference price at the start of the day. (A fixed-tick array for an FPGA
  order book is not new: He et al., FPL 2017, use one. What is specific here is deriving it from TWSE's limit and
  tick rules, including the band boundary.)
- **Queues are doubly linked lists in block RAM**, indexed by order id, so a cancel from the middle of a queue takes
  constant time and a reduce keeps the order's place.
- **The call auction is one pass.** The prices that satisfy R6's first two principles form one contiguous run of
  ticks, so the price is `clamp(anchor, lo, hi)`. The hardware finds `lo` and `hi` in one ascending scan; the golden
  model applies the three principles literally; `make prove` compares them on all 19,683 small books.
- **The five-minute VWAP is 300 one-second buckets** with running sums; the 3.5% test is cross-multiplied, so there
  is no divider and no rounding.
- **Interface:** one valid/ready input stream (NEW, CANCEL, REDUCE, TIME, SNAP) and one valid/ready output stream
  (ACK, REJ with a reason, TRADE, CXL with a reason, RED, VI, AUC, PHASE, best-five levels). One message at a time.

## Results

All numbers below were measured on an Apple M5 (shared with other jobs), Verilator 5.052, Yosys 0.69, Python 3.13.

### TWSE's worked examples

35 worked examples were found in TWSE material (call-auction tables, continuous-matching tables, the ROD/IOC/FOK
comparison, market-order conversion slides, limit-price and reference-price examples, the VWAP example and eleven
volatility-interruption slides); the list with sources is at the end of `docs/RULES.md`.

- The golden model reproduces **34 of 35** exactly (`make unit`). The 35th is a source discrepancy, recorded as a
  failing comparison rather than a pass: a TWSE page states a limit-up of 106.7 for a reference of 97, which is not a
  valid price under the same page's tick table; the rules give 106.50 (R3.2).
- The RTL produces the same messages as the golden model on all **31** examples that are message sequences
  (`make vectors`); the four limit/reference-price examples are covered by `make prove`, which checks the RTL's
  limits for every reference price up to TWD 7,000.
- Not found in the sources, so tested from the rule text only: a closing-auction example, the nearest-price tie-break,
  reduce keeping priority, the minimum-tick clause.

### Equivalence and invariants

| Check | Result | Command |
| --- | --- | --- |
| RTL vs golden, random and adversarial flows (12 seeds; odd seeds with random back-pressure on both handshakes) | **3,017,660 input messages, 3,892,706 output messages, all identical** | `make equiv-long` |
| same, CI size (4 seeds) | ~200,000 messages | `make equiv` |
| invariants on the RTL's own logs (price-time priority, limits and ticks, quantity conservation, cancelled orders never trade, book never crossed in continuous trading, auction conditions, best five, nothing after the close) | hold on every run above | `tools/invariants.py` |
| directed boundary scenarios (band edges, exact-second ends of fixed references and halts, interruption ending at 13:25:00, FOK for exactly what is there) | identical | `make vectors` |
| exhaustive: limits and level count for all 4,800 valid reference prices up to TWD 7,000; price↔level mapping at 260 references; auction price on 19,683 books | all equal to the rule | `make prove` |
| planted RTL bugs | **42 of 42 caught** | `make mutants` |
| combinational loops in the RTL netlist | none (`yosys check -assert`) | `make loopcheck` |

The adversarial profiles cover deep queues with cancels in the middle, large sweeps across many levels, invalid
messages and limit-price corners, frequent interruptions, an id space of eight so ids are reused at once, many resting
market orders, and reference prices on tick-band boundaries, below TWD 1 and beyond the 256-level capacity.

### Latency and throughput

Latency in clock cycles from accepting a message to being ready for the next, output side always ready, including
sending every output message (one per cycle). `make bench`, ordinary-day flow:

| Message | p50 | p99 | max |
| --- | --- | --- | --- |
| cancel / reduce | 4 | 4 | 4 |
| new limit ROD, rests without trading | 6 | 6 | 6 |
| new limit ROD, 1 fill | 7 | 9 | 9 |
| new limit ROD, 2–4 fills | 10 | 16 | 18 |
| new limit ROD, 5+ fills | 22 | 40 | 52 |
| new market order, 1 fill | 6 | 11 | 11 |
| new FOK, killed | 7 | 10 | 13 |
| rejected message | 4 | 4 | 4 |
| TIME (session clock, window update; auctions run here) | 5 | 9 | 5,693 |
| best-five snapshot (call phases include an auction scan) | 19 | 256 | 256 |

Average cycles per message: 6.9 (ordinary day), 12.4 (sweep profile), 16.1 (adversarial mix).

At the **estimated** 50 MHz clock below, the ordinary-day flow corresponds to about 7.2 million messages per second
for one symbol, and a cancel or a single-fill order to 80–140 ns. For comparison, He et al. (FPL 2017) report
132–288 ns and 1.2–1.5 million messages per second for an FPGA **order book update** (feed processing at 10 Gb/s,
measured on hardware). The two are not like for like: theirs includes network I/O and was measured on a board; this
engine does matching rather than book building, excludes I/O, and its clock is a pre-place-and-route estimate.
Treat the comparison as an order of magnitude only.

### Synthesis estimate

`make synth`: Yosys `synth_xilinx -family xc7`, default parameters (4,096 order ids, 256 levels, 12-bit quantities).
Full report: [`synth/report.md`](synth/report.md).

| LUTs | LUT-RAM cells | Flip-flops | RAMB36 | RAMB18 | DSP48 |
| --- | --- | --- | --- | --- | --- |
| 11,050 | 208 | 2,495 | 3 | 9 | 34 |

Longest register-to-register path: 30 LUT/mux levels and 10 CARRY4 stages, from the bid/ask bitmaps through a priority
encoder, the market-order price conversion and the level-to-price multiplication. **Timing ESTIMATE: about 20 ns,
50 MHz**, using 1.0 ns + 0.6 ns per LUT level + 0.1 ns per carry stage. No place and route was run; a real figure
needs one. Early runs printed thousands of "Detected loop" warnings; they come from measuring paths through
flip-flop and LUT-RAM cells and are explained in `docs/DESIGN.md` §9; `yosys check` finds no loop in the RTL.

## Limitations

- Simulation only. No board, no place and route, no network interface.
- One symbol per engine instance; time has one-second resolution; one message is processed at a time.
- Postponed open and close (暫緩開盤/收盤) are not modelled (R1.1). Pre-open time priority is arrival order; TWSE
  randomises it, so the generator shuffles instead (R5.1). Price change is cancel + new, which is what TWSE says it is.
- Capacity: 4,096 live orders, 256 price levels (reference prices up to TWD 6,395), 4,095 lots per order by default;
  a day needing more levels is refused, not truncated. All parameterised.
- No accounts, risk checks, self-trade prevention or short-sale rules.
- The critical path was not pipelined further (`docs/DESIGN.md` §9).
- CI uses Ubuntu's older Verilator and Yosys; the design keeps to a conservative SystemVerilog subset for them.

## Related work

Open-source order books and matching engines are plentiful, including on FPGAs, and FPGA trading systems are a
recurring university course project. The closest ones found:

- [yibo-hou/fpga-limit-order-book](https://github.com/yibo-hou/fpga-limit-order-book): SystemVerilog price-time
  limit order book for Artix-7 with a Python golden model, UDP/Ethernet and UVM verification. Continuous matching only.
- [mjfalz16/FPGA-Trading](https://github.com/mjfalz16/FPGA-Trading): SystemVerilog limit order book and matching engine
  for Zynq-7000 with a Python golden model and a randomized self-checking testbench.
- [adilsondias-engineer/08-fpga-order-book](https://github.com/adilsondias-engineer/08-fpga-order-book): BRAM-based
  order book with 256 price levels and best bid/offer tracking (book building, not matching).
- C. He, H. Fu, W. Luk, W. Li, G. Yang, "Exploring the Potential of Reconfigurable Platforms for Order Book Update",
  FPL 2017 ([pdf](https://www.doc.ic.ac.uk/~wl/papers/17/fpl17ch.pdf)): fixed-tick order book structure on an FPGA.
- Course projects: MIT 6.111 "HFT Accelerator" (2019), MIT 6.205 "High Frequency Trading on FPGA" (2022), Cornell
  ECE 5760 "High Frequency Trader" (2024), Columbia 4840 "HFT Book Builder" (2024). All follow US-style feeds or
  continuous matching.
- Software: e.g. [philipperemy/order-book-matching-engine](https://github.com/philipperemy/order-book-matching-engine).

How this one differs: it implements a specific real market's rulebook — TWSE's call-auction price rule, banded ticks
and daily limits, session-dependent order types, resting market orders with a converted price, and the volatility
interruption — checks it against that exchange's own worked examples, and uses those rules to shape the hardware. To
my knowledge no public RTL implements TWSE's rules; that is a statement about what I could find, not a claim of
novelty for the techniques, which are standard.

## Build and test

Requirements: Verilator (5.020 or newer), a C++17 compiler, Python 3.10+ (standard library only), GNU make; Yosys for
`make synth` and `make loopcheck`. Paths may contain spaces.

```
make lint          # verilator -Wall
make unit          # golden model: TWSE examples, rule tests, invariant-checker self-test
make vectors       # RTL vs golden on TWSE examples and directed boundary scenarios
make equiv         # RTL vs golden on random/adversarial flows (~200k messages)
make prove         # exhaustive small-configuration checks (~30 s)
make test          # all of the above
make equiv-long    # 3 million messages (~1.5 min)
make mutants       # 42 planted bugs (~2.5 min with 4 jobs)
make bench         # latency per message type, throughput
make loopcheck     # yosys check: no combinational loops
make synth         # Yosys 7-series estimate (~6 min)
make report        # one simulated day -> build/report.html
```

## License

MIT, see [LICENSE](LICENSE).
