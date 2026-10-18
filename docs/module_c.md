# Module C — readiness ranking and allocation

Version: `PMS_v1.1.0_module_a:MODULE_C_v1.1:PAPER`. Implements section 5 of
`PMS_Rules_v1(1).docx`. Module C creates order proposals, never signals or broker
orders. Setup 1 is the only production setup; Setup 2 and research bypasses are
rejected. Financial eligibility remains mandatory.

## Run

```powershell
python scripts/evaluate_module_c.py
python scripts/evaluate_module_c.py --persist --details
python scripts/ingest_module_c_benchmark.py
```

The default evaluation is read-only and prints the order sheet, watchlist, counts
and global blockers. `--details` includes every rejection; `--persist` saves the
current daily result. The existing `.env` database URL is used with the
`pms,public` search path. Future dates and intraday generation on a trading day
are rejected. An evaluation on a weekend uses the latest completed exchange
session for market/account/regime data and current knowable A membership.

The benchmark command imports approximately the latest year of official Nifty
500 history by default. Supply `--start YYYY-MM-DD --end YYYY-MM-DD` for a larger
backfill. Successful monthly windows are committed independently; the final audit
compares actual dates against official Nifty 50 sessions. No Nifty 50 or
MidSmallcap substitute is used for relative strength.

`GET /v1/module-c?trade_date=YYYY-MM-DD` now returns numeric score components,
detailed decisions, the selected order sheet and ranked watchlist for this exact
implementation version. Existing `selected_only` and `limit` parameters remain.
Results are PAPER proposals (`proposal_only: true`), not executable orders.

## Eligibility and allocation

A bulk Module A scan produces an explicit result for every stock. Rejected A
stocks skip B and retain the A reasons. Accepted stocks must have a production
Module B universe pass and live Setup-1 signal, or a current valid-rally watchlist
state. Historical sector classifications come from A's point-in-time context.

Each entry proposal checks the next-session window, current ADR ≥3%, announced
earnings within three trading sessions of the planned entry session, not already
held/pending, at least one share, 2% of **20-session** ADTV, and minimum score 60.
Earnings knowledge is capped at the evaluation timestamp. Entry session days 1–3
are valid; after-close signal day prepares day 1, and after the third session
there is no new proposal. Holidays use the same sourced calendar as Module B.

Candidates are sorted by score descending, stop-risk percentage ascending,
ADTV descending, then instrument ID for deterministic ties. Allocate top-down
under all of these limits:

- Open plus pending positions ≤ Module E capacity and never more than seven.
- At most two positions per normalized sector, including pending orders.
- Position value ≤10% of fixed account NAV and initial trade risk ≤0.5% of NAV.
- Existing plus newly reserved heat ≤3.5% of the same NAV.
- Available cash covers entry value **and estimated entry costs**.

Heat is actual rupee risk divided by NAV. It is not clipped to a default risk
budget and does not use shrinking free cash as its denominator. Every accepted
proposal reserves its projected cash, heat and sector/position slot within the
plan. Duplicate instruments and non-finite values cannot qualify.

The order sheet includes rank, score/components, buy stop, stop, shares, rupee
risk, position percentage, estimated entry costs, expiry, next entry date, days
left and reason. Gap fills require the trade engine and order writer to recheck
size, cash, heat and eligibility using the actual fill. Persisting a proposal does
not reserve funds at a broker; atomic account/order execution belongs to the
orchestrator and paper-execution phases.

## Explicit scoring formulas

Weights retain the specification's proposed 25/20/20/15/10/10 split. The document
did not prescribe numeric normalization formulas; these starting formulas are
now explicit in code/config, to be tested and tuned through the research matrix.
They are not a profitability claim.

| Component | Measurement |
| --- | --- |
| Rally, 25 | Equal contributions from velocity normalized to 2%/session and volume ratio normalized to 3×; each capped at 100%. |
| Pullback, 20 | Equal contributions from shallow signal-close depth below EMA21 in ATR units, current price above DMA50, rising EMA21 over five sessions, and signal close location within its candle range. Depth contribution reaches zero at one ATR. |
| Fundamental, 20 | A score plus a proposed 10-point bonus when a Stage-3 trigger is present, capped at 100 before weighting. |
| Relative strength, 15 | Mean of 63- and 126-session stock returns relative to Nifty 500, ranked across this run's measurable live-signal/watchlist cross section. Exact aligned exchange dates are required. |
| High proximity, 10 | `max(0, 100 − 2 × distance-from-252-session-high%)`. |
| Sector, 10 | Median combined relative strength per represented sector, then cross-sector percentile rank. |

Ties receive averaged percentile ranks; a one-item cross section is neutral 50.
The watchlist is ordered by absolute distance to EMA21 in ATR units, then score
and instrument ID. It carries readiness measures but creates no entry order.

## Account, regime and persistence contracts

There is no implicit empty ₹1 crore account. A current PAPER snapshot is required,
with finite positive NAV and cash, reconciled position quantities and available
protective stops. Recompute open and pending risk from mode-linked strategy
trades. Include pending broker buys and not-yet-submitted strategy entries,
reserving cash including costs without double-counting the slot. Unknown mode,
unknown stop, duplicate linked orders, inconsistent quantities, historical mutable
rows updated after the requested timestamp, and missing sectors all block new
allocation. PAPER_SANDBOX/LIVE positions do not silently become PAPER holdings.

Module E must provide a matching-version regime for the latest completed market
session. Missing, mismatched or invalid records block entries; a hard override
forces capacity zero. The market-close worker now writes the account snapshot
before asking C to rank it. Historical account/regime vintages still need a
separate audit; this implementation does not reconstruct unknown past accounts.

Numeric rankings remain in `module_c_rankings`. Full per-stock order-sheet,
watchlist and rejection records use `strategy_decisions` under module C. Writes
are serialized by a transaction-scoped advisory lock per date/version. Reruns
update records without duplication and invalidate vanished selections/details,
including empty reruns. Stale records are marked rejected rather than deleted.

## Validation and current blockers

See `module_c_validation_2026-10-18.json`. Official Nifty 500 history was imported
for 2026-01-01 through 2026-10-16: 196 sessions, zero missing against Nifty 50.
This establishes current ranking coverage, not a complete pre-2026 backfill.

The full stored-A replay evaluates 2,302 stocks, all rejected by A, and selects
zero. Actual read-only database queries independently confirm no regime or
portfolio snapshot exists. Tests exercise successful synthetic ranking/allocation
paths; PostgreSQL rollback assertions verify idempotency, empty-rerun invalidation
and replacement of rejection reasons. All synthetic DB rows were rolled back.

Upstream financial/policy gaps, Module E availability and a reconciled PAPER
account remain required for production qualification. No live or paper broker
order was submitted during this implementation.
