# PMS Console

Next.js operator and research interface for the PMS. All financial values come from API evidence; missing evidence displays unavailable instead of zero. Formal PAPER execution and manual PAPER_SANDBOX accounts have separate pages.

## Run locally

Start the complete backend from the repository root:

```bash
uvicorn apps.api.main_exec:app --reload
```

Set DATABASE_URL (or PMS_DATABASE_URL) on the backend and OPS_API_KEY for operator mutations. Database connections use the pms schema. Then:

```bash
cd apps/web
cp .env.example .env.local
npm install
npm run typecheck
npm run build
npm start
```

PMS_API_BASE_URL is the server-side backend origin and takes precedence over NEXT_PUBLIC_API_BASE_URL. Refresh reloads current evidence; no background polling runs strategy jobs. Dates use Asia/Kolkata and completed official market sessions. Operator actions require entering the API operator key for that action; the web server never supplies a privileged key on behalf of visitors. Serve operator pages over HTTPS in deployed environments.

## Views

- Overview: persisted account NAV history, regime, selected signals, workflow stages.
- Signals: dated ranking, search, status tabs, sizing, rejection reasons and pagination.
- Paper trading: formal PAPER positions, orders, fills and trade event timelines.
- Paper sandbox: explicitly isolated manual orders, conditional prices and saved-bar processing.
- Regime: weighted Module E components, capacity rules and data coverage evidence.
- Research: paginated universe and dated Module A/B stock details.
- Backtests: saved simulations, run detail, stress and walk-forward evidence.
- Data health: actual session coverage, benchmark freshness and unresolved quality issues.
- Operations: safety state, readiness, reconciliation, validation and worker audit; authenticated disable/stop controls.

## Verification

GitHub CI runs TypeScript checking, the production build, and tests/smoke.mjs against a separate synthetic API fixture. Browser checks cover 12 routes, mobile overflow, filters/pagination, operator authorization, idempotency forwarding, empty and unavailable states. Screenshots and results are saved as the pms-ui-smoke workflow artifact. Fixtures are confined to the test process and never used as application fallbacks.

UI completion does not certify the backtesting matrix, walk-forward validation, data integrity, production reliability or the 2–3 month paper campaign. Existing execution locks remain enforced by the backend.
