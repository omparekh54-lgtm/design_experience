# Module B — technical universe and setups

Implementation version: `MODULE_B_v1.1`. Production decisions use the combined
version `PMS_v1.1.0_module_a:MODULE_B_v1.1`. Module A's version is unchanged.

## Run locally

Install the repository requirements and use the existing `.env` database URL.
The CLI sets the `pms,public` search path automatically.

```powershell
python scripts/evaluate_module_b.py --all --refresh-calendar
python scripts/evaluate_module_b.py --all --persist
python scripts/evaluate_module_b.py --instrument NSE:VGUARD --research-only
python scripts/evaluate_module_b.py --instrument NSE:VGUARD --setup 2
```

`--all` summarizes rejection counts and includes signal/watchlist records. Add
`--details` to include every stock. Omitting `--persist` is read-only, except
`--refresh-calendar`, which explicitly saves a sourced holiday observation.
`--as-of YYYY-MM-DD` selects an after-close historical evaluation date; future
dates are rejected. Refreshing today's calendar does not backdate its knowability.

The production API remains `GET /v1/module-b/{instrument_id}?as_of=YYYY-MM-DD`.
It returns rejection reasons, Module A reasons, current signal, valid-rally
watchlist and diagnostics. `require_module_a=false` returns HTTP 400. The CLI's
single-stock research bypass is explicitly labelled and cannot persist signals.

## Rules implemented

| Requirement | Behavior |
| --- | --- |
| Fundamental membership | Module A must pass at evaluation time; rejected stocks do not run technical queries. Module C also requires the production result. |
| Technical universe | Price > ₹30; average 20-session volume >100,000; worst volume >75,000; average turnover ₹1–500 crore inclusive; ADR20 ≥3%; within 50% of 252-session high; at least 252 complete observed sessions; exclude NIFTY50 membership on the evaluation date. |
| Turnover | Prefer reported rupee turnover. Missing values use close × volume, disclosed in successful-result diagnostics. |
| Earnings blackout | Exclude already-announced earnings from the evaluation date through the next three exchange sessions, including event dates on intervening holidays/weekends. Future announcements are not used. |
| Refresh | Recheck the universe on every scan, exceeding the minimum once per four sessions. |
| Setup 1 | First strict close above EMA21 after a close at/below it; base is crossing-day low; rally ≥15%; velocity ≥1% per session; peak reached within 30 sessions; average rally volume ≥1.5× frozen preceding 50-session volume. |
| Rally window convention | Duration and average rally volume cover crossing day through highest-high day, inclusive. A flat tail does not extend that window. A higher high recomputes all qualification tests. |
| Signal | First strict close below EMA21 after a valid rally. Equality does not signal. The signal bar does not retroactively qualify an invalid rally. |
| Entry window | The next three trading sessions after the signal. After-close output is live on signal day and after sessions one/two; after session three it is expired. The trade engine separately handles orders during those sessions. |
| Watchlist | Current valid rallies above/at EMA21 are returned before a signal forms. |
| Setup 2 | Research/backtest only. Requires an earnings Stage-3 trigger at filing time, with Module A membership by default; pre-result close uses the actual IST filing timestamp; reaction is the first session after filing date; reaction volume ≥2× preceding 20; qualification within 30 sessions; first subsequent close below EMA21; expires after 60 reaction sessions. |

## Price and calendar integrity

Use only completed observed exchange sessions from the official NIFTY50 series.
Ignore non-session equity rows, choose sources deterministically, validate OHLCV,
and reject stale prices or gaps in the most recent 252 sessions. Current-day
unfinished candles are excluded. Source preference is Angel One, NSE, then other
feeds in deterministic name order.

Split/bonus actions are deduplicated; conflicting ratios fail closed. Explicitly
configure already-adjusted feeds in `technical.adjusted_price_sources` only after
verifying their import settings. Mixed raw/adjusted series with actions fail.
Yahoo rows with applicable split/bonus actions remain blocked until their
adjustment basis is verified. Historical price vintages still need a separate
point-in-time audit before backtesting results can be certified.

Future regular sessions use official NSE holiday snapshots stored in
`provider_snapshots`, observed no later than the requested timestamp. Unknown
years fail closed. The holiday response flags Diwali 2026-11-08 as a potential
special session; any forward window crossing that date fails closed until a
sourced snapshot explicitly supplies `special_sessions`. Other special-session
circulars must also be ingested. Observed historical dates take precedence over
weekday assumptions. Benchmark dates are used as calendar information, not as
future market-price inputs.

## Persistence and validation

`--persist` saves explainable Module B decisions in `strategy_decisions` and
Setup-1 signals in `module_b_signals`. Identical instrument/timestamp/version
saves update the decision; signal identity is instrument/date/setup/version.
The write transaction uses an advisory lock plus signal upserts. Failing the
universe or losing a signal cancels/expires active records. Terminal `CONSUMED`,
`CANCELLED`, `SUPERSEDED` and `EXPIRED` records never become active on replay.
Research and Setup-2 results cannot enter production persistence.

Run `python -m pytest -q`. A rollback-only Postgres integration test is available
when `PMS_TEST_DATABASE_URL` points to an explicitly chosen migrated test DB.
The connected database also passed the actual persistence SQL assertions for
idempotency, consumed/expired replay and expiry-day status, with all test rows
rolled back.

See `module_b_validation_2026-10-18.json` for validation evidence. The full
2,302-stock Module A gate replay yields zero eligible stocks and zero production
signals. This validates blocking, not a successful production trade path. The
real VGUARD price diagnostic correctly rejects stale/missing sessions and ADR;
its index/earnings reference checks were stubbed and it is research-only.

Module B code is implemented and tested. Production qualification remains
blocked by Module A's missing policies/fundamental/reference inputs. The earlier
Phase-1 sectors/benchmark pass does not establish complete stock-price,
financial-statement or legacy surveillance history. No order is submitted by
this module or its CLI.
