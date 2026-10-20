# Phase 3 — Cloud deployment

Status: the existing free API and SSR dashboard are live on Neon. Guarded startup, database readiness, read-only API credentials and controlled releases are applied. The combined hosted preflight passed. Collection-only scheduling is enabled; initial full-universe catch-up is still being verified. No PAPER campaign has started.

## Prepared configuration

- `render.yaml`: the existing free Python API and Node SSR dashboard, one API process, `/ready` database health probe, explicit PAPER-only environment, collection freshness gate and startup backfill disabled. The proposed Blueprint turns automatic deploys off; current services still deploy on commits. No paid worker, cron, Redis, database or disk is provisioned. The existing service names were verified against the repository; do not create a duplicate API or frontend. The Blueprint is reviewed desired configuration, not a record that it has been applied. Existing dashboard settings use `next start`; standalone output was removed to match this runtime.
- `scripts/start_cloud_api.py`: rejects missing or unsafe cloud configuration before API import. Requires a remote PostgreSQL URL with `sslmode=verify-full`, a private operator key and a valid port. Never applies migrations, starts collection or changes database safety state. The official public Supabase production root CA is bundled and scoped to Supabase database/pooler hosts; reviewed custom CA configuration takes precedence and other providers use the system trust store. Configuration validation alone does not prove the certificate or database connection.
- `netlify.toml`: optional alternative only; the actual PMS frontend is on Render. Builds the Next.js application from `apps/web` using the automatically installed SSR adapter. The frontend needs server-side `PMS_API_BASE_URL` in Builds and Functions scopes followed by a redeploy. It receives no database password or operator key. Keep the frontend lockfile committed and use `npm ci` for reproducible installs.
- Python source dependencies are pinned, including Finstack's exact reviewed source revision. Transitive Python dependencies are not yet fully locked. The frontend stays on Next 15 / React 19.1 with patched releases; explicit PostCSS and sharp overrides address transitive advisories reported by npm. Recheck those overrides when upgrading Next.

## Connected-resource discovery (2026-10-19)

Supabase project `cxrkicqggsuylirzohqw` is ACTIVE_HEALTHY in ap-south-1. Preserve this existing shared database and its backfill. The user confirmed Render **My Workspace** (`tea-dah20kpt0dsc73e8ibs0`) by asking to continue. Both existing free services are in Singapore and match the PMS repository:

| Service | ID | URL |
|---|---|---|
| API | `srv-db9r6b6q1p3s739a40e0` | https://pms-status-api.onrender.com |
| SSR dashboard | `srv-db9r6g6q1p3s739a4fo0` | https://pms-status-dashboard.onrender.com |

The API `/health` and dashboard `/` returned HTTP 200. The initial API `/ready` and `/v1/execution/reliability` returned HTTP 503. Browser inspection found the literal `[YOUR-PASSWORD]` in Render DATABASE_URL. The user replaced the connection privately and deployed `dep-dbb4bvs9v7es73a5lj1g`. Both endpoints then returned HTTP 200. Operator key configuration and strength passed the runtime posture; worker evidence remains absent. No credential values were recorded. No private database credentials or Render administrative token are present in this execution environment; the Render connector can merge environment updates but cannot read the existing database URL or change service commands. The credential handoff is complete; the later browser session expired. Do not guess a pooler hostname, rotate the database password or print connection strings. The URL does not declare sslmode; `PMS_DATABASE_VERIFY_FULL=1` strengthens runtime connections to verify-full. Supabase connections use the bundled official production root CA unless a reviewed CA path is supplied; other providers use the system CA store. Deployment checks and startup use the same policy; placeholders are rejected before connection attempts. The first system-CA-only rollout (`22699ce`, deploy `dep-dbb5sbuk1f9s7385ruq0`) returned 503 on its new instance. The corrective root-CA revision `3363104ca0ae394f420fb3c2454c10c687e07998` is live in API deploy `dep-dbb622c9v7es73afps10` and dashboard deploy `dep-dbb622c9v7es73afpsag`. After both became live, `/ready`, `/health`, reliability and the dashboard returned 200. API logs at 18:29:20 UTC tie readiness, reliability and representative console reads to new instance `pwgkk`, so old-instance responses are not the evidence. Certificate and hostname verification remain enforced. Full Python suite: 712 passed, 1 skipped; CI run 39302310843 passed all four jobs. Worker evidence remains `NO_EVIDENCE`, restore evidence `NOT_VERIFIED` and execution readiness false.

Merged API environment updates preserve existing credentials and set PAPER mode, live trading false, startup backfill off, private HTTP exception off and collection-required=1. The dashboard origin is set to the API HTTPS URL. Each environment update triggered a deployment automatically; do not trigger duplicates. The guarded startup command and `/ready` platform health check remain proposed pending adoption through a supported administrative route. Runtime database verification and operator-key configuration have been confirmed.

The free Supabase organization has a measured database size of 681,962,643 bytes and user relations of 669,442,048 bytes. The official database-size guide documents a 500 MB free database quota (https://supabase.com/docs/guides/platform/database-size). Measured database and user-relation sizes exceed it. Default transaction read-only is currently off; this does not waive the quota or certify future writes. Verify provider quota/usage and preservation options before collection writes; do not delete or compress away backfill to make room without a reviewed recoverable plan. There are currently no Storage buckets. Database safety remains PAPER with new orders disabled and `reference_data_incomplete` locked.

The connected GitHub tools cannot set Actions secrets/variables. Browser inspection verified that repository secret `PMS_DATABASE_URL` already exists, but its validity has not been tested; no repository variables are set, so the collection workflow remains disabled. Do not put credentials in workflow files, logs or chat. Secure configuration requires an authorized supported administrative route or a dashboard handoff.

## Deployment sequence still to execute

1. Resolve provider quota; verified TLS rollout is complete. Existing free services and workspace are identified. Verify spend limits and available workspace hours; a free compute plan alone does not prove zero overage charges.
2. Establish a recoverable backup including shared public-schema source dependencies; record its private location and hash. Review the current schema and application-role grants. Apply only migration `010_cloud_collection.sql` in a transaction with `search_path=pms,public`; do not replay migrations 001–009 or rebuild the backfill.
3. Configure PostgreSQL credentials and trusted TLS CA privately. Configure the Render API with the complete execution entrypoint, then verify deployed `/health` and `/ready`, operator authentication and PAPER safety state. Review protection for read endpoints: the existing mutation key does not make GET dashboards private.
4. Configure the frontend's HTTPS API origin and deploy SSR. Verify representative dashboard reads, errors and operator controls against the actual API. A local Next build alone does not prove deployed API/database connectivity.
5. Configure a private durable raw-source archive and retention before collection activation. Existing JSON artifacts contain receipts, not replayable raw inputs. Never publish shareholding XML as public Actions artifacts.
6. In GitHub Actions, privately set `PMS_DATABASE_URL` (TLS verification enabled) and set repository variable `PMS_CLOUD_COLLECTION_ENABLED=true` only after migration/storage are ready. Leave `PMS_CLOUD_PAPER_ENABLED` unset or false. Do not activate a second Celery scheduler.
7. Run collection to clear the initial XML/classification backlog; confirm full-universe writes, receipt freshness and resource/usage limits. PARTIAL is not acceptance. Save actual deployed revisions, service URLs, source receipt and resource measurements as deployment evidence.

Free Render services can sleep and lose local files; the API is not the daily scheduler. GitHub Actions performs collection directly against PostgreSQL independently of API uptime. Account quotas, schedule delivery and private archive capacity still need verification. No paid resource or upgrade is authorized by these files.

## Completion criteria

Phase 3 is complete only after verified live API and SSR frontend deployment, reviewed migration and grants, secure scheduler configuration, private raw archival, full-universe catch-up and measured free-tier capacity. Until then do not mark this phase complete or begin the 2–3 month campaign. Historical validation, alerts/restore and consecutive-session rehearsal remain Phases 4–6.

## Selected free capacity route — destination provisioned, copy pending

Current Supabase data exceeds its documented 500 MB free quota. Read-only table statistics do not demonstrate enough reclaimable space to solve this with routine vacuuming. No data or indexes were deleted.

Neon's official pricing page (https://neon.com/pricing, checked 2026-10-19) currently lists **1 GB PostgreSQL storage**, **5 GB object storage**, **100 CU-hours/month/project**, and **5 GB public transfer/month** on Free with no card required. Its older FAQ still mentions 0.5 GB; verify the actual account entitlement before provisioning. The user selected a separate Neon Free PMS copy. The destination is provisioned as recorded below; capacity after restore and application cutover remain unverified. Aiven Free currently lists 1 GB disk, so it offers less useful separation of raw archives and database data.

Copy only the complete dependency closure into the separate free PMS project: the `pms` schema plus every public source table/view/function/extension it reads. Do not move or delete the shared Supabase source. Use a consistent source snapshot and preserve a private dump and hashes. Validate row counts, key/date coverage, view dependencies, constraints, identity sequences and PAPER safety before switching either the API or scheduler. Use a direct/session-compatible PostgreSQL connection because collection/orchestration use session advisory locks; transaction pooling cannot substitute for that guarantee. Test certificates, role grants and source connectivity on the actual runner.

Keep raw source documents and backup dumps in private object storage with retention, checksums and a successful restore rehearsal. Do not put raw XML, database dumps or secrets in public repository files or public Actions artifacts. Object-storage functionality, regional availability and access controls must be verified against the actual account; the pricing allowance alone is not a working archive.

A 682 MB database leaves only about 318 MB nominal headroom under a 1 GB limit. Initial catch-up, indexes, WAL/history accounting, daily growth, compute and network allowances need measurement. Stop below hard limits; do not enable a paid plan or card-backed overages. The user authorized this separate copy; no source data has been transferred yet.

## Neon destination provisioned (2026-10-19 UTC / 2026-10-20 IST)

- Organization `org-holy-firefly-58167446` (Om), plan `free` verified before and after creation.
- Project `green-resonance-86147459` (`pms-paper`), AWS Singapore, PostgreSQL 17.
- Default branch `br-twilight-sea-azumjkpc` (`main`), database `pms`, owner `pms_owner`.
- Compute `ep-bitter-forest-azwuzrwh`, fixed minimum/maximum 0.25 CU. Free-account custom suspend-time configuration was rejected before creation; creation succeeded with the provider default. Do not infer tested idle-suspend behavior from the returned zero-valued setting.
- Actual project database limit: 1,073,741,824 bytes. Empty database query succeeded (7,520,256 bytes). The internal MCP SQL connection reports TLS false; this is not evidence about external client TLS. Verify the external direct connection with verify-full before transfer/cutover.
- Private bucket `pms-private-archive` on the default branch. Storage upload and signed download returned 200 with matching SHA-256; anonymous download returned 403. The 44-byte probe is storage transport evidence, not a database backup or restore rehearsal.
- Read-only view/foreign-key traversal identifies 82 PMS relations plus `public.benchmarks`, `public.corporate_events`, `public.financial_facts`, `public.market_bars`, `public.securities`, `public.security_benchmarks`, and `public.sources`: 626,384,896 bytes of relation storage. Complete function, type, extension, sequence, trigger and policy dependency review remains necessary before building the archive. PMS has the `audit_operational_change()` function.

No data copied; no migration 010; no service connection switch; no scheduler activation. Supabase is unchanged. No database or storage credential has been recorded in this document.

### One-time destination secret setup

Open the Neon project's **Connect** panel, choose database `pms` and role `pms_owner`, disable connection pooling, and copy the direct connection string privately. Add it to repository **Settings → Secrets and variables → Actions → New repository secret** as `NEON_PMS_DATABASE_URL`. Preserve existing `PMS_DATABASE_URL` as the source; do not overwrite it. Never paste either URL into chat, source, issue text or logs. The GitHub connector cannot write Actions secrets.

A reviewed one-shot migration runner and private backup credentials still need configuration before executing a copy. It must use a consistent source snapshot, archive all required dependencies privately before restore, verify fingerprints/constraints/sequences/safety and measure capacity. Do not mistake adding the secret for starting that runner. No local or daily laptop command is needed for this configuration step.

## One-time copy runner

`scripts/copy_pms_to_neon.py` defaults to a read-only connection/dependency/safety check.
The destination is pinned to the provisioned direct Neon endpoint/database/role;
the source is pinned to the existing Supabase project on session port 5432.
Both use verify-full with reviewed CA selection. No connection is silently
rewritten to a guessed pooler. The user reports adding `NEON_PMS_DATABASE_URL`;
a successful actual runner check is still needed to verify it.

`.github/workflows/pms-neon-copy.yml` runs **check only** when its configuration
lands on main. It has no schedule. Its manual `copy` mode refuses non-empty
destinations and requires `PMS_NEON_ARCHIVE_JSON` with exactly these JSON fields:
`access_key_id` and `secret_access_key`. These must be a branch-scoped Neon
credential with storage read/write permission, configured privately in GitHub
Actions secrets. The private bucket/branch endpoint are pinned in code. This is
separate from the Postgres connection password; a working SQL secret does not
authorize S3 uploads. Never print or commit the credential.

The copy holds a read-only REPEATABLE READ source transaction and exports its
snapshot to two custom-format pg_dump archives: public dependencies (including
owned sequences), and the complete PMS schema. The runner checks the live
view/FK dependencies against the reviewed seven public tables and blocks
unsupported external types/functions/triggers. Additional newly introduced
dependencies require review. It does not copy unrelated public tables or users.

Before any restore it writes both archives and the row-hash manifest to the
private bucket and downloads each to verify SHA-256. It reserves archive and
Postgres capacity below the Free limits. Restore renders pre-data, data and
post-data sections in dependency order into one psql transaction. Supabase API
role policies/ACLs are omitted; RLS is retained, PUBLIC access is revoked and
owner access is used for validation. Application role grants remain separate.
Every selected table/view is compared using row-count-preserving sorted hash
buckets; sequence state may not regress, and PAPER/new-orders-disabled state
must match. No app URL, ingestion toggle or trading permission is changed.

Only the sanitized receipt is published as a workflow artifact. Dumps, manifests,
restore SQL and credentials never go into Actions artifacts. A failed restore or
post-restore comparison leaves the isolated destination for review, refuses to
retry over it, and never clears it automatically. A successful copy still needs
role/privilege, quota, collection migration and application validation before
cutover. The separate CI PostgreSQL17 rehearsal uses synthetic data and no
production credentials; it does not certify that the real data has been copied.

## Hosted transfer preflight result (2026-10-19 19:02 UTC)

PR14 implemented the copy runner; PR15 separated connection checks and credential-free failure categories. Full local Python suite: 725 passed, 2 skipped. All five PR15 CI jobs passed, including the real PostgreSQL17 export/restore rehearsal. PR16 configured the actual Ubuntu CA bundle through SSL_CERT_FILE for binary libpq's system-root verification, without weakening TLS.

Hosted check-only run `39307509790` at revision `f920a0217719b16b39ffc1c04f5b67c2628cc89d` verified the new `NEON_PMS_DATABASE_URL` with certificate and hostname verification and confirmed the isolated target is empty. It then stopped at `source_configuration` with `DATABASE_PASSWORD_PLACEHOLDER`: the existing GitHub `PMS_DATABASE_URL` has a placeholder password. No real backup or data copy occurred; no service or scheduler setting changed.

Remaining one-time configuration in GitHub Actions secrets:

1. Replace source `PMS_DATABASE_URL` with the actual existing Supabase project's **session pooler** connection (port5432), with the real database password properly included. Keep `NEON_PMS_DATABASE_URL` as the validated destination; never substitute Neon for the source.
2. Set `PMS_NEON_ARCHIVE_JSON` to a JSON object with `access_key_id` and `secret_access_key`. Obtain these privately in Neon **pms-paper → Connect → Storage → Parameters only → Reveal credential**, using AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY respectively. Do not rotate existing credentials or put the API token in the S3 secret field. The current default storage credential has storage read/write scopes. A separately named scoped storage credential is also supported.
3. Open repository Actions **PMS Neon one-time copy**, select **Run workflow**, branch main, mode **copy**. The existing authorization covers the isolated copy; the connection and private-backup guards run before restore. The connector cannot write Actions secrets or dispatch workflows, so these are one-time dashboard steps, not daily laptop commands. Never paste credentials into chat or public files.

The hosted receipt contains only fixed status codes and connection verification flags. It is not evidence that data has been migrated or that the full cloud deployment/campaign is accepted.


## Latest hosted copy attempt (2026-10-19 19:28 UTC)

The user corrected the source secret. Check-only run `39307509790`, attempt 3,
verified both database connections with certificate and hostname verification.
Copy run `39310049295` stopped before restore with an initially generic receipt.
PR18 added fixed diagnostic codes and detailed stages; all five CI jobs passed
(run `39310602409`), including 18 PostgreSQL17 copy-rehearsal tests. The local
suite passed 729 tests with 2 integration skips.

Explicit copy retry `39311170924` at revision
`5f5f2dd77b55122befeffb5aed18a8847c15ce62` stopped at
`archive_configuration` with `ARCHIVE_CREDENTIAL_JSON_INVALID`.
`PMS_NEON_ARCHIVE_JSON` is present but cannot be parsed as JSON. This is a format
failure before SDK authentication, database dumping, archival or restore; it
does not establish whether the credential values are valid. Both database
connections and the source dependency/safety gates passed. The target was empty
at the start of this attempt. No cutover, migration or campaign was started.

The only immediate user action is to edit that existing Actions secret into a
JSON object containing exactly `access_key_id` and `secret_access_key`, with both
values as double-quoted strings. Use the actual Neon Storage parameter values
privately. Do not include shell assignments, Markdown fences, an outer quoted
JSON string or a trailing comma. Preserve both database URL secrets. Never put
credential values into chat or repository files. Retry the guarded workflow
only after correcting the secret. The earlier placeholder diagnosis above is
historical and is resolved.


## Retry after user secret update (2026-10-19 19:33 UTC)

Run `39311170924`, attempt 2, stopped at `destination_configuration`, before any
database connection or data operation. The destination URL previously verified
successfully, but this latest attempt could not read its configuration. It did
not reach the archive JSON check, so the previous archive format failure must
not be described as the current confirmed blocker. Source data and application
settings remain unchanged; no copy or campaign has started.

The runner now reports independent, credential-free format checks for
`NEON_PMS_DATABASE_URL`, `PMS_DATABASE_URL` and `PMS_NEON_ARCHIVE_JSON` before
opening a connection. URL parsing failures have fixed source/destination error
codes and never echo their input. A `FORMAT_VALID` result confirms only syntax,
pinned destination and connection options; it does not prove authentication or
storage access. Correct only the specific secrets identified by the hosted
receipt. Database URL values and storage JSON belong in different secrets.


## Independent hosted configuration checks (2026-10-19 19:37 UTC)

PR20 passed all five CI jobs (`39312281024`), including 21 tests in the real
PostgreSQL copy-rehearsal job; local tests passed 732 with 2 integration skips.
Check-only run `39312565267`, revision
`5c33bc192775c61a1eba89a87dea1a62d485fc1a`, reported:

| Secret | Current format result |
| --- | --- |
| NEON_PMS_DATABASE_URL | DESTINATION_URL_INVALID |
| PMS_DATABASE_URL | FORMAT_VALID |
| PMS_NEON_ARCHIVE_JSON | ARCHIVE_CREDENTIAL_JSON_INVALID |

Both malformed secrets need correction. The destination secret must contain
only the direct Postgres URI from Neon Connect (pms database, pms_owner role,
pooling disabled). It must not contain JSON, a shell assignment or surrounding
quotes. The archive secret must contain only a valid JSON object with the two
Storage parameter strings described above, using ordinary double quotes and
no Markdown fences or trailing comma. Keep the source database secret unchanged.
Only formats were checked in this run: it stopped before database connections.
The prior successful connection checks are historical evidence. A separate
read-only Neon check after the retry still found 0 application objects and
7,520,256 database bytes; database-copies/ contained no objects. No data copy,
service cutover, migration or campaign has happened.


## Stored data restored and verified; derived-ID repair pending (2026-10-19 UTC)

Fresh run `39314574277` verified all secret formats and both verify-full database
connections. Explicit copy run `39314787355` created and round-trip SHA-256 checked
two private database dumps and the snapshot manifest before committing the
isolated restore. Its strict comparison correctly stopped with
`RESTORED_DATA_MISMATCH`; do not rerun copy over this populated destination.

Independent comparisons against that archived snapshot matched all **57 physical
tables** and **5 of 6 views** exactly (62/63 relation manifests). All 26 sequences
passed their nonregression checks. All 2,302 instruments are present. The new
PMS database measured 575,979,520 bytes against its 1,073,741,824-byte limit.
This is current capacity evidence, not a 3-month growth guarantee.

The sole mismatch is `pms.shares_outstanding_history`. Its external view generates
ordinal IDs using ROW_NUMBER ordered by security, period end and source, without
a final unique tie-breaker. There are 243 tied groups covering 486 distinct
payload rows. Different physical layouts/plans assign different IDs to those
rows. The entire 44,579-row multiset excluding only that generated ID matches
between source and destination (MD5 `e7ecd55bb58a6812cf238301428bd74c`). The underlying
physical tables, including stored local IDs, match the snapshot exactly. No
source or destination stored rows were changed to obtain this result. The
original workflow remains BLOCKED rather than being relabeled a strict pass.

`migrations/011_deterministic_share_view_ids.sql` adds the source financial-fact
primary key as a final sort key. It changes only the derived view, retaining
all rows and local IDs. Ordinal IDs remain derived and can shift when source
rows are added; they are not permanent source identifiers. The change has been
prepared on temporary Neon branch `br-lucky-lab-azr3ohss`, migration
`c07b91cd-6314-4cc1-a954-84256c32be6b`. Branch tests found 44,579 unique IDs,
identical full hashes under different planner settings, unchanged payload hash,
zero local-ID/payload differences, unchanged owner/ACL and locked PAPER safety.
Main is unchanged. The Neon completion tool requires explicit confirmation
before applying the prepared migration and removing its temporary branch.

Detailed non-secret receipts are in `docs/neon_copy_validation_2026-10-19.json`.
The private dumps and row-level hash buckets remain outside GitHub. Supabase
is preserved. Migration010, application grants, service cutover, raw-source
archival, collector catch-up and capacity/operational acceptance remain pending.
No campaign or new orders were enabled.


## 2026-10-20: repaired copy and collection deployment preparation

The user instructed continuation. Prepared migration011 was applied through Neon
and rechecked on main: 44,579 unique derived share IDs, matching hashes across
planner settings, identical payloads/local IDs/ACL, locked PAPER safety. The test
branch was removed. Supabase was not modified. The original strict-copy run
remains a historical BLOCKED receipt; subsequent independent checks and the
recorded view-only repair establish the copied dataset's integrity.

The reviewed expansion of migration010 (`docs/deployment_sql/010_neon_collection.sql`)
was tested on a copy-on-write branch and applied to main. All 747,098 backfilled
prices remain visible, with an empty separate cloud-write table, RLS and no
PUBLIC access. A test insert was visible through the combined view and rolled
back before committing. The ingestion queue index is present.

A read-only `pms_status` login role was tested and applied for the existing public
status API. It can read the copied views but has no database/schema creation,
PMS writes, source writes, owner membership or RLS bypass. Its password is managed
privately and never stored in these files. `PMS_API_READ_ONLY=1` rejects operational
mutations clearly while retaining public stateless calculations and read views.

Automatic review rejected the proposed `pms_api` writer role because of its broad
DML and function privileges. It was NOT applied; its test branch was discarded.
The exact unapproved proposal remains in `docs/proposals/api_writer_grants.sql`.
Operational API writes require explicit approval/review before enabling. The
scheduled collector is independent of the status API and remains disabled until
hosted checks pass. PAPER activation remains a separate later phase.

`PrivateArchive` saves raw source bytes in the existing private bucket under
content-addressed date keys. It validates privacy using a non-sensitive probe
before any raw upload, verifies actual downloaded SHA-256, deduplicates content,
refuses conflicting existing objects and stops below a 4GB reserve. It performs
no automatic deletion. Provider receipts retain archive keys, not raw bodies or
signed links. A fresh collection receipt without verified private archival and
anonymous-read denial cannot open the PAPER entry gate.

The cloud workflow now uses the existing `NEON_PMS_DATABASE_URL` and storage JSON
secrets, leaving the shared Supabase source secret intact. New manual workflow
**PMS cloud deployment preflight** checks actual TLS, safety, schema, capacity and
private archival without database writes. An optional past completed date probes
official sources and archives their raw documents privately; it remains sampling,
not proof of full-universe readiness. No broker key or PAPER permission is added.


## Phase-3 continuation (2026-10-20 UTC)

PR23 is merged at `f04cac90d2887be6a0136b010e17f18d82552600`. Its CI run
`39381034111` passed all five jobs, including 730 Python tests and 23 real
PostgreSQL copy/overlay tests. Actual hosted preflight `39381649589` verified
Neon certificate/hostname validation, locked PAPER safety, all six writable
collection targets and the private archive. Seventeen raw source objects were
uploaded and downloaded with matching SHA-256; anonymous access was denied.
The archive measured 48,659,293 bytes including the prior private backups.

The overall preflight correctly remained BLOCKED because LPDC's shareholding
XML identifies the company by BSE scrip code rather than NSE symbol. PR24,
merged at `5b8e9afca7b4603421bd5c460ec2e4ec1895fd50`, now requires a same-period
header to link that code, NSE symbol and listing ISIN before admitting its
contexts. Mismatches, ambiguous identities and duplicate contexts fail closed.
The private archived XML parses; nine synthetic regressions were added without
committing individual shareholder disclosures. Local tests: 739 passed, 5
skipped. All five CI jobs passed in run `39382844327`, but its source probe
skipped the current exchange non-session; that is not a post-repair full
source/preflight pass. Repeat the dated hosted preflight before activation.

Render sign-in succeeded through the secure GitHub authentication handoff. The
first settings edit and subsequent browser recovery timed out. A connector
read confirmed no settings update: auto-deploy remains enabled, the build is
`pip install -r requirements.txt`, the start command is direct Uvicorn and the
health path is empty. The existing live deployment is still `3363104`.

Automatic approval review rejected transferring the new `pms_status` Neon
password to the existing Render API's `DATABASE_URL` because that specific
credential disclosure was not explicitly authorized. No environment update or
cutover occurred. Obtain explicit approval for that precise destination and
read-only credential before retrying; do not route the secret through another
channel. The earlier broad API writer-grant proposal remains unapplied. The
read-only role is the intended status-API connection and does not authorize
operational writes, collection or PAPER admission by itself.

A management-API observation at 07:11 UTC found the Neon compute idle, with
last activity at 06:40:16 and suspension at 06:45:17. Thus automatic suspension
was observed despite the management timeout field reporting zero. Compute is
limited to 0.25 CU; this observation does not certify monthly usage or
three-month data growth. Collection remains disabled and no campaign started.

A separate read-only source probe at 12:42:38 IST on the repaired main revision
returned SUCCESS for the completed 2026-10-19 session: 3,405 equity price rows,
all four benchmark/VIX codes, 3,203 surveillance rows and successful financial,
shareholding and classification samples. The receipt includes source hashes
and observation times. This ran in the Codex runtime without database writes
or private archival; it does not replace the pending combined hosted preflight
or full-universe ingestion and catch-up evidence.


## Live Neon cutover and collection activation (2026-10-20 UTC)

The user authorized the specific read-only Neon credential transfer by asking to
continue after the explicit approval request. The Render environment merge
succeeded without printing or persisting credentials in this repository. The
API uses `pms_status`, `PMS_API_READ_ONLY=1` and verified TLS. Database activity
confirmed two sessions from that role. No writer role or owner credential was
given to the public API.

The API settings are applied: `pip install -r requirements-ops.txt`,
`python scripts/start_cloud_api.py`, health path `/ready`, auto-deploy off.
Deploy `dep-dbbhd3m7bikc738j0g5g` is live at revision `76a6ba9`. Health, readiness,
overview and stock-list HTTP reads returned 200. The dashboard uses `npm ci`,
has auto-deploy off, and deploy `dep-dbbhggnavr4c73dtcla0` is live at `ea1a154`.
Browser verification shows the API reachable and new entries locked.

Combined hosted preflight `39386151246` passed database/TLS, private archive
privacy/checksums and the completed 2026-10-19 official source sample. It is not
full-universe ingestion evidence. The private bucket measured 50,733,841 bytes.
Render billing showed Hobby, no card on file, 15.38/750 free instance hours,
111/500 included pipeline minutes, 17MB/5GB bandwidth, and zero current service,
datastore and pipeline charges. These are current observations, not future
uptime or growth guarantees.

Repository variable `PMS_CLOUD_COLLECTION_ENABLED=true` is applied.
`PMS_CLOUD_PAPER_ENABLED` is absent, so daily data attempts at 19:07, 19:27 and
19:47 IST cannot start the PAPER lifecycle. Shared source Supabase is untouched.

PR26 adds manual, Neon-pinned initial catch-up for a past completed session
within seven days, at most six resumable batches. Current reference snapshots
retain today's observation date. Each batch checks locked safety, database
reserve and private archival; it never calls PAPER execution. All five CI jobs
passed (`39386404904`); local tests then passed 744 with 5 skipped.

The actual first catch-up `39386729186` stopped at the master ISIN guard before
market imports. Official master and detailed quotes independently confirmed six
current identifiers. The scoped repair in `docs/deployment_sql/013_reviewed_isin_refresh.sql`
was rehearsed with a savepoint/rollback, then applied only in the isolated PMS
copy. It preserves old ISINs, stable instrument IDs, external security links
and all 747,098 prior prices. The review and source hashes are recorded in
`docs/isin_identity_review_2026-10-20.json`. Event effective dates and corporate
action adjustment factors are not inferred by this current-identity review.
Explicitly reviewed prior identifiers may be used only in completed price
archives through 2026-10-19; unknown master changes still fail closed.

Full-universe catch-up, remaining source exceptions and measured data growth
must pass before Phase 3 can be marked complete. The old failed receipts above
are preserved as historical evidence.

PR27's historical-price identity handling passed all five CI jobs in run
`39387817266` (745 local tests passed, 5 skipped) and merged at `cc769e3`.
Initial catch-up resumed in hosted run `39388234721`, worker journal
`8fe8edbe-c625-4582-9f8c-fb0e65404e00`. Its result, not dispatch alone, determines
catch-up completion. The source guard was retained and the exact reviewed
current-identifier refresh is applied; no additional API privileges were given.

The resumed run `39388234721` stopped on explicit 2026-10-13 price coverage
after committing 2,592 sourced bars and expanding the master to 2,620
instruments. Twelve active symbols were absent from both the official UDiFF
archive and the independent security-wise daily report. No zero-volume candles
or suspension states were fabricated. Raw source evidence also linked GFSTEELS
to TIKONA and HEGAM to HEG by unique ISIN, with matching dated surveillance
records. CCAVENUE's historical surveillance identity remains unverified.
Details and hashes are in `docs/cloud_price_exceptions_2026-10-20.json`.

The collector now retains PARTIAL coverage while proceeding with independent
reference feeds and the remaining dated archives. Successful references cannot
promote incomplete price coverage to SUCCESS. Dated ticker mappings require an
unambiguous current or explicitly reviewed ISIN and reject conflicting source
statuses. Catch-up stops repeating batches once all reference queues are empty
even if a historical price exception remains; its exit status still fails.
This enables useful unattended repair without hiding the acceptance blocker.
Local suite: 747 passed, 5 skipped. Full Phase-3 acceptance is still pending.
