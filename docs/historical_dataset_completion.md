# Historical dataset completion and audit

Completion remains **BLOCKED**. Added audit/repair tools do not mean missing history has been obtained.

## Read-only live diagnostic, 21 October 2026

A single read-only transaction scanned the NSE historical master for 2018-01-01 through 2026-10-20. It executed 44 automated rules, with two Python/schema checks explicitly NOT_RUN and five independent reviews outstanding. Across all 51 entries: 20 PASS, 14 FAIL, 15 REVIEW_REQUIRED, 2 NOT_RUN. No source records were written.

| Finding | Count | Meaning |
| --- | ---: | --- |
| Financial quarter coverage | 64,707 | Company/quarter buckets lacking a quarterly filing, including warmup; fiscal exceptions require review |
| Annual OCF/PAT coverage | 18,422 | Company/year buckets missing qualifying annual coverage, not a unique filing count |
| Shareholding quarter coverage | 28,030 | Company/quarter buckets lacking a shareholding filing |
| V2 extraction provenance | 1,504 | XBRL filings lacking corrected parser evidence within cutoff |
| Fundamental presence | 2,304 | Companies without any loaded knowable financial facts |
| Price session coverage | 2,620 | Companies with at least one missing/invalid/source/warmup issue, not missing-candle count |
| Benchmark sessions | 1,982 | Missing benchmark/date pairs relative to loaded Nifty50 sessions |
| Corporate action values | 16,359 | Rows requiring original date/ratio/cash validation |

There are 2,620 candidate NSE master rows and only one inactive master row. This does not establish survivorship completeness. Loaded financial periods begin at 2022-06-30. Calendar, source vintages and missing/delisted-security inventories still need independent reconciliation. Counts identify review conditions, not automatically confirmed corrupt rows.

## Added behavior

- 45 automated source rules, schema preflight and five independent reviews; annual/quarter duplicate identity includes filing kind.
- A prioritized repair plan accompanies the standard JSON/CSV audit output. Errors and unexecuted checks cannot become completion evidence.
- Connector-only diagnostic SQL executes in one read-only transaction and explicitly identifies its two unrun validators; it cannot certify a complete audit.
- Financial discovery traverses all pages with bounded work and rejects repeated pages, page-cap exhaustion and malformed source rows. Backfill requires ingestion V2 and migration 012 before source writes, retains annual companions, and stops on missing/unfetchable XBRL instead of silently omitting it.
- Historical backfill no longer writes shared Module E regime decisions. Each isolated research run computes its own regime.
- Parquet exports now include instrument master, benchmarks, sectors, shares history, provenance and unresolved DQ, along with trading warmup and aligned knowable financial filing/fact vintages. All tables are read from one repeatable-read snapshot. Nested metadata is preserved as JSON text. Exports are explicitly NOT_CERTIFIED and do not attest completeness.
- A manual GitHub workflow runs the full auditor and preserves failure reports and repair plans. Database connections are read-only at transaction level.

## Repair and verification order

1. Merge ingestion #44 and policy #45, then execution #46, command unification #47 and this audit change in dependency order. Apply migration 012 to the correct writable financial table after review.
2. Reconcile historical listings, delistings, symbol identities and the independent official calendar. Repair exact price/benchmark dates using original source artifacts.
3. Load original quarterly financial/shareholding vintages and annual cash-flow statements, with publication times. Reparse source XBRL through V2. Do not infer financial history from today's values.
4. Reconcile corporate actions, sector/index intervals, surveillance circular inventories, original source hashes and earnings announcement archives. Do not invent missing timing or assume absence of a row proves absence of an event.
5. Run the full Python audit, resolve independent reviews, and rerun isolated research with fresh dataset/code identities. Existing backtests and exports are retained; they are not relabelled as repaired evidence.

```bash
python scripts/run_data_integrity_audit.py --plan
python scripts/run_data_integrity_audit.py --run --start 2018-01-01 \
  --end 2026-10-20 --report artifacts/historical-dataset/audit.json
```

Choose a new report path on each invocation. `PMS historical dataset audit` exposes the same complete read-only scan through GitHub Actions. The live connector diagnostic is useful evidence of gaps but does not replace the full Python validators or independent source reviews. Missing source archives remain work to be completed; no blanket historical import or migration was executed in this change.
