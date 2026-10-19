# Consolidated local setup — accumulating checklist

The user has not run the intermediate module commands since backfill and will run the accumulated setup at the end. No command in a phase-completion message should be treated as already executed locally.

This checklist is being maintained while implementation continues. Final ordered commands will be supplied after the remaining phases and local environment requirements are checked. The exact database/migration state must be inspected before running migrations; do not blindly rebuild or replace the existing backfill database.

1. Pull the final repository state once and update Python/frontend dependencies.
2. Verify the existing environment and database connection without printing secrets.
3. Back up the populated database and apply only outstanding migrations to `pms`.
4. Run coverage/integrity checks, including historical warmup and source-vintage requirements.
5. Supply the owner's explicit strategy policies where the original specification did not give numerical defaults. Missing policies continue to block eligibility.
6. Run read-only module/orchestrator checks, then the required historical calculations.
7. Run the matrix plan and preflight, repair actual blockers, then run/resume the research matrix and inspect saved evidence.
8. Run the separate walk-forward/out-of-sample and remaining audit phases when their implementation and data are ready.
9. Start the complete API and frontend and verify connected local views.
10. Begin the forward PAPER campaign only when its gates and explicit PAPER permissions are satisfied. Its 2–3 months are elapsed trading sessions, not an instant batch command.

No Angel One credential, real-money promotion or safety unlock is required to implement or plan the research matrix. Existing safety locks remain active. Implementation tests use synthetic fixtures and do not represent completed historical experiments on the user's computer.
