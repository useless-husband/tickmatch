# Status: postponed open/close and random pre-open order — not started

**Stopped at step 1: this VM cannot reach twse.com.tw.** Following the task's instructions, nothing was implemented
from memory. RTL, golden model, tests, docs, README and CHANGELOG are unchanged; this file is the only change on the
branch.

## What was tried (cloud Linux VM, 2026-10-06)

```
$ curl -sS -o /dev/null -w '%{http_code}' --max-time 30 <url>
https://www.twse.com.tw/zh/products/system/trading.html                        -> 000
https://www.twse.com.tw/en/products/system/trading.html                        -> 000
https://twse-regulation.twse.com.tw/TW/law/DAT0201.aspx?FLCODE=FL007304        -> 000
https://accessibility.twse.com.tw/zh/products/system/continuous-trading.html   -> 000
curl: (56) CONNECT tunnel failed, response 403
```

The VM's egress proxy recorded `connect_rejected` (HTTP 403 to CONNECT) for `www.twse.com.tw:443`,
`twse-regulation.twse.com.tw:443` and `accessibility.twse.com.tw:443`. That is the session's network policy refusing
the hosts, not a TWSE outage, so the request was not retried or routed around.

## Not done

- Sources for 延緩開盤 / 延緩收盤 and the pre-open random arrangement were not fetched; docs/RULES.md (R1.1, R5.1) is
  unchanged.
- No golden-model, RTL, generator, invariant, directed-test or mutant changes.
- Lint, loopcheck, `make test`, `make mutants` and Yosys were not re-run: no code changed, so the README numbers stand.
- CI: `.github/workflows/ci.yml` runs on pushes to `main`, pull requests and manual dispatch only, so pushing this
  branch starts no run.

## To continue

Allow `www.twse.com.tw`, `twse-regulation.twse.com.tw` and `accessibility.twse.com.tw` in the cloud environment's
network settings (Network access → Custom → Allowed domains), then re-run the task from step 1 on this branch.
