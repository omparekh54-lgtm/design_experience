# Cloud rollout Phase 2 — Automated official collection

The collector is implemented for unattended cloud jobs. It neither starts a paper campaign nor enables orders. Deployment activation, the initial full-universe catch-up, runtime measurement and an actual consecutive-session rehearsal remain prerequisites in later phases.

## Collection chain

| Dataset | Official source and validation | Persistence |
|---|---|---|
| Exchange calendar | NSE holiday API; complete year required; special sessions require sourced confirmation | Existing calendar snapshots, preserving prior verified years |
| Instrument master | NSE `EQUITY_L.csv`; unique symbols, listing dates and ISIN checks | Existing instruments; no automatic delisting from absence; ISIN changes block for review |
| Raw daily prices | CM UDiFF ZIP; exact filename/date, finite OHLCV and actual rupee turnover | `NSE_UDIFF`; recent seven-calendar-day repair window; no adjusted Yahoo substitution |
| Benchmarks/VIX | Daily index archive; Nifty 50, Nifty 500, MidSmallcap 400 and India VIX all required | `NSE_DAILY_INDEX`, distinct from historical API provenance; readers accept both official sources and deduplicate sessions |
| Surveillance/suspension | Both REG_IND and REG1_IND files; schema/indicator agreement and universe coverage required | Dated restrictions and observed suspension status; GSM zero remains active, 100 means inactive |
| Financials | Integrated filing API with checked one-based pagination, total counts, publication timestamps and XBRL | Incremental record receipts; never silently drop unparseable records; missing canonical facts block completion |
| Ownership/share counts | Latest official shareholding list plus XBRL | Entity/period/aggregate-context validation; pledge denominator is promoter shares; share-count history keeps its filing timestamp |
| Earnings | Official board-meeting notices with announcement timestamps | Conservative blackouts; changed/cancelled notices never silently erase earlier evidence |
| Corporate actions | Official corporate-action API; parsed ratios and known announcement time | Preserve originals; if announcement time is missing, use observation time, never backdate to ex-date |
| Nifty 500 membership | Official constituent CSV with identity checks | Observed snapshot becomes effective no earlier than the next calendar date; it does not reconstruct past membership |
| Sectors | NSE Next API metadata plus detailed quote; discover actual EQ/BE/BZ/SM/ST series and match ISIN | Bounded rotation starts after 60 days; missing/over-90-day classifications block completion. Historical changes are not backdated |

The initial latest-quarter XML backlog can require multiple runs. Default budgets are 200 financial and 200 ownership documents per attempt, plus up to 100 classification refreshes. Successfully committed records are checkpointed; remaining or failed required records produce `PARTIAL`, not success. Unsupported XBRL taxonomies, identity events and missing archive dates stay visible blockers. This phase does not assert every instrument's taxonomy has already been validated.

Discovered records enter a durable database queue before XML processing, so a failed record is not forgotten when the API's rolling window or reporting quarter changes. Financial discovery uses a completion watermark (30-day initial scan, seven-day overlap thereafter, wider recovery after an outage); a gap beyond 90 days requires explicit backfill. Receipts advance discovery only after all pages are validated and discoveries are queued. Parsing failure does not acknowledge a record.

HTTP requests use verified HTTPS, restricted official hosts (including redirects), bounded retries for transient failures, a 20 MiB document limit, 256 MiB aggregate download budget and 25-minute collection deadline. XML bodies are released after parsing. Actual peak memory and full-universe runtime still need measurement on the deployment runner.

## Existing database compatibility

The current backfill uses views for prices, financials, earnings, share counts and index membership. Generic INSERTs into these views are invalid. The collector inspects relation kinds and routes writes only to their integrated local tables; standalone physical PMS tables are also supported.

**Migration `010_cloud_collection.sql` is prepared, not applied.** After backing up and inspecting the populated database, Phase 3 must apply it as one transaction with `search_path=pms,public`. It adds a separate `cloud_daily_prices` table and extends the existing price view with `UNION ALL`, preserving the external market-bar backfill. It checks the view's exact column order, is repeatable, and fails on an unexpected relation contract. No drop/rebuild or external-source rewrite is required. The collector refuses an unintegrated writable layout before ingestion.

The new table has restrictive public privileges and RLS enabled. Review dedicated application-role grants/policies during deployment; owner-role success is not least-privilege verification. Never blindly replay migrations 001–009. Future membership/sector rows owned by this collector can be replaced before their effective date; past history and source receipts are preserved. Current constituent receipts retain their full member list.

## Scheduling and PAPER boundaries

The GitHub workflow now defines **19:07, 19:27 and 19:47 IST** attempts. It is disabled until Phase 3 configures the repository variable `PMS_CLOUD_COLLECTION_ENABLED=true` and the private secret `PMS_DATABASE_URL`. An enabled job without its secret fails visibly. Disabled runs emit an explicit warning and do not imply collection success. No activation variable or secret was changed during implementation.

`PMS_CLOUD_PAPER_ENABLED=true` is a separate activation, after the required gates and explicit PAPER permissions. Set `PMS_CLOUD_COLLECTION_REQUIRED=1` on every deployed API, worker and daily runner. The workflow sets it already. This rejects stale, absent, running, partial or failed collection journals before daily evaluation and before formal PAPER admission/pending BUY processing. The latest same-day successful collection must be no older than two hours. Repeated cloud daily runs recompute rather than admitting from a cached previous success with outdated inputs.

Protective settlement can still run following a failed collection, while new entries remain blocked. Failure remains a nonzero workflow outcome even if the PAPER lifecycle also runs. Existing stops/locks are never cleared. Use one scheduler owner: do not also activate independent Celery beat schedules for the same job. Collection and PAPER use their own database advisory locks plus workflow concurrency protection.

GitHub schedules are best effort and may be delayed or dropped; three attempts do not guarantee delivery. Phase 5 must provide independent missed-run detection, alerts and recovery procedures. If a session is missed, the collector repairs recent data but does not manufacture past forward paper fills.

## Commands used by deployment automation

```bash
python scripts/run_cloud_collection.py --plan
python scripts/run_cloud_collection.py --probe --output artifacts/cloud-source-probe-UNIQUE.json
python scripts/run_cloud_collection.py --run --output artifacts/cloud-run-UNIQUE.json
```

Plan mode has no network/database side effects. Probe mode makes official requests with **no database or trading secrets**, validates one completed date and samples one financial XML, one ownership XML and one classification. `--date YYYY-MM-DD` is available only for probes. A successful probe is source-access/parser evidence, not a full-universe write, freshness certificate or migration test. `--run` ingests today's completed IST session and fails closed on missing prerequisites. Reports refuse overwrite.

Normal reports return `SUCCESS`, `PARTIAL`, `FAILED`, `BUSY` or `SKIPPED` with hashes, public source URLs, observation timestamps, row counts and stable error codes. They never return database passwords or raw exception messages. `SUCCESS` means the implemented collection gates passed; it is not strategy, recovery or campaign acceptance. Initial backlogs, unsupported taxonomies, incomplete historical data and unresolved identity changes require resolution before rehearsal.

## Evidence and remaining deployment work

A no-write probe observed 2,619 master records, 3,398 parsed equity bars, 3,201 surveillance rows, all four benchmark codes and 501 Nifty 500 members for the chosen 2026-10-16 market session. It also paged recent financial discovery and parsed sampled financial/shareholding XML and classification. Reference lists are current observations, not reconstructed snapshots for October 16. Their exact counts can change as notices arrive. The legacy quote endpoint returned 403; the implemented Next API alternative passed the local probe.

CI runs an independent GitHub-hosted no-secret probe and preserves its actual result as `pms-cloud-source-probe`. External access failures do not pretend to be unit-test failures or verified collection; inspect the report's status even if the CI job is green. A non-session probe reports `SKIPPED` and does not verify all sources. Deployment must prove full-universe writes and freshness on the selected runner.

Scheduled JSON reports are retained as workflow artifacts for 90 days where account limits permit. **Private durable raw-source archival is not configured by this phase.** Reports explicitly disclose that gap. Phase 3 must provision suitable private storage/retention, and Phase 5 must verify backups and restore. Report hashes alone cannot reconstruct a source that later changes. Do not upload raw shareholding XML containing personal identifiers to public artifacts.

No production migration, cloud service, activation secret, safety permission or campaign start was changed. Next: Phase 3 deployment, including migration review, configuration and actual catch-up verification.
