# Daily PAPER orchestrator

Implementation: `DAILY_ORCHESTRATOR_v1.0`. Implements the after-close scheduling
and shared module flow in section 1 of `PMS_Rules_v1(1).docx`. CLI, GitHub Actions
and the Celery market-close task now call the same service. IPO Module D remains
parked. This phase produces decisions and proposals; formal paper order placement,
trade-engine checkpoints and observed-bar fills belong to the next execution phase.

## Run

From the repository root with the existing `.venv` activated and `.env` configured:

```powershell
git pull --ff-only origin main
python scripts/run_market_close.py --check
python scripts/run_market_close.py
```

`--check` is read-only: calendar, exact-date benchmark presence, equity-bar presence,
critical DQ errors and the current safety lock. It creates no journal, snapshots,
module decisions or orders. A READY preflight is **not** certification of complete
histories: E and A/B perform the detailed coverage and eligibility checks.

The default command persists the current IST day's decision workflow. It requires
a completed sourced exchange session; weekends and holidays do not replay Friday
as a new close. Explicitly ingested special weekend sessions are supported.
Unverified calendars block the run. A session before 15:30 IST is skipped.

`--as-of YYYY-MM-DD --check` inspects operational readiness against that date's
bars using current DQ/safety state; this is not a point-in-time historical audit.
Persisted runs require today's IST date to avoid overwriting historical accounts
with mutable present-day holdings. Use the backtester for historical simulations.
`--rerun` refreshes a previously successful current-day run; blocked and failed
attempts retry automatically without this flag.

## Ordered stages

| Stage | Behavior |
| --- | --- |
| Preflight | Require the exact session's official Nifty 50, Nifty 500, MidSmallcap 400 and India VIX rows, some current equity data, zero critical DQ errors, and an available unlocked safety record. |
| PAPER reconciliation | Compare the internal PAPER broker and ledger before account-dependent allocation. Mismatch stops the workflow. This is internal consistency, not independent live broker evidence. |
| PAPER snapshot | Persist current PAPER cash, NAV and holdings using configured initial capital. No LIVE or sandbox account is substituted. |
| Module E | Verify benchmark/breadth histories, persist the regime and apply existing formal PAPER pending-buy capacity controls. |
| Modules A/B/C | C's canonical scan refreshes A for the full universe, evaluates production B only on A-eligible stocks, ranks and allocates. This workflow also persists A and evaluated B decisions. No duplicated alternative scanner. |
| Final report | Save counts, configuration readiness, blockers, stage outcomes, timestamps and the run lineage. An unavailable E regime or C/global configuration blocker gives BLOCKED, not SUCCESS. |

A valid defensive regime with zero capacity can complete successfully with no new
entries. Missing data cannot. Per-stock A rejections are normal strategy results;
unconfigured A leverage policy is an operational blocker. No default policy is
invented to make the workflow pass.

Data ingestion is upstream of this service. GitHub still refreshes official E
benchmarks before running it. Local equity/financial/reference ingestion must
finish first, especially for sources that block cloud workers. Missing bars are
never filled with synthetic prices, and these commands do not complete backfills.

## Persistence, retry and overlap protection

The existing `worker_runs` table stores one row per attempt under job name
`daily_orchestrator`. No migration is needed. Every stage start and completion is
committed with its result. Run keys include IST date, PAPER mode, implementation
version and a hash of the full strategy configuration.

A dedicated PostgreSQL **session** advisory lock serializes daily runs across
CLI, Celery and GitHub, including different dates/configurations. A contending
caller returns BUSY without starting any stage. Use a direct connection or the
Supabase session pooler (port 5432); transaction pooler port 6543 is rejected.
The backend PID is checked at journal writes; a lost connection/session aborts
rather than silently continuing under a newly acquired connection. The connection
is explicitly unlocked, or invalidated if cleanup fails.

A repeat of an already successful key returns the saved report with `replayed`
set. FAILED/BLOCKED attempts create a new linked attempt and restart from
preflight, rather than skipping saved stages with stale account or market inputs.
An abandoned RUNNING attempt with no remaining lock is marked INTERRUPTED.
Existing module upserts make replay safe. Stage writes are not one giant DB
transaction; completed module output may survive a later failure, and the journal
is the authoritative completion gate.

Before a new attempt, existing same-day PAPER C selections are withdrawn. A
blocked/failed finish also withdraws selections. Their rows and reasons remain
available for audit. This prevents a previous successful day's rerun from leaving
an old order sheet selected after a new data failure. Broker orders/holdings are
not proposal rows; only E's existing PAPER pending-buy controls can cancel/trim
those. Downstream execution must require the latest matching successful daily run
and recheck account, eligibility, prices and capacity at actual entry time.

No broker orders are submitted, no trade is filled, and execution mode is never
promoted or unlocked. Exceptions store a stage and type without raw DB URLs,
credentials or SQL parameters. Inspect the stage diagnostics and source services
to investigate failures.

## Scheduling and visibility

GitHub Actions and Celery Beat are configured for **19:00, 19:15 and 19:30 IST**
every day; the sourced exchange calendar decides whether to run. The second and
third attempts recover late data or transient failures; successful keys return
their saved result. GitHub also serializes its own jobs and retains the JSON
report as a workflow artifact for 14 days. CLI exit codes are 0 for SUCCESS,
READY check or SKIPPED; 1 FAILED, 2 BLOCKED, 3 BUSY. A blocked Celery daily task
also surfaces as a failure, with its durable run ID in the error.

The duplicate standalone PAPER reconciliation and research-snapshot beat entries
were removed because this workflow performs those operations. Their tasks remain
available manually. Separate data-health/surveillance guards and research
validation/readiness tasks remain scheduled; none can automatically clear a lock
or promote execution.

Scheduling configuration requires `PMS_DATABASE_URL` in GitHub or a running Celery
worker/beat with database and Redis settings. This implementation does not certify
that a deployed scheduler has run. Use `.env` for local runs; install
`requirements-ops.txt` only when running Celery. No broker API key is needed for
this decision-only workflow.

## Validation and current blockers

See `daily_orchestrator_validation_2026-10-18.json` for tests and fresh database
preflight exports. The actual October 18 non-session is SKIPPED. October 16 has
all four exact-date official benchmark rows but only 100 equity instruments with
bars; the existing `reference_data_incomplete` safety lock blocks readiness.
The prior E audit also found 2,050 mature stocks missing complete recent history.
These conditions were preserved, not overridden.

PostgreSQL rollback checks execute compiled `RunJournal.save` SQL, verify journal
upserts/stages/status, withdraw stale PAPER proposals, preserve sandbox proposals,
and roll back all synthetic fixtures. A separate session-lock check confirms the
lock survives commit and explicitly unlocks. Controlled unit fixtures cover
contention, retry, release and backend changes. A concurrent connector experiment
was inconclusive because requests serialized; no full multi-process production
run or production-qualified selection is claimed.

## Paper-execution integration update

`DAILY_ORCHESTRATOR_v1.1` adds forward PAPER settlement before preflight and
idempotent admission after the successful decision journal. Successful replay
retries admission; it does not repeat fills. Decision success and admission
permission are reported separately. A/B/C receives eligible, unused Leg-2
re-entry candidates from persisted checkpoints and applies its usual ranking
and portfolio limits. See [paper_execution.md](paper_execution.md).
