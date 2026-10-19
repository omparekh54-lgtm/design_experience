# Consolidated local setup — accumulating checklist

**Superseded operating model:** the user now requires unattended cloud operation with no daily laptop use. Follow [cloud deployment readiness](deployment_readiness.md) and its phased consolidated deployment sequence. The checklist below is retained as a manual/local fallback only; it is not a requirement to keep the laptop running or execute daily jobs locally.

Phase 2 adds the [automated official collector](cloud_data_collection.md). Phase 3 must review/apply migration 010 after backup, configure source storage/secrets and prove the initial catch-up. Do not treat a successful no-write source probe as completed ingestion or campaign admission.

The user has not run the intermediate module commands since backfill and will run the accumulated setup at the end. No command in a phase-completion message should be treated as already executed locally.

This checklist is being maintained while implementation continues. Final ordered commands will be supplied after the remaining phases and local environment requirements are checked. The exact database/migration state must be inspected before running migrations; do not blindly rebuild or replace the existing backfill database.

1. Pull the final repository state once and update Python/frontend dependencies.
2. Verify the existing environment and database connection without printing secrets.
3. Back up the populated database and apply only outstanding migrations to `pms`.
4. Run the dated Step-12 integrity plan and full audit, including historical warmup, financial-unit conventions, source vintages and listing/sector session coverage. Preserve reports, investigate FAIL/ERROR items, and separately reconcile the five independent source reviews (including full ASM/GSM archives). A REVIEW_REQUIRED or partial scan is not full data certification.
5. Supply the owner's explicit strategy policies where the original specification did not give numerical defaults. Missing policies continue to block eligibility.
6. Run read-only module/orchestrator checks, then the required historical calculations.
7. Run the matrix plan and preflight, repair actual blockers, then run/resume the research matrix and inspect saved evidence.
8. Freeze the walk-forward design, run its offline plan and read-only preflight, then run/resume retrospective folds. Review the separate 2025 final-holdout inclusion before consuming it; never retune settings against test results. Continue with integrity/security/acceptance audits.
9. Configure private operator authentication, HTTPS, verified database TLS and restricted broker/database access using the Step-13 runbook. Run the security/reliability plan and read-only check. Rehearse a trusted, complete backup restore into a separate empty disposable database and compare the source manifest; review grants, application behavior and recovery objectives separately. Do not restore over the backfilled source.
10. Start the complete API and frontend and verify connected local views, request guards and worker journals.
11. Begin the forward PAPER campaign only when its gates and explicit PAPER permissions are satisfied. Its 2–3 months are elapsed trading sessions, not an instant batch command.

No Angel One credential, real-money promotion or safety unlock is required to implement or plan the research matrix. Existing safety locks remain active. Implementation tests use synthetic fixtures and do not represent completed historical experiments on the user's computer.
