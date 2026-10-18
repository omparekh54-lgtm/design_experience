# PMS Operations — Phases 22–31

## Execution safety status

Unattended live auto-execution is intentionally disabled. Supported operating stages are:

- `PAPER`
- `LIVE_VALIDATION`
- `LIVE_APPROVAL`

`LIVE_AUTO` is rejected by both the safety controller and production-readiness controller.

Apply these migrations before running the operations layer:

```text
migrations/008_execution_operations.sql
migrations/009_validation_go_live.sql
```

## Install worker dependencies

```bash
pip install -r requirements-ops.txt
```

Set:

```text
DATABASE_URL=...
REDIS_URL=redis://localhost:6379/0
EXECUTION_MODE=PAPER
```

Angel One approval-only adapter additionally requires:

```text
ANGEL_API_KEY
ANGEL_CLIENT_ID
ANGEL_PASSWORD
ANGEL_TOTP_SECRET
```

Broker order actions should run only from the static IP registered for the Angel API key.

## Phase 22 — workers / scheduler

Start worker:

```bash
celery -A packages.pms.workers.celery_app worker --loglevel=INFO
```

Start scheduler:

```bash
celery -A packages.pms.workers.celery_app beat --loglevel=INFO
```

Operational roles:

- `market_worker` — end-of-day Module E + portfolio snapshot
- `financial_worker` — Module A refresh tasks
- `surveillance_worker` — surveillance freshness guard
- `research_worker` — daily research/portfolio snapshot + paper validation
- `execution_worker` — broker reconciliation + readiness refresh

The India-time beat schedule runs after market close and never promotes execution mode or submits unattended live orders.

## Phase 23 — paper broker

`PaperBroker` persists orders/fills and updates the portfolio ledger. `PaperExecutionService` processes market/limit/stop orders against supplied market bars, including gap-aware stop fills and the configured transaction-cost model.

## Phase 24 — portfolio accounting

`PortfolioAccountingService` calculates and persists cash, market value, realised/unrealised P&L, NAV, open risk, portfolio heat and per-position quantities/prices.

## Phase 25 — Angel One adapter

`AngelOneBroker` exposes order-book, positions, RMS cash, place and cancel methods using SmartAPI. Placement is rejected unless execution mode is `LIVE_APPROVAL`, new orders are enabled, emergency stop is off and no execution lock exists.

There is no public live-place-order API and `LIVE_AUTO` is not supported.

## Phase 26 — reconciliation

`ReconciliationService` compares internal orders/positions/cash with broker state. Any mismatch locks new execution automatically and persists the discrepancy in `reconciliation_runs`.

## Phase 27 — kill switch / safety

Automatic or operator locks cover reconciliation mismatch, stale market data, surveillance-data staleness and worker/reconciliation exceptions. Emergency stop disables new orders and persists an audit event.

Mode changes always leave new orders disabled. Enabling new orders is a separate operator action:

```bash
python scripts/operations.py enable-orders
python scripts/operations.py disable-orders
```

## Phase 28 — complete audit trail

`AuditTrail` writes structured events with actor, entity, correlation ID, strategy/data versions and before/after state. Migration 009 also adds database triggers for operational tables so worker-originated changes cannot bypass auditing.

Audited entities include broker orders/fills, portfolio ledger/snapshots, reconciliation, workers, safety state, paper validation, live-validation sessions/proposals/observations and production readiness.

Inspect audit history:

```bash
python scripts/operations.py audit --limit 50
python scripts/operations.py audit --entity-type live_order_approval --entity-id <id>
```

## Phase 29 — paper-trading validation

`PaperValidationService` generates a persisted pass/fail report from actual paper evidence:

- minimum calendar duration
- minimum paper fills
- minimum NAV snapshots
- zero reconciliation mismatches by default
- zero recent worker failures by default
- zero unresolved data-quality errors by default
- configurable maximum drawdown

The default thresholds are implementation defaults, not original strategy rules, and can be changed before production use.

Run manually:

```bash
python scripts/operations.py paper-validate --start 2026-08-01 --end 2026-10-16
```

A 60-day report is also generated weekly by the scheduler. It records evidence but never changes execution mode.

## Phase 30 — controlled small-capital live validation

`LIVE_VALIDATION` is an intermediate operating stage. A validation session defines hard limits:

- maximum validation capital
- maximum order notional
- maximum open positions
- maximum daily loss
- optional symbol allowlist

The service only creates and approves **order proposals**. It does not submit those proposals to Angel automatically. Any actual broker action remains a separate manual operator action through the approval-only broker boundary.

Example flow:

```bash
python scripts/operations.py mode LIVE_VALIDATION
python scripts/operations.py live-session-create --max-capital 100000 --max-order-value 25000 --max-open-positions 2 --max-daily-loss 1500 --symbols NSE:ABC,NSE:XYZ
python scripts/operations.py live-session-activate <session_id>
python scripts/operations.py enable-orders
python scripts/operations.py live-propose <session_id> NSE:ABC BUY 10 --estimated-price 1000 --reason validation_entry
python scripts/operations.py live-approve <approval_id>
```

Approval does **not** equal broker submission. After the separately executed real-world order, record the observed fill and reconciliation result:

```bash
python scripts/operations.py live-observe <approval_id> --expected-price 1000 --fill-price 1002 --quantity 10 --executed-at 2026-10-16T10:15:00+05:30 --fees 25 --external-order-reference BROKER-REF --reconciliation-ok
```

A completed session cannot contain unresolved proposals, approved proposals without execution evidence, or unreconciled observations.

Close only after evidence has been captured:

```bash
python scripts/operations.py live-session-close <session_id>
```

## Phase 31 — production readiness framework

`ProductionReadinessController` evaluates the path:

```text
PAPER -> LIVE_VALIDATION -> LIVE_APPROVAL
```

Promotion to `LIVE_VALIDATION` requires the latest paper validation to pass, latest reconciliation to be clean, no unresolved data-quality errors, no recent failed workers and an unlocked safety state.

Promotion to `LIVE_APPROVAL` additionally requires a completed live-validation session with at least five reconciled execution observations. Five is an implementation default and can be changed before production use.

The scheduler refreshes readiness daily but cannot promote the stage. Promotion is an explicit operator action:

```bash
python scripts/operations.py readiness
python scripts/operations.py promote LIVE_VALIDATION
python scripts/operations.py promote LIVE_APPROVAL
```

Every promotion disables new orders again; the operator must explicitly run `enable-orders` afterwards.

`LIVE_AUTO` has no supported promotion path.

## Operations CLI

```bash
python scripts/operations.py status
python scripts/operations.py mode PAPER
python scripts/operations.py enable-orders
python scripts/operations.py disable-orders
python scripts/operations.py emergency-stop --reason manual_review
python scripts/operations.py reconcile-paper
python scripts/operations.py snapshot --date 2026-10-16
python scripts/operations.py paper-validate --start 2026-08-01 --end 2026-10-16
python scripts/operations.py readiness
python scripts/operations.py audit --limit 50
```

## Production API

Run the extended API entrypoint:

```bash
uvicorn apps.api.main_exec:app --reload
```

Important execution/validation endpoints include:

- `GET /v1/execution/safety`
- `POST /v1/execution/emergency-stop`
- `POST /v1/execution/mode`
- `POST /v1/execution/enable-orders`
- `POST /v1/execution/disable-orders`
- `GET /v1/execution/reconciliation/latest`
- `POST /v1/execution/reconciliation/paper`
- `GET /v1/execution/portfolio-snapshot/{date}`
- `GET /v1/execution/workers`
- `GET /v1/execution/audit`
- `POST /v1/execution/paper-validation`
- `GET /v1/execution/paper-validation/latest`
- `GET /v1/execution/readiness`
- `POST /v1/execution/promote/{stage}`
- `POST /v1/execution/live-validation/session`
- `POST /v1/execution/live-validation/session/{id}/activate`
- `POST /v1/execution/live-validation/session/{id}/close`
- `POST /v1/execution/live-validation/session/{id}/proposal`
- `POST /v1/execution/live-validation/proposal/{id}/approve`
- `POST /v1/execution/live-validation/proposal/{id}/reject`
- `POST /v1/execution/live-validation/proposal/{id}/observation`

No endpoint submits a live-validation proposal directly to the broker.

### Phase-1 repair and verification

`python scripts/local_phase1_backfill.py --audit-only` performs a read-only audit.
After a previously completed GSM import, use
`python scripts/local_phase1_backfill.py --skip-legacy-gsm` to repair sectors and
benchmarks without re-reading the legacy circular archive. Without flags, the
original four-job sequence still runs.

Sector lookups discover active/suspended NSE series and require the detailed
quote's symbol and ISIN to match. Two verified exchange aliases (MANBRO →
KDGREEN and SILLYMONKS → CRESTO) are used for classification lookup only;
original instrument identifiers are retained. Current sector observations are
not backdated. Suspension provenance is retained and is not trading eligibility.

Benchmark requests use calendar months, persist each successful window, reject
invalid/out-of-window rows and compare dates against separately downloaded
Nifty 50 sessions (`NIFTY50` in `module_e_benchmark_bars`). This avoids both
false completion from min/max dates and false gaps caused by holiday candles
in equity feeds. The measured coverage percentage covers sectors and benchmark
sessions; GSM interval counts alone do not establish historical completeness.

Only `ASM_GSM_HISTORY_MISSING` can be automatically resolved, and only after
both measures have official legacy rows, recent open snapshots and no inverted
intervals. Other errors remain open and are printed with their IDs and details.
API health and acceptance checks count unresolved errors rather than all past
error records. Full GSM historical reconstruction remains a separate audit.

### Module B scanner

Run `python scripts/evaluate_module_b.py --all --refresh-calendar` for the
production-gated technical scan and a current official NSE holiday snapshot.
Then use `--all --persist` to save decisions and Setup-1 signals, or add
`--details` for every rejection. Module A is mandatory in production. Single-stock
`--research-only` diagnostics and `--setup 2` cannot persist production signals.
See [Module B rules and validation](docs/module_b.md) for calendar conventions,
price-source checks, signal expiry and current upstream data blockers.

### Shared trade engine

`python scripts/replay_trade_engine.py --demo` runs a synthetic, local-only
entry → partial exit → Leg-2 exit demonstration and prints the audit checkpoint.
`--input replay.json` resumes a supplied checkpoint with daily bars and explicit
A eligibility. Neither command connects to a broker. See
[trade engine rules and limits](docs/trade_engine.md) for session calendars,
conservative same-day stops, restart behavior and backtester integration.

### Module C readiness and order proposals

`python scripts/evaluate_module_c.py` runs a read-only after-close PAPER plan.
Use `--persist --details` to save all decision records. The output includes the
order sheet, watchlist, rejection counts and missing account/regime/data gates.
`python scripts/ingest_module_c_benchmark.py` refreshes the official Nifty 500
history used for three/six-month relative strength. See
[Module C scoring and account contracts](docs/module_c.md). The plan submits no
broker orders and must be revalidated at execution time.

### Module E market regime

`python scripts/evaluate_module_e.py` evaluates the latest completed session
without writes. `--details` prints all coverage failures. `--persist` saves the
regime/checkpoint and cancels or trims only formal PAPER pending buys to its
capacity; it never calls a live broker. Incomplete breadth data yields an explicit
UNAVAILABLE record with capacity zero. See [Module E contracts](docs/module_e.md)
for the score transforms, cold-start/upgrade rules and current data blockers.

## Daily orchestrator

The local command and Celery `pms.market_close` now use one durable PAPER workflow:
preflight → PAPER reconciliation → account snapshot → E → A/B/C → final report.
It persists stage results under `worker_runs.job_name='daily_orchestrator'`, blocks
overlapping runs, retries failed/blocked attempts, and withdraws stale same-day
PAPER proposals before a new attempt. It submits no orders or fills.

```powershell
python scripts/run_market_close.py --check
python scripts/run_market_close.py
python scripts/run_market_close.py --rerun
```

Run after data ingestion. `--check` is read-only; normal runs use today's IST date
only. Use direct PostgreSQL or the session pooler on 5432, not transaction pooling.
GitHub/Celery schedules are 19:00, 19:15 and 19:30 IST with exchange-calendar guards.
A configured schedule needs its secret/worker to be active. BLOCKED/FAILED exits
are nonzero; a successful repeat returns its saved report. No locks are cleared
or execution modes promoted. See [daily_orchestrator.md](docs/daily_orchestrator.md)
for recovery, reporting, scheduler prerequisites and validation limitations.
