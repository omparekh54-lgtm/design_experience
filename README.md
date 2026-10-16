# Design Experience — Automated PMS

Production-oriented Indian equities PMS research, backtesting, paper-trading and execution platform.

## Current scope

This repository implements the v1 rules defined in `PMS_Rules_v1`: Module A (fundamental eligibility), Module B (rally-pullback technical setup), Module C (readiness ranking), Module E (market regime), and the shared risk/trade engine. Module D (IPO) remains intentionally parked until its rules are defined.

## Architecture

- `apps/api` — FastAPI service
- `packages/pms` — deterministic strategy, risk and domain logic
- `packages/finstack_gateway` — adapter boundary for Finstack / Angel One / exchange data
- `config` — versioned strategy parameters
- `tests` — rule-level regression tests
- `apps/web` — Next.js dashboard shell (after backend contracts stabilize)

## Safety / execution status

Live execution is **disabled by default**. The implementation path is research -> backtest -> paper trading -> human-approved live trading. Broker credentials are read only from environment variables and must never be committed.

## Local backend quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn apps.api.main:app --reload
```

Open `http://127.0.0.1:8000/docs`.

Run tests:

```bash
pytest -q
```

## Data design

Finstack is the internal gateway. Angel One SmartAPI will supply broker/live-market capabilities once credentials are configured. Official exchange filings remain the point-in-time source of truth for fundamentals, filing timestamps, corporate actions and surveillance history.

Every strategy decision must be reproducible using an `as_of` timestamp. Historical revisions must not overwrite the information that was known earlier.

## Important

This software is for research and controlled execution workflows. Backtests must use point-in-time data and model costs/slippage. Do not enable unattended live trading until data integrity, reconciliation, kill-switches and paper-trading validation are complete.
