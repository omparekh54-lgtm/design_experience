# Cloud rollout Phase 1 — Deployment readiness

The target is unattended cloud PAPER operation with the laptop switched off. This assessment prepares that deployment; it does not start services, ingestion, migrations, orders or the campaign. Existing module implementation tests do not establish historical or forward performance.

## Current assessment (2026-10-19)

| Item | Observed status | Action / owner phase |
|---|---|---|
| Code baseline | Step 13 merged; API, frontend, daily PAPER lifecycle and safety guards implemented | Carry verified revision into release evidence |
| Database contract | Read-only inspection of 526 `pms` columns found no missing columns in the Step-12 contract | No rebuild; indexes, types, constraints, functions, grants and migration history still require deployment review |
| Migration files | Nine numbered SQL files, fingerprinted by assessment | Back up and compare actual DDL before selecting any migration; schema presence does not prove an applied migration ledger |
| Safety | PAPER, new orders disabled, `reference_data_incomplete` lock active | Investigate data blockers; no automated unlock |
| Critical DQ issues | Zero open ERROR issues observed | Full integrity/source review still required in Phase 4 |
| Strategy policy | Default debt/equity and interest-coverage limits unset | Owner supplies explicit limits; no invented thresholds |
| Source conventions | Historical surveillance verification and adjusted-price conventions need evidence | Phases 2 and 4; setting a flag is not verification |
| Daily cloud ingestion | Existing workflow refreshes benchmark/VIX data, then invokes daily decisions; it is not a full collector | Phase 2 must establish all required feeds and prove cloud source access |
| Scheduler | Workflow defines 19:00, 19:15, 19:30 IST attempts; delivery, secret availability and missed-run recovery unverified | Phase 3 selects one scheduler owner; Phase 5 adds monitoring; Phase 6 demonstrates actual sessions |
| Hosting inventory | Render connector reports no workspace selected; live service inventory not verified | Confirm target workspace before Phase 3; no services created or changed |
| Build reproducibility | `nse` is unpinned, Finstack uses `@main`, frontend has no committed lockfile | Freeze dependencies after verifying the target build; Phase 3 |
| Resources and cost | Full-universe cloud ingestion/replay runtime and memory unmeasured | Measure on target runner; free-first, no paid provisioning authorized |
| Historical/recovery evidence | Matrix, walk-forward, full audit and actual isolated restore remain pending | Phases 4 and 5 |

Result: **assessment implemented; deployment and campaign remain BLOCKED**. This is the expected handoff into Phase 2, not a failed implementation of Phase 1.

## Deployment units and boundaries

| Unit | Deployment contract | Persistence / dependencies |
|---|---|---|
| Frontend | `apps/web`; Node.js 22 is the tested CI baseline; `npm run build`, then `npm start -- --hostname 0.0.0.0 --port $PORT` | Server-rendered Next.js requires a compatible runtime; a static HTML upload is insufficient. Server-side `PMS_API_BASE_URL` points to deployed HTTPS API |
| API | Python 3.12 CI baseline; `uvicorn apps.api.main_exec:app --host 0.0.0.0 --port $PORT` | PostgreSQL; `/health` liveness, `/ready` DB readiness; neither authorizes trading |
| Data collector | Complete cloud collection chain to be implemented/verified in Phase 2 | Prices, disclosures, ownership/share counts, corporate actions, listing/sector/index history, surveillance, exchange sessions, benchmarks and VIX as required by modules |
| Daily batch | `python scripts/run_market_close.py`, after successful data refresh; current IST session only | Durable database journals, paper accounts and reconciliation; no late fabricated fills |
| Optional worker service | Celery worker/beat only if selected architecture needs it | Protected Redis and one scheduler owner; do not run duplicate independent beat/batch schedules |
| Evidence/backup storage | External durable storage, retention covering the campaign and final audit | Ephemeral web disks and the existing 14-day workflow artifacts are insufficient for the full campaign |

Do not choose a web instance size by guessing. Measure peak RAM, CPU, collection/decision duration, database connections/storage, archive size, provider quotas and delivery latency. The existing scheduled job has a 45-minute timeout and three daily attempts. Compare measurements with current provider limits before committing to an architecture. A sleeping web service does not replace a dependable scheduler. Free-tier feasibility is **unverified**, not promised.

## Environment contract

Separate secrets by service. Never embed credentials in plans, committed files or frontend public variables.

| Variable / configuration | Scope | Requirement |
|---|---|---|
| `DATABASE_URL` or `PMS_DATABASE_URL` | API and jobs | Actual cloud PostgreSQL connection, dedicated least-privilege role; explicit `sslmode=verify-full` with trusted CA for deployment readiness. Current online helper minimum is encrypted TLS; this deployment gate is stricter |
| GitHub secret `PMS_DATABASE_URL` | Existing scheduled workflow, if retained | Presence/delivery must be tested privately; never print it |
| `OPS_API_KEY` | API | Random private 32–256 character key; operator supplies it per action. Length check does not establish randomness |
| `PMS_API_BASE_URL` | Frontend server | Deployed HTTPS API origin; no username, password, query secrets or fragment |
| `EXECUTION_MODE=PAPER`, `LIVE_TRADING_ENABLED=false` | API and jobs | Explicit paper-only configuration; database safety remains authoritative |
| `RUN_REFERENCE_GAP_BACKFILL=0` | API | No startup backfill; collection belongs in explicit scheduled jobs |
| `PMS_ALLOW_PRIVATE_HTTP=0` | Frontend server | Require HTTPS for cloud operator transport |
| `REDIS_URL` | Optional Celery workers | Only needed if architecture selects queue workers; protect network/auth/TLS |
| `PMS_RESTORE_TEST_URL` | Isolated recovery job only | Separate empty disposable database, never the populated source |

Provider-specific secrets and alert destinations will be configured in the responsible later phases. No Angel One key is required.

## Assessment commands (for deployment automation, not daily laptop use)

```bash
# Offline inventory; no dotenv load or connection; exit 0 means plan produced.
python scripts/check_deployment_readiness.py --plan

# Inspect selected runtime environment without making a connection.
python scripts/check_deployment_readiness.py --check

# Explicit read-only database transaction; never applies migrations.
python scripts/check_deployment_readiness.py --check --database \
  --output artifacts/deployment-readiness-UNIQUE.json
```

`--check` currently exits **2** because cloud ingestion, hosting, historical validation, recovery and unattended rehearsal remain unverified. Individual configuration checks can pass without granting campaign readiness. The report includes UTC creation time, code revision/dirty state, strategy/input hashes, migration file hashes and bounded schema/safety/DQ results. Database errors expose only exception type, not credentials, SQL or arbitrary stored lock text. Reports refuse overwrite. Plan generation is included in CI and stored as `pms-deployment-readiness`.

The schema probe is the existing Step-12 column-presence contract, not a migration engine or full DDL validator. It stops before querying dependent tables if required columns are missing. Database inspection runs in a PostgreSQL READ ONLY transaction. No DQ resolution, safety update, grant, migration or repair is attempted.

## Consolidated deployment sequence

1. Complete Phase 2: full cloud collection and source-access evidence. Keep existing source database and backfill intact.
2. Complete Phase 3: select confirmed hosting workspace and free-tier architecture; pin tested dependencies, configure secrets, durable storage and one scheduler. Back up and apply only individually reviewed necessary schema changes.
3. Complete Phase 4: run pending historical calculations, matrix, walk-forward and full integrity/source reviews on a suitable cloud research runner. Choose resource limits using measurements; research need not compete with daily jobs.
4. Complete Phase 5: demonstrate access controls, monitoring/alerts, safe failures and isolated restore with matching manifest.
5. Complete Phase 6: demonstrate complete unattended market sessions; resolve critical failures and freeze strategy/data conventions.
6. Start Phase 7 / Step 14 only after explicit PAPER permissions and all admission gates are satisfied. Record actual start date and version. Routine jobs require no laptop; exceptional blockers require attention.
7. Perform Phase 8 / Step 15 final acceptance after the actual 2–3 month campaign. Checks can be implemented beforehand; no advance PASS or automatic live promotion.

Operational deployment supersedes the earlier local-only setup plan. The old checklist remains a manual fallback, not the daily operating model. No laptop command is requested during this implementation phase.
