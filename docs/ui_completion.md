# UI completion

The operator console now connects persisted strategy, account and operational evidence through the complete API entry point `apps.api.main_exec:app`.

## Implemented views

Overview includes actual PAPER NAV history, account and decision dates, E capacity, the selected C order sheet and durable daily stages. Signals supports dated search, selected/watchlist/rejected tabs, sizing, component scores, reasons and pagination. Research provides a paginated active NSE universe and dated A/B stock evaluations. Regime shows weighted components, raw/smoothed scores and coverage evidence.

Formal PAPER activity includes positions, orders, modeled fills, and individual lifecycle timelines. Manual orders are isolated at `/paper-trading/sandbox`. Backtests, stress studies, walk-forward studies, data health, readiness, validation, reconciliation, workers and audit history expose stored evidence and explicit empty/error states.

## Read and mutation boundaries

The new `/v1/console/*` routes only SELECT data. Overview and signals use persisted rankings; visiting them never starts a full strategy scan. Stock detail evaluates one instrument without persisting it. Readiness GET no longer writes an audit/readiness record. Formal trades exclude sandbox and mixed-account associations. Dates use IST and completed official sessions, while actual snapshot dates remain visible.

Manual sandbox create/process/cancel and operations disable/stop controls require an operator-entered key for each action. No frontend environment credential is automatically forwarded. Create carries a form idempotency key. API authorization and safety checks remain authoritative. Mutation responses use a same-origin JSON route with origin validation and operator-supplied credentials. Success feedback provides an explicit refresh button to reload saved evidence. Page links use ordinary browser navigation. The UI cannot clear locks, enable trading, promote modes or submit real-money orders.

## Verification

Python regression suite: 421 passed, one skipped. Eighteen console/API cases cover read-only behavior, bound filters, pagination, invalid dates, formal account isolation, unverified regime capacity and operator authorization. All 24 emitted dashboard SELECT statements were checked with EXPLAIN against the actual PMS schema. Production CI checks TypeScript, builds Next.js, and runs a separate Playwright API fixture covering all 12 pages, filters, pagination, authorization, empty/unavailable data and mobile overflow. Screenshot artifacts contain synthetic UI fixtures only.

## Acceptance limits

UI completion does not establish historical data completeness or complete the subsequent backtesting matrix, walk-forward testing, integrity/security audits, 2–3 month paper campaign or final acceptance. Missing values remain unavailable; no example NAV, positions, trades or campaign results are substituted in production. Existing new-entry data and safety locks remain active.

See `apps/web/README.md` for setup and route details.
