# Phase 5 — monitoring and recovery

Status: **Phase 5 implemented and verified**, with the existing Phase-3 collection
incident still open. This does not grant Phase-3 data acceptance or start the
paper campaign.

## Independent watchdog

`pms-cloud-watchdog.yml` runs five times per day, including 20:23 IST after the
20:15 collection deadline. It also runs after collection, initial catch-up and
backup workflows finish. It uses a separate concurrency group; an import cannot
keep the watchdog queued behind itself. A missing complete collection is checked
against the observed NSE holiday calendar, including explicit special sessions.
Unknown calendar coverage fails closed. A collection still running after 32
minutes is stale; a current initial catch-up remains visibly in progress.

Checks cover API liveness/readiness/security headers, dashboard availability,
unauthenticated mutation denial, the status role's lack of write/administrative
privileges, PUBLIC schema exposure, locked PAPER safety, critical DQ errors,
database reserve, archive privacy/capacity and verified-backup age. All SQL runs
in a read-only transaction. The only remote operational writes are a harmless
private archive probe and GitHub incident updates. A failed import, restore or
access check never enables orders or automatically changes credentials.

Alerts go to one deduplicated `[PMS watchdog] Operational incident` issue in this
repository, assigned to its owner `omparekh54-lgtm`. Public issue bodies contain fixed error codes and a workflow link,
never balances, holdings, source XML, passwords or database connection strings.
Recovery closes the incident only when all configured checks pass. A separate
manual drill creates and closes an explicitly named test issue and cannot close
a real incident. GitHub issue creation is the verified delivery boundary;
email/push delivery depends on the owner's GitHub notification settings. No
external messaging service or recipient is configured.

GitHub scheduling can be delayed. Collection and watchdog have separate jobs
but share the GitHub provider; a complete GitHub outage can delay both. Native
workflow failure notifications and the visible incident/workflow history are
additional evidence, not a claim of an independent provider heartbeat.

## Private backup and isolated restoration

`pms-recovery-backup.yml` takes a consistent current Neon snapshot weekly on
Sunday at 21:41 IST, or on manual request. The dump and row-hash manifest share
one exported PostgreSQL snapshot while ingestion may continue. Sequences are
nontransactional: their baseline is captured before the dump, and restored
values must be at least that baseline. Original Supabase and old backups remain
untouched. No restore targets Neon: the script accepts only the dedicated empty
`pms_recovery` database on localhost:5432 in the ephemeral PostgreSQL17 service.

The full custom dump and manifest are uploaded privately, downloaded again and
checked byte-for-byte with SHA256 before restoration. Restoration must reproduce
all selected table/view row hashes, column definitions, constraints, indexes,
functions, triggers, RLS flags, sequence safety and locked PAPER state. Roles and
passwords are not dumped. A read-only status role is rebuilt in the test server;
its read succeeds and a mutation permission check must fail. Existing API
credentials are not copied to the test server. Production access is checked
separately by the watchdog.

Only small sanitized receipts enter public Actions artifacts (90 days). Dumps,
manifests and source documents remain in `pms-private-archive`. Successful restore
receipts are also stored privately under `recovery-evidence/`; the watchdog
requires one no older than eight days. Nothing deletes old evidence. Storage
warns at 3.2 GB and blocks new archive operations below the 4 GB reserve ceiling.
No paid plan, persistent extra database, worker or disk is provisioned.

The configured recovery point objective is **up to seven days** between full
snapshots; the isolated dump/download/restore/verification objective is **under
60 minutes**. This does not certify a full production service cutover in 60
minutes. A fresh manual backup is required before an approved high-impact
release or PAPER activation. Daily raw archives are additional source evidence,
not a claim of daily full database recovery. If daily operational recovery is
required for the campaign, storage/retention must be revised before activation.

## Incident response

1. Keep new orders disabled. Inspect the incident codes and linked receipt.
2. Repair the identified source, configuration or access error; do not mark a
   missing fact, unknown ISIN or missing pledge percentage as verified.
3. Retry collection from its committed acknowledgements. Do not replay old
   market fills or silently backdate current reference observations.
4. For database loss, recover the verified private dump to a separate empty
   server, compare its manifest and schema, reconstruct reviewed roles and
   credentials privately, then obtain approval for production cutover.
5. Rerun the watchdog. A healthy result closes the incident; campaign permission
   remains a separate explicit gate.

## Verification record

- Implementation merged at `168a7ec2b03daadb061f29c4c2b41d4f73a22fe7`; all five
  CI jobs passed (`39420923482`), with 773 Python tests and the real PG17
  integration checks passing.
- Actual backup/download/restore run `39421303419` passed in **145.77 seconds**.
  All **64 relations and 26 sequences** matched, including schema, constraints,
  indexes, functions, triggers and RLS checks. Read-only role read succeeded;
  mutation was denied; PAPER safety stayed locked. Private dump: 44,453,047 bytes.
- Alert drill `39421490311` created owner-assigned issue **#34** and closed it
  through the tested recovery path.
- Backup completion automatically triggered watchdog `39421752083` in its
  independent concurrency group. API, dashboard, authorization, role, TLS,
  archive and backup-freshness checks passed. Its sole code was
  `COLLECTION_INCOMPLETE` for 19 October, correctly creating owner-assigned
  incident **#35**. The nonzero watchdog exit is expected for this real data
  incident; it is not a failed monitoring installation.
- The failed initial collection's most recent hard stop is
  `index_membership_identity_unresolved`; financial and shareholding exceptions
  also remain. These are Phase-3 repair work. No collection fact is waived by
  this Phase-5 result.

Machine-readable evidence and archive hashes are in
`cloud_operations_verification_2026-10-20.json`. Actual scheduled delivery and
consecutive unattended sessions remain Phase 6. The API's older static
restore-evidence label is not wired to these private recovery receipts; the
verified workflow receipt and this runbook are the Phase-5 evidence boundary.

## Catch-up source verification follow-up (20 October)

Read-only run `39430574188` checked all 4,447 required source items with no
HTTP access failures. It passed 4,445 and blocked two current classifications:
SUPREMEENG and TIRUPATIFL. The import job was skipped, so this run did not
write collection data or enable PAPER.

Their Next API metadata contains former ISINs while the live official equity
master identifies the same company and series with the current ISIN. The
call-auction detailed quotes omit ISINs. The two exact metadata payload hashes
are reviewed in `reviewed_classification_identities.json`. Only those payloads
can use the live-master fallback, which independently checks current ISIN,
symbol, company name and series; the detailed quote must still match symbol,
series and market and have no explicit conflicting ISIN. Changed metadata or
master identity blocks. Evidence is retained in classification provenance.
This authorizes current classifications only, with the existing next-day
effective date. It does not authorize historical prices, filings, trading,
instrument identity changes, or historical sector reconstruction.

A subsequent full successful verification and actual import receipt are still
required to finish Phase 3. The prior run is not a completed import.
