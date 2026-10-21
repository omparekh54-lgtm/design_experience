# Financial ingestion duration and context repair

FINANCIAL_XBRL_v2 fixes annual/quarter selection and segment contamination in the NSE/BSE financial ingestion path. A disclosure may produce two records with the same publication timestamp and revision: a quarter and a full year. Annual operating cash flow is paired with annual PAT, not the quarter ending on the same day.

## Selection rules

- Select exact period ends and explicit duration windows: 75 to 105 days for a quarter; 330 to 380 days for a year. These windows accommodate calendar variation and 52/53-week years. Other durations remain unavailable; no YTD subtraction or quarter annualisation is performed.
- Balance sheet debt/equity require an instant on the exact report end.
- Ignore segment, geography, share-class, typed and unsupported scenario contexts. Explicit standalone/consolidated dimensions must match the filing scope. Conflicting entity totals, entities or duration starts remain unavailable, independently of XML order.
- Prefer the declared specific revenue concept over generic total revenue. Require INR for monetary totals and INR/share for diluted EPS. Preserve negative profits and explicit zero values; never invent omitted income lines.
- Preserve the exchange timestamp/revision and original source payload. Offset timestamps are converted to India time before comparison; a later revision/dissemination timestamp cannot be backdated to an earlier broadcast.
- Each canonical field carries selected context/concept diagnostics under `raw_payload.financial_extraction`. Missing/ambiguous facts are not eligibility passes.

## Database and rollout

Migration `012_financial_period_identity.sql` changes only the writable filings identity index to include `filing_kind`. It supports a standalone table and the Neon `pms.financial_filings_local` overlay. It preserves all rows, IDs, facts and source views. It must be applied before the new writer starts. Index creation is transactional and takes a bounded table lock; schedule it after existing ingestion writers finish or pause them first.

1. Drain/pause old ingestion writers. Do not run the old and new writer versions simultaneously across the index change.
2. Apply migration 012 transactionally to the intended PMS database using its normal `pms,public` search path (or the standalone schema). Verify the seven-column unique index.
3. Deploy the new writer and resume collection. Quarter and annual companions are written in one transaction. Reprocessing clears stale canonical facts invalidated by the new parser, without deleting the source filing.
4. Collection and full-source preflight now use `CLOUD_FINANCIAL_XML_V2` receipts. Existing pending source records are reprocessed even if an older `CLOUD_FINANCIAL_XML` receipt exists. Revisions and both periods must commit before the new receipt is recorded. Failed records remain pending.
5. Refresh Module A after catch-up and inspect remaining missing financial fields. The parser repair does not certify multi-year completeness or define the owner's missing sector leverage policy.

The receipt change covers retained queue records and newly discovered filings. Older disclosures absent from the retained queue still require explicit historical re-ingestion. External exchange availability, historical universe completeness and original disclosure identity remain separate source audits.

## Validation

`tests/test_financial_ingestion_v2.py` exercises duration, dimensional scope, units, conflicts, timestamp provenance and the annual OCF/PAT calculation. `tests/test_financial_ingestion_postgres.py` runs against an isolated local/CI PostgreSQL service: both standalone and overlay layouts, idempotent migration, quarter/year coexistence, stale-fact removal, atomic rollback and source preservation. The PostgreSQL cases are included in the existing CI service job.

No production migration, historical data repair or eligibility unlock is implied by a passing parser test. Execution permissions are unchanged by this repair.
