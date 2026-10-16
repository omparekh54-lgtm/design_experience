from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from packages.pms.config import load_strategy_config
from packages.pms.module_a import FundamentalSnapshot, evaluate_fundamentals
from packages.pms.module_e import decide_regime
from packages.pms.risk import size_position

app = FastAPI(title="Design Experience PMS API", version="0.1.0")


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
    triggers: list[str] = []
    flags: list[str] = []


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
    return {
        "status": "ok",
        "strategy_version": cfg["version"],
        "live_trading": False,
    }


@app.post("/v1/module-a/evaluate")
def module_a(payload: FundamentalRequest) -> dict:
    cfg = load_strategy_config()
    snapshot = FundamentalSnapshot(
        **payload.model_dump(exclude={"flags", "triggers"}),
        flags=tuple(payload.flags),
        triggers=tuple(payload.triggers),
    )
    decision = evaluate_fundamentals(snapshot, cfg)
    return {
        "eligible": decision.eligible,
        "score": decision.score,
        "score_components": decision.components,
        "failed_rules": decision.failed_rules,
        "triggers": decision.triggers,
        "flags": decision.flags,
    }


@app.post("/v1/risk/position-size")
def position_size(payload: PositionRequest) -> dict:
    cfg = load_strategy_config()["risk"]
    result = size_position(
        capital=payload.capital,
        entry=payload.entry,
        signal_low=payload.signal_low,
        risk_per_trade_pct=cfg["risk_per_trade_pct"],
        max_position_pct=cfg["max_position_pct"],
        raw_stop_buffer_pct=cfg["raw_stop_buffer_pct"],
        max_stop_risk_pct=cfg["max_stop_risk_pct"],
    )
    return result.__dict__


@app.post("/v1/module-e/regime")
def module_e(payload: RegimeRequest) -> dict:
    cfg = load_strategy_config()
    result = decide_regime(
        score=payload.score,
        nifty_midcap_400_above_200dma=payload.nifty_midcap_400_above_200dma,
        breadth_above_50dma_pct=payload.breadth_above_50dma_pct,
        cfg=cfg,
    )
    return result.__dict__
