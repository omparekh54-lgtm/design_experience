# Data integrity audit — Step 12

This phase implements the audit runner and saved evidence views. It does not imply that the user's populated laptop database has passed the audit: the user is deferring local commands until the consolidated end-of-build setup. No existing backfill is overwritten.

## Design

An audit is a dated, read-only scan over the historical NSE master, including inactive companies whose listing intervals overlap the requested period. Optional canonical symbols make an explicitly labelled sample. An explicit completed-date end is required; an audit never quietly substitutes today or a partial market session.

The design contains a schema-contract check, **39 automated rules**, and five mandatory independent source reviews. Missing schema fields prevent dependent checks from running. An empty universe fails. A partial `--checks` request leaves the omitted rules `NOT_RUN`; it cannot produce a clean audit result.

| Group | Automated evidence |
|---|---|
| Universe | Canonical identity, listing dates, bar/listing bounds, inactive historical membership |
| Prices | Null/nonfinite/invalid OHLC, negative volume/turnover, provider duplicates/conflicts |
| Coverage | Historical sessions, 257-session warmup, stock-session gaps, exact official benchmark/VIX dates |
| Financials | Filing revisions/scope identities, filing/publication chronology, orphan/duplicate facts, missing data, finite values and unit conventions |
| References | Effective intervals, overlaps, dated sector-session gaps, positive known shares, ASM/GSM presence |
| Actions/events | Ex-date/open knowability, split/bonus ratios, dividend amounts, conflicting/unsupported actions, earnings announcement timestamps |
| Decisions | Safe timezone-aware parsing of saved Module A filing timestamps versus the decision's as-of timestamp |
| Provenance/quality | Provider source URI/hash presence and open critical DQ issues |

Negative profits are valid observations. A missing value is never converted to zero. Cross-provider price differences, reference overlaps and unsupported action types require review; they are not automatically proof of corrupt records. Close-only benchmark observations are allowed when the original source supplies no OHLC. Financial unit conventions are checked against the existing ingestion contract: monetary amounts in INR crore, EPS in INR/share. Generic INR labels or other scales require source reconciliation; the audit never guesses a conversion.

Price/source selection and the raw/adjusted source allowlist use the same coverage policy as the matrix. Expected sessions are based on official Nifty50 observations, with exact missing-date comparisons for Nifty500, MidSmallcap400 and India VIX. This denominator cannot detect a date missing from every benchmark; independent calendar reconciliation is therefore mandatory. Historical listing-date and indicator warmup requirements can expose additional data work despite a successful Phase-1 fetch audit.

## Five independent reviews

The following always remain `REVIEW_REQUIRED` in this automated runner. It does not accept a boolean, row count or text attestation as proof that they are complete:

1. Independent exchange calendar inventory, holidays and special sessions.
2. Historical listing/delisting and symbol-change inventory, including companies absent from the loaded master.
3. Full ASM/GSM circular inventory and attachment reconciliation against reconstructed intervals.
4. Reference and financial publication vintages, including sector/index effective dates and revised filings.
5. Independent corporate-action, price-adjustment and share-count reconciliation, including fractional rights and delistings.

This explicitly carries forward the full GSM-history review that the Step-1 command did not certify. Presence of 1,871 intervals, zero open DQ errors, or all selected prices matching the same benchmark calendar does not clear these independent reviews.

## Status and limits

Each rule is `PASS`, `FAIL`, `ERROR`, `REVIEW_REQUIRED`, `NOT_APPLICABLE` or `NOT_RUN`. No saved Module A decisions means the decision-history check is `NOT_APPLICABLE`, not proof that historical decisions were valid. The observed-decision count is retained.

`finding_count` is the complete query count. Only the first 1–100 sample rows are stored (25 by default). Samples are illustrative, not the complete repair inventory; their order is not a ranking. Source records, raw provider payloads, SQL exception messages and database credentials are not dumped into reports.

Summary status is `INCOMPLETE` for interrupted/omitted/dependent-unrun checks; otherwise `FAILED` if any check fails or errors; otherwise `REVIEW_REQUIRED` when independent or source reviews remain. `automated_checks_passed` only means the requested full automated scan had no failures/errors. Full data certification remains false and strategy acceptance remains `NOT_CERTIFIED`, even when every automated query passes. Exit code 2 is expected while review requirements remain; it does not mean the report file failed to save.

The runner uses database-enforced repeatable-read/read-only transactions. Each rule has a savepoint and statement timeout so a bad query or timeout is reported as `ERROR` and later rules can continue. Manifest generation must complete before a fingerprinted full report is available; setup/manifest failures stop safely. Default per-rule timeout is 60 seconds, explicitly configurable within 1–600 seconds.

The input manifest hashes source rows, historical universe, extended calendar, operational A/B/C decision inputs and provider provenance. Schema field/type/nullability information, config, code and audit scope have separate fingerprints. The code fingerprint includes all three research CLIs. This identifies a snapshot, not independent truth of its contents.

JSON reports update atomically during scans and include a SHA256 report checksum. CSV summaries accompany the final JSON. Ctrl+C preserves any already saved partial report as `INCOMPLETE`. Each invocation uses a new path/run ID; an existing JSON path is rejected to preserve evidence. Re-running against new inputs is a new audit, not a repair or a continuation of stale results.

Optional `--persist` writes only an identity-guarded `DATA_INTEGRITY_AUDIT` record in the existing research table. It never writes source data, resolves DQ issues, updates policy configuration, modifies safety state, places orders or performs ingestion. No migration is introduced. The read-only list/detail API paginates saved checks and status filters; it does not start a scan. Data health links to these saved audit views.

Audit findings are research evidence for subsequent integrity remediation and acceptance review; this report does not automatically replace existing execution gates or certify that any paper/live readiness condition has been met.

## Deferred command reference

These commands belong to the consolidated setup at the end, not an instruction to run them now:

```bash
python scripts/run_data_integrity_audit.py --plan
python scripts/run_data_integrity_audit.py --run --start 2018-01-01 --end 2026-10-16 --persist
```

Choose the final completed-session cutoff during setup. `--symbols`, `--checks`, `--sample-limit`, `--timeout-ms`, `--config` and a new `--report` path explicitly change the scope. `DATABASE_URL` takes precedence over `PMS_DATABASE_URL`, matching the application and other research runners. Exit codes: 0 for fully clean automated status with no reviews, 2 for failed/incomplete/review-required results or setup failure, 130 for interruption. Independent mandatory reviews mean this version normally returns 2 even when the automated scan is clean.

After implementation, the actual full populated-database scan, remediation, independent source reconciliation, historical performance runs and acceptance review still have to happen. A code test pass is not a data audit pass.
