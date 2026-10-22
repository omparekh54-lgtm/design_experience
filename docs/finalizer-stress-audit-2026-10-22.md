# Step 1 and Step 10 hardening audit — 22 October 2026

Base reviewed: `55495ec`. Scope: historical data finalization and final matrix acceptance. PAPER/research mode only.

## Findings repaired

| Area | Failure before this change | Result after this change |
| --- | --- | --- |
| Step 1 audit resolution | A missing-data/evidence policy could hide an `ERROR` such as a SQL timeout. | Only executed `FAIL` / `REVIEW_REQUIRED` findings can be resolved. Errors, unknown statuses and unexecuted checks always block. |
| Certificate lifecycle | An interrupted or failed rerun could leave an earlier success certificate available. | Each attempt writes a non-success state first; failure retains a detailed repair inventory or writes a blocked state. Starting Step 1 invalidates dependent Step 10 acceptance. |
| Concurrent finalizers | Step 1 and Step 10 could alter related evidence concurrently. | Both commands share an OS-backed lock. Checkpoint quarantine also refuses to rename files owned by an active matrix process. |
| Historical price writes | `INSERT ... ON CONFLICT` targeted a logical price view in overlay deployments. | The reviewed CloudStore resolver writes to the appropriate physical table, including `cloud_daily_prices`. |
| Price resumability | One surviving NSE row plus a receipt could mark an entire day complete. | Resume checks the receipt's complete row count in the actual write target; new receipts additionally bind stored content. Conflicting multiple-series mappings and zero matched rows cannot be marked complete. |
| Financial resumability | A completion receipt could survive deletion of its filings/facts. | Stored filing counts are checked; new receipts bind filings and facts by a database-content digest. The latest receipt is authoritative. |
| Local checkpoints | Valid JSON with a scalar/array shape could crash resume code. | Malformed local structures are ignored; database evidence remains authoritative. |
| Prerequisite checks | Missing audit columns or unsupported writable-view layouts could fail after expensive downloads. | Full audit schema and reviewed write targets are checked before network backfills. Step 10 checks its research storage contract before replay. |
| Step 1 audit identity | A complete-looking report or changed dataset could be associated with another audit scope. | Required audit codes, uniqueness, completion, scope and report checksum are validated. Pre-audit, audit-snapshot and post-audit inputs must agree. |
| Code/config identity | Finalizer/ingestor/migration changes were omitted from the research code fingerprint. | The fingerprint covers package code, all Python scripts, migration SQL and the base strategy configuration. Step 1 certificates bind this identity; Step 10 rejects stale identities. |
| Step 10 preflight | An empty or shortened response could satisfy `ready_cells == len(cells)`. | The exact full ordered cell plan, historical-master scope, check mode and explicit empty blocker lists are required. |
| Step 10 evidence | Summary statuses alone did not revalidate the declared plan and all journal bytes. | Both reports must match config/code/data/plan, pass row checksums and retain unchanged journals for every terminal-success cell. |
| Step 10 input drift | Data/code could change after preflight while replays were running. | Certificate/code/dataset identity is checked again before issuing final acceptance. Certificates record both report SHA-256 values. |
| Interrupted matrix process | The parent could stop while its replay child continued writing. | The child is terminated and reaped before the parent releases its finalization lock. |
| Matrix coverage | SQL NULL OHLC/volume values could escape the invalid-row count; coverage caching ignored adjusted-source policy. | NULL/non-finite/negative/invalid values block preflight. Coverage cache keys include dates and adjusted-source policy. |
| Sector point-in-time evidence | A present-day constituent snapshot was backdated to an inferred semiannual review boundary. | Historical coverage stops at reviewed source freshness. Current snapshots begin at observation date; intervening history remains unknown. The next repair replaces only the script-owned inferred current-snapshot projections, preserving source receipts. |
| Benchmark repair | Conflicting duplicate source dates were silently overwritten; invalid closes could be reused; a complete one-day window was unnecessarily fetched again. | Conflicting values fail closed, invalid closes are re-fetched, and a complete one-day window can resume. |
| Acceptance dates | Custom subperiods could be presented as final acceptance. | Both certificate workflows require the exact 2023-01-01 through 2024-12-31 matrix scope. Step 1's broader audit must contain it. |

No acceptance gates were removed. No historical observations were fabricated. Strategy eligibility, performance thresholds, execution permissions and live-trading switches were not enabled or relaxed.

## Verification

- Full Python suite: **1,118 passed, 13 skipped**.
- New finalizer adversarial suite: **86 tests**. Includes complete synthetic Step 1 orchestration, SQL-error/missing-check/input-drift failures, and two complete 28-cell synthetic matrix passes through the Step 10 finalizer. The synthetic runs test orchestration; they do not establish strategy performance or certify market data.
- Additional matrix-policy cache regression.
- PostgreSQL SQL verification: **12 assertions passed** in an isolated PGlite PostgreSQL engine using SQL compiled directly from the updated receipt functions. Cases cover partial price deletion, changed prices, restored prices, deleted financial facts, restored facts, deleted filings, malformed receipt counts and superseded receipts.
- Two persistent localhost PostgreSQL receipt tests were added to `tests/test_finalizer_receipts_postgres.py`. They use the existing `PMS_COPY_TEST_PG_URL` isolation harness and are among the skipped tests when that service is absent.
- The other skipped tests require services/credentials unavailable here. Skips are not claimed as passes.
- Python compile checks and `git diff --check` passed.
- Five baseline test failures were reproduced on unchanged `55495ec`: four benchmark doubles used the previous query/benchmark contract, and one SQLite fixture omitted the now-required instrument metadata column. Fixtures now reflect the current contracts; production guards were retained.
- Core test/runtime versions were aligned to the repository pins for the final suite: pytest 8.4.2, SQLAlchemy 2.0.44, FastAPI 0.118.0, Pydantic 2.12.0, pydantic-settings 2.11.0, HTTPX 0.28.1.

## What remains environment-dependent

This review did **not** run source downloads against NSE or run either finalizer against the user's Neon database. The Windows project directory and its database configuration are not available in this session. Network availability, existing historical repairs and real backtest results therefore remain to be verified locally.

The last user-verified prerequisites remain the starting checkpoint: 9,951 pre-2024 ASM rows, zero ambiguous master identities, and financial period identity PASS. This change does not require reapplying migration 012. It adds earlier schema/write-target validation rather than assuming every other prerequisite is valid.

Old certificates lack the expanded identity and must be recertified. Database receipts and valid checkpointed source work are preserved. Corrupted/missing data is re-fetched; an old local JSON file cannot override database evidence. Legacy receipts without content digests retain count validation and are still subject to the full audit; new receipts add content validation.

Sector rows after 2026-05-15 may now be absent where the previous implementation inferred history. This is intentional: current snapshots do not prove historical membership. Under the existing missing-sector policy, affected stocks must remain ineligible until dated evidence is available. The 2023–2024 matrix uses its dated historical scope.

## Next local run

```powershell
cd "C:\Users\Om Parekh\Downloads\DE"
.\.venv\Scripts\Activate.ps1
git pull --ff-only origin main
python scripts/finalize_step1.py
```

Run the following **only after** the new Step 1 certificate says `STEP1_COMPLETE`, `pending_repairs` is zero and `pending_tasks` is empty:

```powershell
python scripts/finalize_step10.py
```

Do not delete checkpoint directories to retry. If either command stops, preserve the exact console error and certificate. Resolve the reported repair/preflight/reproducibility failure; do not bypass it. Historical acceptance does not activate live trading or complete the elapsed-session paper campaign.
