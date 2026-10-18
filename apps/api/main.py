from __future__ import annotations

import os
from datetime import date, datetime, time
from functools import lru_cache
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from packages.data_layer.reference_data import ReferenceDataQueries
from packages.pms.config import load_strategy_config
from packages.pms.module_a import FundamentalSnapshot, evaluate_fundamentals
from packages.pms.module_a_pipeline import ModuleAPipeline
from packages.pms.module_b_pipeline import ModuleBPipeline
from packages.pms.module_e import decide_regime
from packages.pms.risk import size_position

app = FastAPI(
    title="Design Experience PMS API",
    version="0.3.0",
    description="Point-in-time research/backtest API. Live trading remains disabled.",
)


def _render_safe_database_url(raw: str) -> str:
    """Normalize Supabase URLs for Render's IPv4-compatible session pooler.

    Handles both a direct ``db.<ref>.supabase.co`` URL and an already-configured
    ``*.pooler.supabase.com`` URL whose username is still the plain ``postgres``.
    Password/query/database values are preserved and never logged.
    """
    try:
        url = make_url(raw)
    except Exception:
        return raw

    host = (url.host or "").lower()
    configured_ref = os.getenv("SUPABASE_PROJECT_REF", "").strip()

    if host.startswith("db.") and host.endswith(".supabase.co"):
        parts = host.split(".")
        if len(parts) < 4:
            return raw
        project_ref = parts[1]
        region = os.getenv("SUPABASE_POOLER_REGION", "ap-south-1").strip() or "ap-south-1"
        pooler_host = os.getenv("SUPABASE_POOLER_HOST", f"aws-0-{region}.pooler.supabase.com").strip()
        pooler_user = os.getenv("SUPABASE_POOLER_USER", f"postgres.{project_ref}").strip()
        return url.set(host=pooler_host, port=5432, username=pooler_user).render_as_string(hide_password=False)

    if host.endswith(".pooler.supabase.com"):
        project_ref = configured_ref
        if not project_ref:
            return raw
        current_user = (url.username or "").strip()
        if current_user in {"", "postgres"}:
            pooler_user = os.getenv("SUPABASE_POOLER_USER", f"postgres.{project_ref}").strip()
            return url.set(username=pooler_user).render_as_string(hide_password=False)

    return raw


@lru_cache(maxsize=1)
def _db_engine():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    return create_engine(_render_safe_database_url(url), pool_pre_ping=True)


def db_engine():
    try:
        return _db_engine()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _as_of_end(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59))


class FundamentalRequest(BaseModel):
    market_cap_cr: float
    avg_traded_value_cr: float
    asm_gsm: bool = False
    promoter_pledge_pct: float
    quarterly_sales_cr: float
    pat_cr: float
    sales_yoy_pct: float | None
    operating_profit_yoy_pct: float | None
    pat_yoy_pct: float | None
    eps_yoy_pct: float | None
    positive_growth_quarters_last_4: int
    ttm_sales_rising: bool
    ttm_profit_rising: bool
    ocf_to_pat: float | None
    quality_pass: bool = True
    growth_acceleration_pp: float | None = None
    margin_expansion_bps: float | None = None
    debt_to_equity: float | None = None
    interest_coverage: float | None = None
    record_sales_8q: bool = False
    record_pat_8q: bool = False
    turnaround: bool = False
    triggers: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class PositionRequest(BaseModel):
    capital: float
    entry: float
    signal_low: float


class RegimeRequest(BaseModel):
    score: float
    nifty_midcap_400_above_200dma: bool
    breadth_above_50dma_pct: float


@app.get("/health")
def health() -> dict:
    cfg = load_strategy_config()
    db_status = "configured" if os.getenv("DATABASE_URL") else "not_configured"
    return {"status": "ok", "strategy_version": cfg["version"], "live_trading": False, "database": db_status}


@app.post("/v1/module-a/evaluate")
def module_a_calculation(payload: FundamentalRequest) -> dict:
    cfg = load_strategy_config()
    snapshot = FundamentalSnapshot(
        **payload.model_dump(exclude={"flags", "triggers"}),
        flags=tuple(payload.flags), triggers=tuple(payload.triggers),
    )
    decision = evaluate_fundamentals(snapshot, cfg)
    return {"eligible": decision.eligible, "score": decision.score, "score_components": decision.components,
            "failed_rules": decision.failed_rules, "triggers": decision.triggers, "flags": decision.flags}


@app.post("/v1/risk/position-size")
def position_size(payload: PositionRequest) -> dict:
    cfg = load_strategy_config()["risk"]
    result = size_position(
        capital=payload.capital, entry=payload.entry, signal_low=payload.signal_low,
        risk_per_trade_pct=cfg["risk_per_trade_pct"], max_position_pct=cfg["max_position_pct"],
        raw_stop_buffer_pct=cfg["raw_stop_buffer_pct"], max_stop_risk_pct=cfg["max_stop_risk_pct"],
    )
    return result.__dict__


@app.post("/v1/module-e/regime")
def module_e_calculation(payload: RegimeRequest) -> dict:
    result = decide_regime(payload.score, payload.nifty_midcap_400_above_200dma, payload.breadth_above_50dma_pct, load_strategy_config())
    return result.__dict__


@app.get("/v1/universe")
def universe(as_of: date, exchange: str = "NSE") -> dict:
    symbols = ReferenceDataQueries(db_engine()).universe_as_of(as_of, exchange.upper())
    return {"as_of": as_of, "exchange": exchange.upper(), "count": len(symbols), "instruments": symbols}


@app.get("/v1/module-a/{instrument_id}")
def module_a_from_database(instrument_id: str, as_of: date) -> dict:
    try:
        pipeline = ModuleAPipeline(db_engine())
        snapshot, decision = pipeline.evaluate(instrument_id.upper(), _as_of_end(as_of))
        payload = pipeline.serialize(snapshot, decision)
        return {"instrument_id": instrument_id.upper(), "as_of": as_of, **payload}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/v1/module-b/{instrument_id}")
def module_b_from_database(instrument_id: str, as_of: date, require_module_a: bool = True) -> dict:
    try:
        result = ModuleBPipeline(db_engine()).evaluate(instrument_id.upper(), as_of, require_module_a=require_module_a)
        return {"instrument_id": result.instrument_id, "as_of": result.as_of, "eligible_universe": result.eligible_universe,
                "rejected_reasons": list(result.rejected_reasons), "signal": result.signal}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/v1/module-e/{trade_date}")
def module_e_from_database(trade_date: date) -> dict:
    with db_engine().connect() as conn:
        row = conn.execute(text("select * from market_regime_history where trade_date=:d"), {"d": trade_date}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="regime snapshot not found")
    return dict(row)


@app.get("/v1/module-c")
def module_c_rankings(trade_date: date, selected_only: bool = False, limit: int = Query(100, ge=1, le=1000)) -> dict:
    clause = "and r.selected=true" if selected_only else ""
    with db_engine().connect() as conn:
        rows = conn.execute(text(f"""
            select r.trade_date, i.exchange, coalesce(i.nse_symbol, replace(i.trading_symbol,'-EQ','')) as symbol,
                   r.score, r.qualified, r.selected, r.components, r.rejected_reasons, r.strategy_version
            from module_c_rankings r join instruments i on i.id=r.instrument_id
            where r.trade_date=:d {clause}
            order by r.selected desc, r.score desc limit :limit
        """), {"d": trade_date, "limit": limit}).mappings().all()
    return {"trade_date": trade_date, "count": len(rows), "rankings": [dict(r) for r in rows]}


@app.get("/v1/positions")
def positions(open_only: bool = True) -> dict:
    clause = "where t.state not in ('CLOSED','CANCELLED')" if open_only else ""
    with db_engine().connect() as conn:
        rows = conn.execute(text(f"""
            select t.id, i.exchange, coalesce(i.nse_symbol, replace(i.trading_symbol,'-EQ','')) as symbol,
                   t.signal_date, t.state, t.entry, t.stop, t.shares, t.remaining_shares,
                   t.realized_pnl, t.exit_reason, t.strategy_version, t.updated_at
            from trades t join instruments i on i.id=t.instrument_id
            {clause} order by t.updated_at desc
        """)).mappings().all()
    return {"count": len(rows), "positions": [dict(r) for r in rows]}


@app.get("/v1/portfolio")
def portfolio() -> dict:
    with db_engine().connect() as conn:
        row = conn.execute(text("""
            select count(*) filter (where state not in ('CLOSED','CANCELLED')) as open_positions,
                   coalesce(sum(realized_pnl),0) as realized_pnl,
                   coalesce(sum(case when state not in ('CLOSED','CANCELLED') then entry*remaining_shares else 0 end),0) as gross_open_value
            from trades
        """)).mappings().one()
    return {**dict(row), "live_trading": False}


@app.get("/v1/orders")
def orders() -> dict:
    with db_engine().connect() as conn:
        rows = conn.execute(text("""
            select t.id, i.exchange, coalesce(i.nse_symbol, replace(i.trading_symbol,'-EQ','')) as symbol,
                   t.signal_date, t.state, t.entry, t.stop, t.shares, t.strategy_version
            from trades t join instruments i on i.id=t.instrument_id
            where t.state in ('SIGNAL_CREATED','PENDING_ENTRY') order by t.created_at desc
        """)).mappings().all()
    return {"execution_enabled": False, "count": len(rows), "order_candidates": [dict(r) for r in rows]}


@app.get("/v1/trades")
def trades(limit: int = Query(100, ge=1, le=1000)) -> dict:
    with db_engine().connect() as conn:
        rows = conn.execute(text("""
            select t.id, i.exchange, coalesce(i.nse_symbol, replace(i.trading_symbol,'-EQ','')) as symbol,
                   t.signal_date, t.state, t.entry, t.stop, t.shares, t.remaining_shares,
                   t.realized_pnl, t.exit_reason, t.strategy_version, t.created_at, t.updated_at
            from trades t join instruments i on i.id=t.instrument_id
            order by t.updated_at desc limit :limit
        """), {"limit": limit}).mappings().all()
    return {"count": len(rows), "trades": [dict(r) for r in rows]}


@app.get("/v1/backtests")
def backtests(limit: int = Query(50, ge=1, le=500)) -> dict:
    with db_engine().connect() as conn:
        rows = conn.execute(text("select * from backtest_runs order by created_at desc limit :limit"), {"limit": limit}).mappings().all()
    return {"count": len(rows), "runs": [dict(r) for r in rows]}


@app.get("/v1/backtests/{run_id}")
def backtest_detail(run_id: UUID) -> dict:
    with db_engine().connect() as conn:
        run = conn.execute(text("select * from backtest_runs where id=:id"), {"id": run_id}).mappings().first()
        if not run:
            raise HTTPException(status_code=404, detail="backtest run not found")
        fills = conn.execute(text("select * from backtest_fills where run_id=:id order by trade_date,id"), {"id": run_id}).mappings().all()
    return {"run": dict(run), "fills": [dict(r) for r in fills]}


@app.get("/v1/research/runs")
def research_runs(limit: int = Query(50, ge=1, le=500)) -> dict:
    with db_engine().connect() as conn:
        rows = conn.execute(text("select * from research_runs order by created_at desc limit :limit"), {"limit": limit}).mappings().all()
    return {"count": len(rows), "runs": [dict(r) for r in rows]}


@app.get("/v1/research/stress")
def stress_runs(limit: int = Query(20, ge=1, le=200)) -> dict:
    with db_engine().connect() as conn:
        rows = conn.execute(text("select * from stress_test_runs order by created_at desc limit :limit"), {"limit": limit}).mappings().all()
    return {"count": len(rows), "runs": [dict(r) for r in rows]}


@app.get("/v1/research/walk-forward")
def walk_forward_runs(limit: int = Query(20, ge=1, le=200)) -> dict:
    with db_engine().connect() as conn:
        rows = conn.execute(text("select * from walk_forward_runs order by created_at desc limit :limit"), {"limit": limit}).mappings().all()
    return {"count": len(rows), "runs": [dict(r) for r in rows]}


@app.get("/v1/data-health")
def data_health() -> dict:
    queries = {
        "latest_price_date": "select max(trade_date) from daily_prices",
        "latest_financial_filing": "select max(filing_timestamp) from financial_filings",
        "latest_shareholding_filing": "select max(filed_at) from shareholding_history",
        "latest_regime_date": "select max(trade_date) from market_regime_history",
        "latest_module_c_date": "select max(trade_date) from module_c_rankings",
        "price_rows": "select count(*) from daily_prices",
        "financial_filings": "select count(*) from financial_filings",
        "unresolved_data_quality_errors": "select count(*) from data_quality_issues where severity='ERROR'",
    }
    out = {}
    try:
        with db_engine().connect() as conn:
            for key, sql in queries.items():
                out[key] = conn.execute(text(sql)).scalar_one_or_none()
        out["status"] = "ok" if not out.get("unresolved_data_quality_errors") else "degraded"
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"data health query failed: {exc}") from exc
    return out
