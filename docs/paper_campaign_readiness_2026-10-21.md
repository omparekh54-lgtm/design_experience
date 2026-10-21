# Forward PAPER campaign readiness — 21 October 2026

**BLOCKED. Campaign not activated; elapsed campaign sessions: 0.**

User authorization is conditional on readiness passing. The existing `reference_data_incomplete` safety lock remains and new orders remain disabled. No historical sessions, catch-up runs, simulated trades or date ranges were counted as elapsed forward evidence.

## Completed prerequisites

- Confirmed successful GitHub CI for PRs #44–#48 and merged all five in dependency order, retargeting stacked PRs to main.
- Applied migration 012 transactionally before deploying V2 writers. Filing count before and after: **1,507**. The unique key now includes `filing_kind`; no filing values were rewritten.
- Verified the official calendar identifies 20 October as an exchange holiday, so its skipped collection is expected.
- Added a repeatable read-only campaign readiness CLI and explicit elapsed-session qualification rules.
- Corrected earnings timestamp validation to use the actual source document receipt. A legitimate announcement arriving during earlier collection stages no longer fails against the run-start clock. A timestamp after document receipt still fails.

## Live database verification

Read-only snapshot: **2026-10-21 15:32:28 UTC (21:02:28 IST)**. Latest completed exchange date requested: **21 October 2026**. A separate 19 October diagnostic was also checked.

| Check | Evidence | Result |
| --- | --- | --- |
| Execution mode | PAPER; emergency stop false | PASS |
| Financial uniqueness migration | Valid seven-column unique index | PASS |
| Critical DQ issue ledger | 0 open ERROR rows | PASS; not proof of complete inputs |
| Safety lock | `reference_data_incomplete`; orders disabled | BLOCKED |
| Dated sector coverage | 1,063 of 2,619 active NSE instruments lack current dated coverage | BLOCKED |
| Financial V2 evidence | 1,507 source XBRL filings need provenance/reprocessing verification | BLOCKED |
| Annual OCF presence | 50 companies have annual OCF facts | Presence only; not complete universe coverage |
| 21 October market data | 0 benchmark series; 0 stock price instruments | BLOCKED |
| 19 October market data | 4 benchmark series; 2,606 stock instruments | Presence only |
| Latest collection attempt | 19 October catch-up FAILED with `earnings_identity_or_timestamp_invalid` | BLOCKED; source-clock fix needs a successful new run |
| PAPER snapshots | 0 | No elapsed forward evidence |
| Deployment, scheduler, archive, recovery and rehearsal proof | Not established as a complete current launch packet | BLOCKED |

The historical dataset need not be fully reconstructed before forward observation begins, but the campaign's current source inputs, bounded universe, risk state, operating receipts and frozen configuration must be verified first. Zero recorded DQ errors or a successful unit test suite cannot waive missing source coverage or an active lock.

## Repeatable readiness check

```bash
python scripts/check_paper_campaign.py --as-of 2026-10-21 \
  --report artifacts/paper-campaign/readiness-2026-10-21.json
```

Use an explicitly completed exchange date and a new report path. The command executes one read-only input snapshot and exits 2 on blockers. It never clears locks, enables new orders, creates fills or writes an activation date. Operational proofs are deliberately not assumed by this CLI; they must be established from current verified deployment/recovery/rehearsal receipts before activation.

## Activation and elapsed-session evidence

Only after every readiness check passes may the campaign freeze its code/configuration, universe, starting capital and actual activation timestamp, then enable PAPER admission. Do not backdate activation. Activation after an exchange session has opened cannot claim that session as a fully elapsed campaign session.

The elapsed-session qualifier requires an official exchange session, a completed same-day after-close SUCCESS journal, matching frozen code/configuration, successful PAPER execution, complete collection and reconciliation, and timing after the actual activation. Catch-up dates, failed/skipped runs, timezone-ambiguous timestamps and changed configurations do not qualify. Deduplicate evidence by exchange session. The rule document calls for **2–3 months** of real paper observation; this duration cannot be accelerated with historical replay.

These qualification helpers do not retroactively certify the legacy `paper_validation_runs` date-range reports. Campaign-scoped activation/evidence integration must be completed before such reports can be used for promotion. No live-broker execution is authorized or enabled by this work.

## Remaining launch work

1. Run the corrected collector successfully for the current completed session and complete its source queues.
2. Repair current dated reference coverage and reprocess financial sources through V2; verify the full intended campaign universe.
3. Establish current deployment, scheduler/private archive, recovery and unattended rehearsal receipts.
4. Freeze a real activation record and bind the validation journal to it; then clear the resolved PAPER lock through the normal safety controller and enable PAPER admission.
5. Accumulate actual subsequent-session evidence; never rewrite old reports into a campaign history.
