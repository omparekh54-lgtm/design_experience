# Phase 3 — Cloud deployment

Status: configuration prepared; cloud deployment is not complete. No campaign has started.

## Prepared configuration

- `render.yaml`: one free Python API, one process, `/ready` database health probe, explicit PAPER-only environment, collection freshness gate and startup backfill disabled. Automatic deploys are off. No paid worker, cron, Redis, database or disk is provisioned. Confirm the workspace and inspect existing services before applying, to avoid creating a duplicate API.
- `scripts/start_cloud_api.py`: rejects missing or unsafe cloud configuration before API import. Requires a remote PostgreSQL URL with `sslmode=verify-full`, a private operator key and a valid port. Never applies migrations, starts collection or changes database safety state. The CA used by libpq must be installed/configured separately; configuration validation does not prove the certificate or database connection.
- `netlify.toml`: builds the Next.js application from `apps/web` using the automatically installed SSR adapter. The frontend needs server-side `PMS_API_BASE_URL` in Builds and Functions scopes followed by a redeploy. It receives no database password or operator key. Keep the frontend lockfile committed and use `npm ci` for reproducible installs.
- Python source dependencies are pinned, including Finstack's exact reviewed source revision. Transitive Python dependencies are not yet fully locked. The frontend stays on Next 15 / React 19.1 with patched releases; explicit PostCSS and sharp overrides address transitive advisories reported by npm. Recheck those overrides when upgrading Next.

## Connected-resource discovery (2026-10-19)

Supabase project `cxrkicqggsuylirzohqw` is ACTIVE_HEALTHY in ap-south-1. Preserve this existing shared database and its backfill. Render returned workspace `tea-dah20kpt0dsc73e8ibs0`, named **My Workspace**; its selection requires the user's confirmation before service discovery or changes. Netlify returned `lucky-alpaca-0beddd` and `day01-sales-analytics-command-center`; neither is yet verified as the PMS frontend. Do not replace either based on its name alone.

The connected GitHub tools cannot set Actions secrets/variables. Do not put credentials in workflow files, logs or chat. Secure configuration requires an authorized supported administrative route or a dashboard handoff.

## Deployment sequence still to execute

1. Confirm Render workspace; inspect service repository, build/start commands, free plan and usage. Resolve which Netlify project is PMS, or create a separate free project after checking its plan. Verify existing spend limits before any deployment; a free compute plan alone does not prove zero overage charges.
2. Establish a recoverable backup including shared public-schema source dependencies; record its private location and hash. Review the current schema and application-role grants. Apply only migration `010_cloud_collection.sql` in a transaction with `search_path=pms,public`; do not replay migrations 001–009 or rebuild the backfill.
3. Configure PostgreSQL credentials and trusted TLS CA privately. Configure the Render API with the complete execution entrypoint, then verify deployed `/health` and `/ready`, operator authentication and PAPER safety state. Review protection for read endpoints: the existing mutation key does not make GET dashboards private.
4. Configure the frontend's HTTPS API origin and deploy SSR. Verify representative dashboard reads, errors and operator controls against the actual API. A local Next build alone does not prove Netlify adapter or deployed connectivity.
5. Configure a private durable raw-source archive and retention before collection activation. Existing JSON artifacts contain receipts, not replayable raw inputs. Never publish shareholding XML as public Actions artifacts.
6. In GitHub Actions, privately set `PMS_DATABASE_URL` (TLS verification enabled) and set repository variable `PMS_CLOUD_COLLECTION_ENABLED=true` only after migration/storage are ready. Leave `PMS_CLOUD_PAPER_ENABLED` unset or false. Do not activate a second Celery scheduler.
7. Run collection to clear the initial XML/classification backlog; confirm full-universe writes, receipt freshness and resource/usage limits. PARTIAL is not acceptance. Save actual deployed revisions, service URLs, source receipt and resource measurements as deployment evidence.

Free Render services can sleep and lose local files; the API is not the daily scheduler. GitHub Actions performs collection directly against PostgreSQL independently of API uptime. Account quotas, schedule delivery and private archive capacity still need verification. No paid resource or upgrade is authorized by these files.

## Completion criteria

Phase 3 is complete only after verified live API and SSR frontend deployment, reviewed migration and grants, secure scheduler configuration, private raw archival, full-universe catch-up and measured free-tier capacity. Until then do not mark this phase complete or begin the 2–3 month campaign. Historical validation, alerts/restore and consecutive-session rehearsal remain Phases 4–6.
