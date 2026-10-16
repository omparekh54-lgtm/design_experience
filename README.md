# Design Experience — Automated PMS

Production-oriented Indian equities PMS research, backtesting, paper-trading and execution platform.

## Current scope

This repository implements the v1 rules defined in `PMS_Rules_v1`: Module A (fundamental eligibility), Module B (rally-pullback technical setup), Module C (readiness ranking), Module E (market regime), and the shared risk/trade engine. Module D (IPO) remains intentionally parked until its rules are defined.

## Architecture

- `apps/api` — FastAPI service
- `packages/pms` — deterministic strategy, risk, trade-state, transaction-cost, regime, ranking, research and backtest logic
- `packages/data_layer` — normalized instruments, OHLCV, official filings, XBRL, reference data, corporate actions and point-in-time queries
- `packages/finstack_gateway` — strategy-facing boundary for point-in-time data access
- `migrations` — PostgreSQL / Supabase point-in-time schema
- `scripts` — ingestion, backfill, dataset export and research/backtest commands
- `config` — versioned strategy and backtest parameters
- `tests` — strategy, data, backtest and research regression tests
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
migrations/006_research_stack.sql
```

Migration 004 adds historical shareholding/pledge data, shares outstanding, sector history, index constituents, corporate actions, earnings calendar, Module A history, Module B signals and trade-state events.

Migration 005 adds persisted trades plus versioned backtest runs, events and fills.

Migration 006 adds daily Module E regime history, Module C rankings/selections, immutable research dataset manifests, research runs and robustness points.

## Phases 1–5 implemented

### Phase 1 — Fully automatic Module A inputs

`ModuleAPipeline` combines point-in-time financial filings with stored shareholding/pledge history, historical shares outstanding, price history, average traded value and ASM/GSM state. Historical market cap is reconstructed as historical close × shares outstanding. Missing hard-filter data fails closed and is surfaced as a flag rather than silently passing.

### Phase 2 — Historical tradable universe

The data layer tracks listing/delisting windows, sector history and index membership intervals. `ReferenceDataQueries.universe_as_of()` reconstructs the exchange universe for a historical date, and `is_index_member()` supports point-in-time exclusions such as NIFTY50.

### Phase 3 — Corporate-action adjustments

`packages/data_layer/corporate_actions.py` preserves raw OHLCV and back-adjusts prices/volume for splits and bonuses. Module B consumes adjusted candles before computing EMA, ADR, 52-week highs and rally statistics so corporate actions cannot create false technical signals.

### Phase 4 — Real-data Module B pipeline

`ModuleBPipeline` pulls stored daily prices and Module A eligibility, applies the documented price/volume/turnover/ADR/52-week-high/NIFTY50/result-blackout filters, runs the rally state machine, and can detect repeated independent Setup-1 signals through history. Module A can be disabled explicitly only for the B-only research variant.

### Phase 5 — Shared trade engine

`packages/pms/trade_engine.py` models signal -> pending entry -> fill -> Leg 1 -> Leg 2 -> close/cancel states. It implements the three-trading-day buy-stop window, gap-up fills with stop/size recalculation, intraday stop handling, 9EMA arming, partial/full Leg-1 decisions, breakeven protection and 21EMA Leg-2 exits. EMA exits are generated after close and executed at the next market open.

## Phases 6–8 implemented

### Phase 6 — Complete exit and re-entry lifecycle

The trade engine preserves the Leg-2 EMA21 exit-signal candle and can create the rule-defined re-entry order from that candle's high/low. Automatic re-entry is available only after a Leg-2 EMA21 exit; stop-loss and Leg-1 loss exits do not create this re-entry path. Re-entry can be switched off for research variants.

### Phase 7 — Event-driven backtester

`packages/pms/backtest.py` processes daily market-open, bar, signal, fill, state-change, re-entry and market-close events. Entries begin only after a close-generated signal. EMA exits execute at the following open. Stops remain intraday. Gap fills, same-day stop ambiguity, cash limits, dynamic Module E capacity and daily marked-to-market equity are modeled.

`DatabaseBacktestStrategy` reuses production Module A/B/C/E, corporate-action-adjusted candles and the shared trade engine. There is no separate simplified backtest strategy.

Run a database-backed test with:

```bash
python scripts/run_backtest.py \
  --symbols NSE:ABC,NSE:XYZ \
  --start 2022-01-01 \
  --end 2026-10-16 \
  --capital 10000000
```

### Phase 8 — Indian transaction-cost model

`packages/pms/costs.py` models delivery-market STT, stamp duty, NSE cash-market transaction charges, SEBI turnover fees, GST, brokerage, slippage and impact cost. Statutory/exchange rates are stored in versioned configuration. Brokerage, slippage and impact assumptions remain configurable.

## Phases 9–14 implemented

### Phase 9 — Module E market regime

`ModuleEPipeline` builds the documented 0–100 regime score from Nifty Midcap 400 trend, breadth above 50DMA/21EMA, 10-day advance/decline, new highs/lows, India VIX level/direction and recent strategy feedback. The engine uses the documented 30/25/15/15/15 weights, a 3-day score average, immediate downgrades, two-close upgrade confirmation and the Midcap400-below-200DMA + breadth-below-30% hard override. Daily capacity is persisted in `market_regime_history` and enforced by the backtester.

### Phase 10 — Module C ranking and allocation

`module_c.py` now applies Stage-1 eligibility and the full 100-point ranking: rally 25, pullback 20, fundamentals 20, relative strength 15, 52-week-high proximity 10 and sector strength 10. It enforces score >=60, 2% ADTV position size, signal age, ADR, existing-position checks, sector limits, cash, portfolio heat, regime capacity and the seven-position cap. Tie-break order is score, lower stop risk, then higher ADTV. Persisted daily selections are consumed by the full research variant.

### Phase 11 — Historical research dataset/backfill

`backfill_research_history.py` coordinates price and official financial backfills, optionally builds Module E snapshots and freezes the result as a Parquet research dataset. `research_dataset.py` exports point-in-time tables to immutable Parquet files plus a manifest containing row counts and SHA-256 checksums.

Run:

```bash
python scripts/backfill_research_history.py \
  --symbols RELIANCE,TCS,INFY \
  --start 2017-01-01 \
  --end 2026-10-16 \
  --out research_data
```

Historical shareholding/pledge, ASM/GSM, index constituents and corporate-action archives must be loaded through `ingest_reference_csv.py`; the system does not invent missing historical reference data.

You can also freeze an already-populated database directly:

```bash
python scripts/export_research_dataset.py --start 2017-01-01 --end 2026-10-16 --out research_data
```

### Phase 12 — Research Lab

`ResearchLab` runs database-backed strategy experiments and computes marked-to-market total return, CAGR, max drawdown, Sharpe, Sortino, Calmar, win rate, profit factor, expectancy, average winner/loser, trade count and holding period.

Run all standard variants:

```bash
python scripts/run_research_lab.py \
  --symbols NSE:RELIANCE,NSE:TCS,NSE:INFY \
  --start 2017-01-01 \
  --end 2026-10-16 \
  --mode variants
```

### Phase 13 — Strategy variants

The Research Lab includes behaviorally distinct variants:

- B only
- B + A
- B + A + C
- full A + B + C + E
- full model without re-entry
- earnings-anchored Setup 2 backtest

`EarningsAnchoredSetup2` implements Stage-3 catalyst gating, pre-result-close base so the gap counts, >=2x reaction volume, >=15% rally within 30 trading sessions, >=1%/day velocity, EMA21 failure signal and 60-session expiry. Setup 2 remains research/backtest-only.

### Phase 14 — Robustness analysis

The robustness engine runs the Cartesian parameter grid requested in the PMS specification. Defaults include ADR 2.5/3/4, rally 12/15/18%, risk 0.25/0.5/0.75% and rally volume 1.25x/1.5x/2x. Every point returns full performance metrics and a comparison score; the engine is designed to find stable parameter regions rather than simply maximize one backtest CAGR.

Run:

```bash
python scripts/run_research_lab.py \
  --symbols NSE:RELIANCE,NSE:TCS,NSE:INFY \
  --start 2017-01-01 \
  --end 2026-10-16 \
  --mode robustness
```

## Market data

Angel One SmartAPI is the primary authenticated source for historical daily OHLCV and quotes. Finstack remains a secondary/fallback source and cross-check layer. Strategy modules depend only on canonical models, not provider-specific response shapes.

## Official NSE/BSE fundamentals

`NSEIntegratedFinancialsProvider` reads NSE Integrated Filing - Financials. `BSEFinancialResultsProvider` reads BSE financial-result/XBRL surfaces. Both normalize into the same point-in-time `FinancialFiling` model with original/revised status, filing timestamps, consolidated/standalone scope and raw provenance.

## Reference-data imports

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
10. Research datasets are immutable and checksummed.

## Important

This software is for research and controlled execution workflows. Backtests must use point-in-time data and model costs/slippage. Do not enable unattended live trading until data integrity, reconciliation, kill-switches and paper-trading validation are complete.
