# PMS Operations — Phases 22–27

## Execution safety status

Live auto-execution is intentionally disabled. Supported execution modes are:

- `PAPER`
- `LIVE_APPROVAL`

`LIVE_AUTO` is rejected by the safety controller.

Apply `migrations/008_execution_operations.sql` before running operations workers.

## Install worker dependencies

```bash
pip install -r requirements-ops.txt
```

Set:

```text
DATABASE_URL=...
REDIS_URL=redis://localhost:6379/0
```

Angel One live-approval adapter additionally requires:

```text
ANGEL_API_KEY
ANGEL_CLIENT_ID
ANGEL_PASSWORD
ANGEL_TOTP_SECRET
```

Order placement/modify/cancel should run only from the static IP registered for the Angel API key.

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
- `research_worker` — daily research/portfolio snapshot
- `execution_worker` — broker reconciliation

The default India-time beat schedule runs after market close and never places unattended live orders.

## Phase 23 — paper broker

`PaperBroker` persists orders/fills and updates the portfolio ledger. `PaperExecutionService` processes market/limit/stop orders against supplied market bars, including gap-aware stop fills and the configured transaction-cost model.

## Phase 24 — portfolio accounting

`PortfolioAccountingService` calculates and persists:

- cash
- market value
- realised P&L
- unrealised P&L
- NAV
- open risk
- portfolio heat
- per-position quantities / prices

## Phase 25 — Angel One adapter

`AngelOneBroker` exposes order-book, positions, RMS cash, place and cancel methods using SmartAPI. Placement is rejected unless all of the following are true:

1. execution mode is `LIVE_APPROVAL`
2. new orders are enabled
3. emergency stop is off
4. no execution lock reason exists

There is no public live-place-order API and `LIVE_AUTO` is not supported.

## Phase 26 — reconciliation

`ReconciliationService` compares system state with broker state. Any mismatch locks new execution automatically and persists the discrepancy in `reconciliation_runs`.

## Phase 27 — kill switch / safety

Automatic or operator locks cover reconciliation mismatch, stale market data, surveillance-data staleness and worker/reconciliation exceptions. The emergency stop disables new orders and persists an audit-log event.

Operations CLI:

```bash
python scripts/operations.py status
python scripts/operations.py mode PAPER
python scripts/operations.py emergency-stop --reason manual_review
python scripts/operations.py reconcile-paper
python scripts/operations.py snapshot --date 2026-10-16
```

Production API with execution safety routes:

```bash
uvicorn apps.api.main_exec:app --reload
```

Execution endpoints include:

- `GET /v1/execution/safety`
- `POST /v1/execution/emergency-stop`
- `POST /v1/execution/mode`
- `GET /v1/execution/reconciliation/latest`
- `POST /v1/execution/reconciliation/paper`
- `GET /v1/execution/portfolio-snapshot/{date}`
- `GET /v1/execution/workers`
