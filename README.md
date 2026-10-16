# Design Experience — Automated PMS

Production-oriented Indian equities PMS research, backtesting, paper-trading and execution platform.

## Current scope

This repository implements the v1 rules defined in `PMS_Rules_v1`: Module A (fundamental eligibility), Module B (rally-pullback technical setup), Module C (readiness ranking), Module E (market regime), and the shared risk/trade engine. Module D (IPO) remains intentionally parked until its rules are defined.

## Architecture

- `apps/api` — FastAPI service
- `packages/pms` — deterministic strategy, risk and domain logic
- `packages/data_layer` — normalized instruments, OHLCV, quotes, filings, validation, provider adapters and ingestion services
- `packages/finstack_gateway` — strategy-facing boundary for point-in-time data access
- `migrations` — PostgreSQL / Supabase point-in-time schema
- `scripts` — operational ingestion commands
- `config` — versioned strategy parameters
- `tests` — strategy and data regression tests
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

## Data layer

The data layer uses our own canonical models so strategy code never depends directly on Angel One, NSE, Finstack or a vendor-specific response shape.

### Primary market data

Angel One SmartAPI is the primary authenticated source for historical daily OHLCV and quotes. The adapter:

- uses the current Angel token/trading-symbol mapping;
- chunks long daily-history requests to the provider limit;
- normalizes all candles into the internal `Candle` model;
- de-duplicates timestamps;
- never exposes credentials to PMS strategy modules.

Authentication uses `ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PASSWORD` (MPIN/PIN) and `ANGEL_TOTP_SECRET` from the environment.

### Instrument master

`AngelInstrumentMasterLoader` downloads Angel's daily scrip master, retries failures, falls back between the Angel One / Angel Broking hosts, filters the v1 universe to cash equities and maps provider tokens to stable IDs such as `NSE:RELIANCE`.

### Finstack fallback

`FinstackNSEMarketProvider` uses the existing Finstack project's `nse_history()` and `nse_quote()` helpers as a secondary market-data source and as a cross-check source. Finstack's current Moneycontrol/Screener helpers are useful for research/live enrichment, but they are not used as historical truth for Module A because the PMS requires original exchange filing timestamps and revisions.

### Point-in-time fundamentals

Official exchange filings remain the source of truth for Module A. A filing is stored with:

- period end;
- filing timestamp;
- consolidated/standalone scope;
- source;
- revision number;
- normalized facts.

Revisions are appended, never used to rewrite what was known at an earlier `as_of` date.

### Data quality

The ingestion layer checks invalid OHLC ranges, non-positive prices, negative volume, duplicate timestamps and cross-provider close/volume disagreements before data is trusted by the strategy.

## Database setup

Apply SQL migrations in order to PostgreSQL/Supabase:

```text
migrations/001_initial_schema.sql
migrations/002_data_layer.sql
```

The second migration adds normalized instrument metadata, ingestion-run tracking, provider snapshots and data-quality issues.

## Angel One data ingestion

Set your environment variables first. Example keys are listed in `.env.example`.

Then backfill selected NSE stocks:

```bash
python scripts/ingest_angel.py --symbols RELIANCE,TCS,INFY --start 2020-01-01 --end 2026-10-16
```

The script refreshes the current instrument/token mapping, authenticates with Angel One, downloads normalized daily candles, validates them and writes them to PostgreSQL.

## Data design principles

1. Raw/provider data is outside strategy logic.
2. Normalized canonical records are the strategy contract.
3. Every time-sensitive record carries the timestamp from which it became knowable.
4. Historical revisions do not overwrite prior knowledge.
5. Backtests read through `as_of` semantics to prevent look-ahead bias.
6. Primary and secondary sources can be reconciled independently.

## Important

This software is for research and controlled execution workflows. Backtests must use point-in-time data and model costs/slippage. Do not enable unattended live trading until data integrity, reconciliation, kill-switches and paper-trading validation are complete.
