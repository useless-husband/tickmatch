# Changelog

## 0.1.0 (unreleased)

First complete version.

- `docs/RULES.md`: the TWSE rulebook for ordinary board-lot stock trading, quoted from TWSE sources fetched on
  2026-10-06, with interpretations of the ambiguous points as named parameters.
- Golden model (`model/`): sessions, call auctions, continuous price-time matching, limit/market × ROD/IOC/FOK,
  daily limits and tick bands, volatility interruption, best five.
- RTL engine (`rtl/`): directly indexed price levels with bitmaps and priority encoders, per-level FIFO queues as
  doubly linked lists in block RAM, one-pass call auction, 300-bucket five-minute average, valid/ready message
  interface; parameterised capacity.
- Verification: TWSE worked examples (golden model and RTL), directed boundary scenarios, seeded random and
  adversarial equivalence (every output message compared), an independent invariant checker, exhaustive checks on
  small configurations, a mutation script with 42 planted bugs, `yosys check` for combinational loops.
- Tools: workload generator, benchmark, Yosys 7-series synthesis estimate, static replay report, `跑跑看.command`.
