# Phase 3 — Cloud deployment

Status: API database connection and certificate/hostname verification restored and verified; quota, backup, migration and private archival remain blocked. Cloud deployment is not complete. No campaign has started.

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
