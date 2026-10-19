# Phase 3 — Cloud deployment

Status: existing API and SSR dashboard discovered and reachable; database readiness is blocked. Cloud deployment is not complete. No campaign has started.

## Prepared configuration

- `render.yaml`: the existing free Python API and Node SSR dashboard, one API process, `/ready` database health probe, explicit PAPER-only environment, collection freshness gate and startup backfill disabled. The proposed Blueprint turns automatic deploys off; current services still deploy on commits. No paid worker, cron, Redis, database or disk is provisioned. The existing service names were verified against the repository; do not create a duplicate API or frontend. The Blueprint is reviewed desired configuration, not a record that it has been applied. Existing dashboard settings use `next start`; standalone output was removed to match this runtime.
- `scripts/start_cloud_api.py`: rejects missing or unsafe cloud configuration before API import. Requires a remote PostgreSQL URL with `sslmode=verify-full`, a private operator key and a valid port. Never applies migrations, starts collection or changes database safety state. The CA used by libpq must be installed/configured separately; configuration validation does not prove the certificate or database connection.
- `netlify.toml`: optional alternative only; the actual PMS frontend is on Render. Builds the Next.js application from `apps/web` using the automatically installed SSR adapter. The frontend needs server-side `PMS_API_BASE_URL` in Builds and Functions scopes followed by a redeploy. It receives no database password or operator key. Keep the frontend lockfile committed and use `npm ci` for reproducible installs.
- Python source dependencies are pinned, including Finstack's exact reviewed source revision. Transitive Python dependencies are not yet fully locked. The frontend stays on Next 15 / React 19.1 with patched releases; explicit PostCSS and sharp overrides address transitive advisories reported by npm. Recheck those overrides when upgrading Next.

## Connected-resource discovery (2026-10-19)

Supabase project `cxrkicqggsuylirzohqw` is ACTIVE_HEALTHY in ap-south-1. Preserve this existing shared database and its backfill. The user confirmed Render **My Workspace** (`tea-dah20kpt0dsc73e8ibs0`) by asking to continue. Both existing free services are in Singapore and match the PMS repository:

| Service | ID | URL |
|---|---|---|
| API | `srv-db9r6b6q1p3s739a40e0` | https://pms-status-api.onrender.com |
| SSR dashboard | `srv-db9r6g6q1p3s739a4fo0` | https://pms-status-dashboard.onrender.com |

The API `/health` and dashboard `/` returned HTTP 200. API `/ready` and `/v1/execution/reliability` returned HTTP 503; application logs report `OperationalError`. This is a database connection failure, not a successful application-data deployment. Its specific cause is not yet established. No private database credentials or Render administrative token are present in this execution environment; the Render connector can merge environment updates but cannot read the existing database URL or change service commands. Secure connection verification needs an administrative route or dashboard handoff. Do not guess a pooler hostname, rotate the database password or print connection strings.

Merged API environment updates preserve existing credentials and set PAPER mode, live trading false, startup backfill off, private HTTP exception off and collection-required=1. The dashboard origin is set to the API HTTPS URL. Each environment update triggered a deployment automatically; do not trigger duplicates. The guarded startup command and `/ready` platform health check remain proposed until valid TLS credentials and the private operator key can be confirmed.

The free Supabase organization has a measured database size of 681,962,643 bytes and user relations of 669,442,048 bytes. The official database-size guide documents a 500 MB free database quota (https://supabase.com/docs/guides/platform/database-size). Measured database and user-relation sizes exceed it. Default transaction read-only is currently off; this does not waive the quota or certify future writes. Verify provider quota/usage and preservation options before collection writes; do not delete or compress away backfill to make room without a reviewed recoverable plan. There are currently no Storage buckets. Database safety remains PAPER with new orders disabled and `reference_data_incomplete` locked.

The connected GitHub tools cannot set Actions secrets/variables. Do not put credentials in workflow files, logs or chat. Secure configuration requires an authorized supported administrative route or a dashboard handoff.

## Deployment sequence still to execute

1. Verify database connection credentials, TLS CA and provider quota. Existing free services and workspace are identified. Verify spend limits and available workspace hours; a free compute plan alone does not prove zero overage charges.
2. Establish a recoverable backup including shared public-schema source dependencies; record its private location and hash. Review the current schema and application-role grants. Apply only migration `010_cloud_collection.sql` in a transaction with `search_path=pms,public`; do not replay migrations 001–009 or rebuild the backfill.
3. Configure PostgreSQL credentials and trusted TLS CA privately. Configure the Render API with the complete execution entrypoint, then verify deployed `/health` and `/ready`, operator authentication and PAPER safety state. Review protection for read endpoints: the existing mutation key does not make GET dashboards private.
4. Configure the frontend's HTTPS API origin and deploy SSR. Verify representative dashboard reads, errors and operator controls against the actual API. A local Next build alone does not prove deployed API/database connectivity.
5. Configure a private durable raw-source archive and retention before collection activation. Existing JSON artifacts contain receipts, not replayable raw inputs. Never publish shareholding XML as public Actions artifacts.
6. In GitHub Actions, privately set `PMS_DATABASE_URL` (TLS verification enabled) and set repository variable `PMS_CLOUD_COLLECTION_ENABLED=true` only after migration/storage are ready. Leave `PMS_CLOUD_PAPER_ENABLED` unset or false. Do not activate a second Celery scheduler.
7. Run collection to clear the initial XML/classification backlog; confirm full-universe writes, receipt freshness and resource/usage limits. PARTIAL is not acceptance. Save actual deployed revisions, service URLs, source receipt and resource measurements as deployment evidence.

Free Render services can sleep and lose local files; the API is not the daily scheduler. GitHub Actions performs collection directly against PostgreSQL independently of API uptime. Account quotas, schedule delivery and private archive capacity still need verification. No paid resource or upgrade is authorized by these files.

## Completion criteria

Phase 3 is complete only after verified live API and SSR frontend deployment, reviewed migration and grants, secure scheduler configuration, private raw archival, full-universe catch-up and measured free-tier capacity. Until then do not mark this phase complete or begin the 2–3 month campaign. Historical validation, alerts/restore and consecutive-session rehearsal remain Phases 4–6.
