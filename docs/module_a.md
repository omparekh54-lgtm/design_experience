# Module A: implementation and acceptance

Specification: `PMS_Rules_v1(1).docx`, Module A, pages 1–2. Strategy revision:
`PMS_v1.1.0_module_a`. This module does not place orders.

## Implemented behavior

- Non-financial NSE stocks only. Consolidated quarterly statements take priority;
  standalone is an explicit fallback. Mixed bases cannot pass growth comparisons.
- The latest knowable quarter is used. Future filings and revisions are excluded.
  Duplicate filings cannot count as additional quarters, and missing quarters
  cannot shift the prior-year comparator. TTM and record tests need eight
  consecutive quarters on the same basis.
- Stage 1 applies market cap above ₹1,000 crore, 63-session average daily traded
  value of at least ₹2 crore, no ASM/GSM membership, pledge at most 20%, quarterly
  sales of at least ₹50 crore and positive PAT. Market sources are deduplicated
  per session and intraday evaluation cannot use that day's closing bar.
- Stage 2 checks Sales/Operating Profit/PAT/Diluted EPS growth at 15/20/25/20%,
  positive Sales AND PAT growth in at least three of the last four quarters, and
  rising year-over-year TTM sales and PAT. Negative/zero prior bases do not
  manufacture percentage growth.
- Stage 3 emits acceleration, eight-quarter records, margin expansion and
  turnaround triggers. These affect scoring/earnings-breakout status, not basic
  membership. A turnaround trigger does not override failing Stage 1/2/4 checks.
- Stage 4 requires annual OCF/PAT ≥0.7, growing reported PBT and normalized
  pre-tax growth at least the PAT growth threshold. Normalized PBT is reported
  PBT minus signed exceptional items minus other income. Missing lines remain
  unknown, not zero. This is **pre-tax normalization**, not an assertion of
  exact after-tax adjusted PAT/EPS: item-specific tax allocation is unavailable.
  Reported diluted EPS is checked separately and dilution/tax distortion flags
  remain visible. Leverage and interest coverage use explicit sector policies.
- Debt/equity can use a knowable balance sheet when absent from the quarter.
  Zero interest is handled explicitly only for a known debt-free company.
- Eligibility expires exactly 100 days after the latest quarterly filing.
  A new failing result is effective immediately at its exchange timestamp.
- Score weights remain 30/25/15/15/15. Cyclical flags and a configurable soft
  valuation penalty are supported; valuation does not become a hard filter.
- Every snapshot carries its filing time, expiry, reason, failures, triggers,
  score components and reference inputs. Missing financial history produces an
  ineligible result rather than an exception or a missing stock in the scan.
- Missing/stale market/reference inputs, unverified surveillance coverage,
  suspended securities and unresolved relevant DQ errors fail closed.

## Policy and data limitations — intentionally explicit

The supplied document does not define numerical sector debt/equity or interest
coverage limits, nor a high-valuation cutoff. `fundamental.leverage_limits` in
`config/strategy_v1.yaml` therefore has null defaults and sector overrides.
Null leverage thresholds **block eligibility**; no investment-policy numbers
have been silently invented. A null valuation cutoff produces a disclosure flag
and no penalty. The five-point configurable valuation penalty is an implementation
starting default, not an owner-confirmed calibration.

The rule document's unspecified "small base" flag uses prior PAT below 10% of
current PAT as an explanatory heuristic. The existing scoring normalization is
also an implementation convention requiring later backtest calibration.

Operational defaults are 63 observed NSE sessions, a seven-day maximum price /
surveillance age, a 200-day shareholding-period age and a 550-day cash-flow /
balance-sheet age. These are explicit data-readiness defaults, not changes to
owner-confirmed investment thresholds.

Pre-2024 surveillance is unverified until its separate completeness audit passes.
`legacy_surveillance_verified` defaults to false. Current sector observations are
not backdated. Current master coverage is not a survivorship-free historical
universe certification. A complete scanner implementation does not establish
that the warehouse has complete fundamentals or that the strategy is profitable.

## Entry points

```powershell
# Read-only single-stock evaluation; current IST time by default.
python scripts/evaluate_module_a.py --instrument NSE:RELIANCE

# Save an explainable pass/fail decision for every active NSE stock.
python scripts/evaluate_module_a.py --all --persist

# Read-only historical evaluation with explicit local-market timestamp.
python scripts/evaluate_module_a.py --instrument NSE:RELIANCE --as-of 2026-10-16T18:00:00+05:30

# Use an explicitly configured policy without changing the implementation.
python scripts/evaluate_module_a.py --all --config config/strategy_v1.yaml --details
```

`--all` prints summary/rejection counts and eligible results; `--details` includes
all snapshots. Database credentials load from `.env`, and CLI connections use
`pms,public`. No manual market-cap/pledge overrides are accepted by the CLI.

`GET /v1/module-a/{instrument_id}?as_of=YYYY-MM-DD` evaluates from the database.
`GET /v1/module-a/eligible-list?as_of=YYYY-MM-DD` revalidates saved eligible
candidates at the requested time, including expiry and changed hard filters.
It does not discover new candidates until a refresh scan has run.
The raw `POST /v1/module-a/evaluate` endpoint is explicitly a calculator; it
cannot grant production eligibility.

NSE filing ingestion (`PostgresStorage.append_filings`) refreshes and saves
Module A after the financial transaction commits, at each filing timestamp.
`refresh_module_a=False` is available for explicit bulk import workflows that
will subsequently run the full scan. BSE ingestion is not incorrectly sent to
the NSE-only universe scanner. The financial-refresh worker saves decisions too.
The old manual-input history adapter delegates eligibility to this same path.

## Validation

`python -m pytest -q` covers rules, temporal boundaries, source deduplication
contracts, future revisions, accounting quality, missing data, sector overrides,
calculation-only API behavior and idempotent persistence. The real-universe
validation summary is committed beside this document. No live orders are sent.
