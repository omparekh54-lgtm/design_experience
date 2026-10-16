# Design Experience — Automated PMS

Production-oriented Indian equities PMS research, backtesting, paper-trading and execution platform.

## Current scope

This repository implements the v1 rules defined in `PMS_Rules_v1`: Module A (fundamental eligibility), Module B (rally-pullback technical setup), Module C (readiness ranking), Module E (market regime), and the shared risk/trade engine. Module D (IPO) remains intentionally parked until its rules are defined.

## Architecture

- `apps/api` — FastAPI service
- `packages/pms` — deterministic strategy, risk and domain logic
- `packages/data_layer` — normalized instruments, OHLCV, official filings, XBRL, reference data, corporate actions and point-in-time queries
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

Run tests with `pytest -q`.

## Database setup

Apply migrations in order:

```text
migrations/001_initial_schema.sql
migrations/002_data_layer.sql
migrations/003_exchange_financials.sql
migrations/004_phases_1_5.sql
```

Migration 004 adds historical shareholding/pledge data, shares outstanding, sector history, index constituents, corporate actions, earnings calendar, Module A history, Module B signals and trade-state events.

## Phases 1–5 implemented

### Phase 1 — Fully automatic Module A inputs

`ModuleAPipeline` now combines point-in-time financial filings with stored shareholding/pledge history, historical shares outstanding, price history, average traded value and ASM/GSM state. Historical market cap is reconstructed as historical close × shares outstanding. Missing hard-filter data fails closed and is surfaced as a flag rather than silently passing.

Official NSE shareholding/pledge CSV exports can be ingested through `scripts/ingest_reference_csv.py`. This file-import path is intentionally supported because NSE may block some cloud hosts; the scheduler can run the same ingestion from an India/local worker.

### Phase 2 — Historical tradable universe

The data layer now tracks listing/delisting windows, sector history and index membership intervals. `ReferenceDataQueries.universe_as_of()` reconstructs the exchange universe for a historical date, and `is_index_member()` supports point-in-time exclusions such as NIFTY50.

### Phase 3 — Corporate-action adjustments

`packages/data_layer/corporate_actions.py` preserves raw OHLCV and back-adjusts prices/volume for splits and bonuses. Module B consumes adjusted candles before computing EMA, ADR, 52-week highs and rally statistics so corporate actions cannot create false technical signals.

### Phase 4 — Real-data Module B pipeline

`ModuleBPipeline` now pulls stored daily prices and Module A eligibility, applies the documented price/volume/turnover/ADR/52-week-high/NIFTY50/result-blackout filters, runs the rally state machine, and returns a normalized Setup-1 signal from real point-in-time data.

### Phase 5 — Shared trade engine

`packages/pms/trade_engine.py` models signal -> pending entry -> fill -> Leg 1 -> Leg 2 -> close/cancel states. It implements the three-trading-day buy-stop window, gap-up fills with stop/size recalculation, intraday stop handling, 9EMA arming, partial/full Leg-1 decisions, breakeven protection, 21EMA Leg-2 exit and re-entry eligibility. EMA exits are generated after close and executed at the next market open.

## Market data

Angel One SmartAPI is the primary authenticated source for historical daily OHLCV and quotes. Finstack remains a secondary/fallback source and cross-check layer. Strategy modules depend only on canonical models, not provider-specific response shapes.

## Official NSE/BSE fundamentals

`NSEIntegratedFinancialsProvider` reads NSE Integrated Filing - Financials. `BSEFinancialResultsProvider` reads BSE financial-result/XBRL surfaces. Both normalize into the same point-in-time `FinancialFiling` model with original/revised status, filing timestamps, consolidated/standalone scope and raw provenance.

Example:

```bash
python scripts/ingest_exchange_financials.py \
  --nse-symbols RELIANCE,TCS,INFY \
  --from-date 2025-01-01 \
  --to-date 2026-10-16
```

## Reference-data imports

Examples:

```bash
python scripts/ingest_reference_csv.py \
  --shareholding RELIANCE:/data/reliance_shareholding.csv \
  --pledge RELIANCE:/data/reliance_pledge.csv \
  --index NIFTY50:2026-10-01:/data/nifty50.csv \
  --asm 2026-10-16:/data/asm.csv
```

Corporate-action exports can be loaded with `--corporate-actions SYMBOL:FILE` and GSM files with `--gsm DATE:FILE`.

## Data design principles

1. Raw/provider data remains outside strategy logic.
2. Normalized canonical records are the strategy contract.
3. Every time-sensitive record carries the timestamp from which it became knowable.
4. Historical revisions do not overwrite prior knowledge.
5. Backtests use `as_of` semantics to prevent look-ahead bias.
6. Primary and secondary official sources can be reconciled independently.
7. Missing hard-filter data fails closed.

## Important

This software is for research and controlled execution workflows. Backtests must use point-in-time data and model costs/slippage. Do not enable unattended live trading until data integrity, reconciliation, kill-switches and paper-trading validation are complete.
