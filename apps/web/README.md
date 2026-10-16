# PMS Console frontend

Next.js operator/research UI for the Automated PMS.

## Local development

```bash
cd apps/web
cp .env.example .env.local
npm install
npm run dev
```

Run the FastAPI backend separately from the repository root:

```bash
uvicorn apps.api.main:app --reload
```

By default the frontend expects `http://127.0.0.1:8000`. Set `NEXT_PUBLIC_API_BASE_URL` to the deployed FastAPI origin for staging/production.

## Production build

```bash
npm run typecheck
npm run build
npm start
```

## Routes

- `/` — portfolio dashboard, Module E regime, Module C On Deck, open positions
- `/signals` — Module C ranking board and pending entry candidates
- `/research` — research hub, stress/walk-forward evidence, selected stocks
- `/research/[instrument]` — detailed Module A + Module B stock research
- `/backtests` — backtest/research/stress/walk-forward run history
- `/data-health` — dataset freshness, row counts and unresolved quality errors

The UI is intentionally read-only for broker actions. Live execution remains disabled until the paper/live broker, reconciliation and kill-switch phases are completed.
