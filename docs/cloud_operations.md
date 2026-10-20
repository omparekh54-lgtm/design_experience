# Phase 5 — monitoring and recovery

Status: implemented; actual watchdog delivery, isolated restore and live access
results must be recorded below before marking this phase verified. This work
does not grant Phase-3 data acceptance or start the paper campaign.

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

Pending hosted run IDs and measured restoration result.
