from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class FundamentalSnapshot:
    market_cap_cr: float
    avg_traded_value_cr: float
    asm_gsm: bool
    promoter_pledge_pct: float
    quarterly_sales_cr: float
    pat_cr: float
    sales_yoy_pct: float
    operating_profit_yoy_pct: float
    pat_yoy_pct: float
    eps_yoy_pct: float
    positive_growth_quarters_last_4: int
    ttm_sales_rising: bool
    ttm_profit_rising: bool
    ocf_to_pat: float
    quality_pass: bool = True
    flags: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FundamentalDecision:
    eligible: bool
    score: float
    failed_rules: tuple[str, ...]
    flags: tuple[str, ...]


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def evaluate_fundamentals(snapshot: FundamentalSnapshot, cfg: dict) -> FundamentalDecision:
    f = cfg["fundamental"]
    failed: list[str] = []

    hard_checks = (
        (snapshot.market_cap_cr > f["min_market_cap_cr"], "market_cap"),
        (snapshot.avg_traded_value_cr >= f["min_avg_traded_value_cr"], "liquidity"),
        (not snapshot.asm_gsm, "asm_gsm"),
        (snapshot.promoter_pledge_pct <= f["max_promoter_pledge_pct"], "promoter_pledge"),
        (snapshot.quarterly_sales_cr >= f["min_quarterly_sales_cr"], "minimum_sales_base"),
        ((snapshot.pat_cr > 0) if f["require_positive_pat"] else True, "positive_pat"),
    )
    for passed, name in hard_checks:
        if not passed:
            failed.append(name)

    growth_checks = (
        (snapshot.sales_yoy_pct >= f["min_sales_yoy_pct"], "sales_yoy"),
        (snapshot.operating_profit_yoy_pct >= f["min_operating_profit_yoy_pct"], "operating_profit_yoy"),
        (snapshot.pat_yoy_pct >= f["min_pat_yoy_pct"], "pat_yoy"),
        (snapshot.eps_yoy_pct >= f["min_eps_yoy_pct"], "eps_yoy"),
        (snapshot.positive_growth_quarters_last_4 >= f["min_positive_quarters_last_4"], "growth_consistency"),
        (snapshot.ttm_sales_rising, "ttm_sales"),
        (snapshot.ttm_profit_rising, "ttm_profit"),
    )
    for passed, name in growth_checks:
        if not passed:
            failed.append(name)

    if snapshot.ocf_to_pat < f["min_ocf_to_pat"]:
        failed.append("ocf_to_pat")
    if not snapshot.quality_pass:
        failed.append("quality_guards")

    growth_level = (
        min(snapshot.sales_yoy_pct / max(f["min_sales_yoy_pct"], 1), 2)
        + min(snapshot.operating_profit_yoy_pct / max(f["min_operating_profit_yoy_pct"], 1), 2)
        + min(snapshot.pat_yoy_pct / max(f["min_pat_yoy_pct"], 1), 2)
        + min(snapshot.eps_yoy_pct / max(f["min_eps_yoy_pct"], 1), 2)
    ) / 8 * 40
    consistency = min(snapshot.positive_growth_quarters_last_4 / 4, 1) * 20
    cash_quality = min(snapshot.ocf_to_pat / max(f["min_ocf_to_pat"], 0.01), 1.5) / 1.5 * 20
    base_quality = 20 if snapshot.quality_pass else 0
    score = round(_clamp(growth_level + consistency + cash_quality + base_quality), 2)

    return FundamentalDecision(
        eligible=not failed,
        score=score,
        failed_rules=tuple(failed),
        flags=tuple(snapshot.flags),
    )
