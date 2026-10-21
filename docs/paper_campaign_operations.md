# Forward PAPER launch preparations

Campaign preparation is implemented separately from data repair. It does not
clear safety locks, enable new orders, reset capital or create elapsed sessions.

## Deployment, scheduling and operational rehearsal

`pms-campaign-operations.yml` uses the existing private archive and Neon database,
with a separate recovery concurrency group. It verifies locked infrastructure,
takes an exported consistent snapshot, downloads and restores it into an empty
local PostgreSQL17 service, and verifies data/schema hashes and read-only access.
The existing repair/collection group is not cancelled or overlapped by an importer.
Only sanitized receipts are published; raw dumps and manifests remain private.

`verify_paper_operations.py` checks the deployed API commit against the exact
workflow revision, actual recent scheduled delivery, collection activation,
archive privacy, a fresh verified restore, and a read-only after-close preflight.
The rehearsal calls no settlement, admission, ranking persistence or journal
writes. A delivered schedule is distinct from successful collection; queued
runs do not prove completed scheduled delivery. Missing or failed checks remain
BLOCKED in the report, including data-dependent rehearsal failures.

The receipt includes a PREPARED activation draft with the code/configuration
hashes, intended instrument identities, initial capital and remaining readiness
blockers. Its activation timestamp is null and elapsed-session count is zero.
Fresh matching receipts can be supplied to `check_paper_campaign.py` using
`--operational-report`; missing, stale or revision-mismatched proofs fail closed.
Current dated sector repairs still cannot be applied to earlier sessions.

## Frozen campaign identity and daily evidence

Apply additive migration 013 before deploying the campaign-aware runtime. The
migration creates no campaign and changes no safety state. The new identity
table permits one active campaign, prevents rewriting activation time/manifest,
and retains closed campaigns. Status credentials receive SELECT only.

The internal `freeze_campaign` transaction requires a fresh passing readiness
assessment, unchanged current code/configuration/universe, and resolved PAPER
safety with entries still disabled. Its activation timestamp comes from the
database transaction clock. It does not clear locks or enable orders. No freeze
or activation is run by the operations workflow. Activation and subsequent
permission enablement remain conditional on all current launch checks passing.

Daily journals bind campaign ID, code/configuration hashes, source completeness
and reconciliation to their actual completed execution, including admission.
A frozen-input change blocks admission while settlement remains available.
The date-range validator now requires campaign-bound elapsed evidence. Old
validation reports cannot satisfy production readiness merely by declaring a
long date range or having `passed=true`.

Evidence is rebuilt from authoritative worker status and timestamps, deduplicated
by verified exchange date. Backfilled, failed, future, drifted and pre-activation
journals do not count. At least 60 actual elapsed days and the configured evidence
minimums are required; the final acceptance audit and any live promotion remain
separate. The intended 2–3 month observation cannot be completed in advance.

## Completion boundaries

Fresh deployment and restore receipts can be verified while repairs run. A fully
passing unattended rehearsal, actual activation and forward-session accumulation
require repaired current inputs. An operations workflow that reports BLOCKED is
not a completed campaign launch. Check its explicit gates before retrying.
