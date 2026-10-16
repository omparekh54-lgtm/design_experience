# Design Experience — Automated PMS

Production-oriented Indian equities PMS research, backtesting, paper-trading and execution platform.

## Current scope

This repository implements the v1 rules defined in `PMS_Rules_v1`: Module A (fundamental eligibility), Module B (rally-pullback technical setup), Module C (readiness ranking), Module E (market regime), and the shared risk/trade engine. Module D (IPO) remains intentionally parked until its rules are defined.

## Architecture

- `apps/api` — FastAPI service
- `packages/pms` — deterministic strategy, risk, trade-state, transaction-cost and backtest logic
- `packages/data_layer` — normalized instruments, OHLCV, official filings, XBRL, reference data, corporate actions and point-in-time queries
- `packages/finstack_gateway` — strategy-facing boundary for point-in-time data access
- `migrations` — PostgreSQL / Supabase point-in-time schema
- `scripts` — ingestion and research/backtest commands
- `config` — versioned strategy and backtest parameters
- `tests` — strategy, data and backtest regression tests
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
migrations/005_backtest_engine.sql
```

Migration 004 adds historical shareholding/pledge data, shares outstanding, sector history, index constituents, corporate actions, earnings calendar, Module A history, Module B signals and trade-state events.

Migration 005 adds persisted trades plus versioned backtest runs, events and fills.

## Phases 1–5 implemented

### Phase 1 — Fully automatic Module A inputs

`ModuleAPipeline` combines point-in-time financial filings with stored shareholding/pledge history, historical shares outstanding, price history, average traded value and ASM/GSM state. Historical market cap is reconstructed as historical close × shares outstanding. Missing hard-filter data fails closed and is surfaced as a flag rather than silently passing.

### Phase 2 — Historical tradable universe

The data layer tracks listing/delisting windows, sector history and index membership intervals. `ReferenceDataQueries.universe_as_of()` reconstructs the exchange universe for a historical date, and `is_index_member()` supports point-in-time exclusions such as NIFTY50.

### Phase 3 — Corporate-action adjustments

`packages/data_layer/corporate_actions.py` preserves raw OHLCV and back-adjusts prices/volume for splits and bonuses. Module B consumes adjusted candles before computing EMA, ADR, 52-week highs and rally statistics so corporate actions cannot create false technical signals.

### Phase 4 — Real-data Module B pipeline

`ModuleBPipeline` pulls stored daily prices and Module A eligibility, applies the documented price/volume/turnover/ADR/52-week-high/NIFTY50/result-blackout filters, runs the rally state machine, and can detect repeated independent Setup-1 signals through history.

### Phase 5 — Shared trade engine

`packages/pms/trade_engine.py` models signal -> pending entry -> fill -> Leg 1 -> Leg 2 -> close/cancel states. It implements the three-trading-day buy-stop window, gap-up fills with stop/size recalculation, intraday stop handling, 9EMA arming, partial/full Leg-1 decisions, breakeven protection and 21EMA Leg-2 exits. EMA exits are generated after close and executed at the next market open.

## Phases 6–8 implemented

### Phase 6 — Complete exit and re-entry lifecycle

The trade engine preserves the Leg-2 EMA21 exit-signal candle and can create the rule-defined re-entry order from that candle's high/low. Automatic re-entry is available only after a Leg-2 EMA21 exit; stop-loss and Leg-1 loss exits do not create this re-entry path. The backtester reuses this exact state machine and can require point-in-time Module A eligibility before activating the re-entry.

### Phase 7 — Event-driven backtester

`packages/pms/backtest.py` processes daily market-open, bar, signal, fill, state-change, re-entry and market-close events. Entries begin only after a close-generated signal. EMA exits execute at the following open. Stops remain intraday. Gap fills, same-day stop ambiguity (configurable conservative mode), cash limits and the seven-position capacity guard are modeled.

`DatabaseBacktestStrategy` reuses production `ModuleAPipeline`, `ModuleBPipeline`, corporate-action-adjusted candles and the shared trade engine. There is no separate simplified backtest strategy.

Run a database-backed test with:

```bash
python scripts/run_backtest.py \
  --symbols NSE:ABC,NSE:XYZ \
  --start 2022-01-01 \
  --end 2026-10-16 \
  --capital 10000000
```

The result is persisted by default to `backtest_runs`, `backtest_events` and `backtest_fills`. Add `--no-persist` for an ad-hoc run.

### Phase 8 — Indian transaction-cost model

`packages/pms/costs.py` models delivery-market STT, stamp duty, NSE cash-market transaction charges, SEBI turnover fees, GST, brokerage, slippage and impact cost. Statutory/exchange rates are stored in versioned configuration, with dated NSE cash-market charge handling. Brokerage, slippage and impact assumptions are deliberately configurable.

Default research assumptions are in `config/strategy_v1.yaml` under `backtest`.

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
8. Backtest and live/paper execution share the same trade-state logic.
9. Costs and strategy configuration are versioned with the run.

## Important

This software is for research and controlled execution workflows. Backtests must use point-in-time data and model costs/slippage. Do not enable unattended live trading until data integrity, reconciliation, kill-switches and paper-trading validation are complete.
