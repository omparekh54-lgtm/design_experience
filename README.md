# Design Experience — Automated PMS

Production-oriented Indian equities PMS research, backtesting, paper-trading and execution platform.

## Current scope

This repository implements the v1 rules defined in `PMS_Rules_v1`: Module A (fundamental eligibility), Module B (rally-pullback technical setup), Module C (readiness ranking), Module E (market regime), and the shared risk/trade engine. Module D (IPO) remains intentionally parked until its rules are defined.

## Architecture

- `apps/api` — FastAPI service
- `packages/pms` — deterministic strategy, risk and domain logic
- `packages/data_layer` — normalized instruments, OHLCV, quotes, official exchange filings, XBRL parsing, validation, provider adapters and ingestion services
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

The data layer uses our own canonical models so strategy code never depends directly on Angel One, NSE, BSE, Finstack or a vendor-specific response shape.

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

### Official NSE/BSE fundamentals

`NSEIntegratedFinancialsProvider` reads NSE's official Integrated Filing - Financials feed. `BSEFinancialResultsProvider` reads BSE's official company financial-results pages and XBRL links. Both are normalized into the same `FinancialFiling` model.

Each filing preserves:

- period end;
- original broadcast/filing timestamp;
- original vs revised submission type;
- audited/unaudited status where supplied;
- consolidated/standalone scope;
- revision timestamp and remarks;
- exchange received/disseminated timestamps where supplied;
- details and XBRL URIs;
- raw exchange payload;
- normalized financial facts.

The XBRL parser currently normalizes monetary facts to **INR crore** and diluted EPS to **INR/share**. It recognizes the core Module A fields including revenue, operating profit, PAT, diluted EPS, PBT, other income, exceptional items, operating cash flow, borrowings/debt, equity and finance/interest cost. Raw XBRL provenance is retained so taxonomy mappings can be expanded without changing the strategy contract.

Revisions are appended/idempotently upserted and never allowed to rewrite what was knowable at an earlier `as_of` date. Point-in-time queries choose the newest report period available by the requested timestamp, prefer consolidated over standalone for the same period, then choose the latest revision.

### Data quality

The ingestion layer checks invalid OHLC ranges, non-positive prices, negative volume, duplicate timestamps and cross-provider close/volume disagreements before data is trusted by the strategy.

## Database setup

Apply SQL migrations in order to PostgreSQL/Supabase:

```text
migrations/001_initial_schema.sql
migrations/002_data_layer.sql
migrations/003_exchange_financials.sql
```

Migration 003 adds official-exchange filing provenance, XBRL links, revision metadata and an identity key that distinguishes standalone and consolidated submissions.

## Angel One data ingestion

Set your environment variables first. Example keys are listed in `.env.example`.

Then backfill selected NSE stocks:

```bash
python scripts/ingest_angel.py --symbols RELIANCE,TCS,INFY --start 2020-01-01 --end 2026-10-16
```

## Official fundamental ingestion

NSE example:

```bash
python scripts/ingest_exchange_financials.py \
  --nse-symbols RELIANCE,TCS,INFY \
  --from-date 2025-01-01 \
  --to-date 2026-10-16
```

NSE plus BSE verification for selected companies:

```bash
python scripts/ingest_exchange_financials.py \
  --nse-symbols RELIANCE,TCS \
  --bse 500325:RELIANCE \
  --bse 532540:TCS
```

Use `--strict-xbrl` when a production ingestion run should fail rather than retain filing metadata with missing parsed facts when an exchange XBRL download is unavailable.

## Data design principles

1. Raw/provider data is outside strategy logic.
2. Normalized canonical records are the strategy contract.
3. Every time-sensitive record carries the timestamp from which it became knowable.
4. Historical revisions do not overwrite prior knowledge.
5. Backtests read through `as_of` semantics to prevent look-ahead bias.
6. Primary and secondary official sources can be reconciled independently.

## Important

This software is for research and controlled execution workflows. Backtests must use point-in-time data and model costs/slippage. Do not enable unattended live trading until data integrity, reconciliation, kill-switches and paper-trading validation are complete.
