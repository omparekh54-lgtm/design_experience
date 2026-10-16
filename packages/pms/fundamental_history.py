from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable


CORE_GROWTH_METRICS = ("sales", "operating_profit", "pat", "diluted_eps")


@dataclass(frozen=True)
class FinancialPeriod:
    period_end: date
    filed_at: datetime
    filing_kind: str
    consolidated: bool
    source: str
    facts: dict[str, float]
    revision_number: int = 1


@dataclass(frozen=True)
class FinancialHistoryMetrics:
    quarterly_sales_cr: float
    pat_cr: float
    sales_yoy_pct: float
    operating_profit_yoy_pct: float
    pat_yoy_pct: float
    eps_yoy_pct: float
    positive_growth_quarters_last_4: int
    ttm_sales_rising: bool
    ttm_profit_rising: bool
    ocf_to_pat: float | None
    growth_acceleration_pp: float | None
    margin_expansion_bps: float | None
    debt_to_equity: float | None
    interest_coverage: float | None
    record_sales_8q: bool
    record_pat_8q: bool
    turnaround: bool
    quality_pass: bool
    triggers: tuple[str, ...] = field(default_factory=tuple)
    flags: tuple[str, ...] = field(default_factory=tuple)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous <= 0:
        return None
    return (current / previous - 1.0) * 100.0


def _margin(op: float | None, sales: float | None) -> float | None:
    if op is None or sales is None or sales == 0:
        return None
    return op / sales


def _sum_metric(periods: list[FinancialPeriod], key: str) -> float | None:
    values = [_number(p.facts.get(key)) for p in periods]
    if any(v is None for v in values):
        return None
    return sum(v for v in values if v is not None)


def _as_period(row: FinancialPeriod | dict[str, Any]) -> FinancialPeriod:
    if isinstance(row, FinancialPeriod):
        return row
    facts = {k: float(v) for k, v in dict(row.get("facts") or {}).items() if v is not None}
    return FinancialPeriod(
        period_end=row["period_end"],
        filed_at=row["filed_at"],
        filing_kind=str(row.get("filing_kind") or "quarter").lower(),
        consolidated=bool(row.get("consolidated")),
        source=str(row.get("source") or "unknown"),
        facts=facts,
        revision_number=int(row.get("revision_number") or 1),
    )


def _quarter_yoy(quarters: list[FinancialPeriod], index: int, key: str) -> float | None:
    prior_index = index + 4
    if prior_index >= len(quarters):
        return None
    return _pct_growth(
        _number(quarters[index].facts.get(key)),
        _number(quarters[prior_index].facts.get(key)),
    )


def calculate_financial_history(
    history: Iterable[FinancialPeriod | dict[str, Any]],
    *,
    consistency_metrics: tuple[str, ...] = ("sales", "pat"),
    min_ocf_to_pat: float = 0.7,
) -> FinancialHistoryMetrics:
    """Calculate Module A financial history using only point-in-time-selected filings.

    `history` may contain quarterly and annual records, but each period should already
    be de-duplicated to the filing version/scope that was knowable at the requested
    as-of timestamp.

    The rules document says "positive YoY in >=3/4 quarters" without naming a
    specific line item. The implementation therefore makes the consistency metrics
    explicit/configurable; v1 defaults to both Sales and PAT being positive YoY.
    """
    periods = [_as_period(row) for row in history]
    quarters = sorted(
        [p for p in periods if p.filing_kind.lower() not in {"annual", "year", "yearly"}],
        key=lambda p: p.period_end,
        reverse=True,
    )
    annual = sorted(
        [p for p in periods if p.filing_kind.lower() in {"annual", "year", "yearly"}],
        key=lambda p: p.period_end,
        reverse=True,
    )
    if not quarters:
        raise ValueError("at least one quarterly financial filing is required")

    latest = quarters[0]
    flags: list[str] = []
    triggers: list[str] = []

    latest_sales = _number(latest.facts.get("sales"))
    latest_pat = _number(latest.facts.get("pat"))
    if latest_sales is None:
        flags.append("sales_missing")
    if latest_pat is None:
        flags.append("pat_missing")

    yoy: dict[str, float | None] = {key: _quarter_yoy(quarters, 0, key) for key in CORE_GROWTH_METRICS}
    for key, value in yoy.items():
        if value is None:
            flags.append(f"{key}_yoy_unavailable")

    positive_quarters = 0
    for idx in range(min(4, len(quarters))):
        metric_growth = [_quarter_yoy(quarters, idx, key) for key in consistency_metrics]
        if metric_growth and all(value is not None and value > 0 for value in metric_growth):
            positive_quarters += 1
    if len(quarters) < 8:
        flags.append("eight_quarter_history_incomplete")

    current_ttm_sales = _sum_metric(quarters[:4], "sales") if len(quarters) >= 4 else None
    previous_ttm_sales = _sum_metric(quarters[4:8], "sales") if len(quarters) >= 8 else None
    current_ttm_pat = _sum_metric(quarters[:4], "pat") if len(quarters) >= 4 else None
    previous_ttm_pat = _sum_metric(quarters[4:8], "pat") if len(quarters) >= 8 else None
    ttm_sales_rising = (
        current_ttm_sales is not None and previous_ttm_sales is not None and current_ttm_sales > previous_ttm_sales
    )
    ttm_profit_rising = (
        current_ttm_pat is not None and previous_ttm_pat is not None and current_ttm_pat > previous_ttm_pat
    )

    acceleration_values: list[float] = []
    for key in CORE_GROWTH_METRICS:
        current_yoy = yoy[key]
        previous_quarter_yoy = _quarter_yoy(quarters, 1, key) if len(quarters) >= 6 else None
        if current_yoy is not None and previous_quarter_yoy is not None:
            acceleration_values.append(current_yoy - previous_quarter_yoy)
    growth_acceleration = max(acceleration_values) if acceleration_values else None
    if growth_acceleration is not None and growth_acceleration >= 10.0:
        triggers.append("growth_acceleration_10pp")

    current_margin = _margin(_number(latest.facts.get("operating_profit")), latest_sales)
    prior_year_margin = None
    if len(quarters) >= 5:
        prior = quarters[4]
        prior_year_margin = _margin(_number(prior.facts.get("operating_profit")), _number(prior.facts.get("sales")))
    margin_expansion_bps = None
    if current_margin is not None and prior_year_margin is not None:
        margin_expansion_bps = (current_margin - prior_year_margin) * 10_000.0
        if margin_expansion_bps >= 200.0:
            triggers.append("margin_expansion_200bps")
    else:
        flags.append("margin_expansion_unavailable")

    record_sales = False
    record_pat = False
    if len(quarters) >= 8:
        sales_values = [_number(q.facts.get("sales")) for q in quarters[:8]]
        pat_values = [_number(q.facts.get("pat")) for q in quarters[:8]]
        if latest_sales is not None and all(v is not None for v in sales_values[1:]):
            record_sales = latest_sales >= max(v for v in sales_values[1:] if v is not None)
        if latest_pat is not None and all(v is not None for v in pat_values[1:]):
            record_pat = latest_pat >= max(v for v in pat_values[1:] if v is not None)
    if record_sales:
        triggers.append("record_sales_8q")
    if record_pat:
        triggers.append("record_pat_8q")

    turnaround = False
    if len(quarters) >= 5 and latest_pat is not None:
        prior_pat = _number(quarters[4].facts.get("pat"))
        turnaround = prior_pat is not None and prior_pat <= 0 < latest_pat
    if turnaround:
        triggers.append("turnaround")
        flags.append("turnaround_lower_confidence")

    ocf_to_pat: float | None = None
    annual_for_cash = next(
        (
            p
            for p in annual
            if _number(p.facts.get("operating_cash_flow")) is not None and _number(p.facts.get("pat")) is not None
        ),
        None,
    )
    if annual_for_cash:
        annual_pat = _number(annual_for_cash.facts.get("pat"))
        annual_ocf = _number(annual_for_cash.facts.get("operating_cash_flow"))
        if annual_pat is not None and annual_pat > 0 and annual_ocf is not None:
            ocf_to_pat = annual_ocf / annual_pat
    if ocf_to_pat is None:
        flags.append("annual_ocf_to_pat_unavailable")

    debt = _number(latest.facts.get("debt"))
    equity = _number(latest.facts.get("equity"))
    debt_to_equity = debt / equity if debt is not None and equity is not None and equity > 0 else None
    if debt_to_equity is None:
        flags.append("debt_to_equity_unavailable")

    interest = _number(latest.facts.get("interest_expense"))
    op = _number(latest.facts.get("operating_profit"))
    interest_coverage = op / interest if op is not None and interest is not None and interest > 0 else None
    if interest_coverage is None:
        flags.append("interest_coverage_unavailable")

    exceptional = _number(latest.facts.get("exceptional_items"))
    if exceptional is not None and abs(exceptional) > 0:
        flags.append("exceptional_items_present")
    if _number(latest.facts.get("other_income")) is not None:
        flags.append("other_income_review_required")

    # The document calls for sector-adjusted leverage/coverage guards but does
    # not define numerical thresholds. Calculate and expose the ratios without
    # inventing pass/fail cutoffs. Annual OCF/PAT is the objective quality guard
    # that can be enforced from the currently confirmed rules.
    quality_pass = ocf_to_pat is not None and ocf_to_pat >= min_ocf_to_pat

    return FinancialHistoryMetrics(
        quarterly_sales_cr=latest_sales or 0.0,
        pat_cr=latest_pat or 0.0,
        sales_yoy_pct=yoy["sales"] if yoy["sales"] is not None else float("-inf"),
        operating_profit_yoy_pct=(
            yoy["operating_profit"] if yoy["operating_profit"] is not None else float("-inf")
        ),
        pat_yoy_pct=yoy["pat"] if yoy["pat"] is not None else float("-inf"),
        eps_yoy_pct=yoy["diluted_eps"] if yoy["diluted_eps"] is not None else float("-inf"),
        positive_growth_quarters_last_4=positive_quarters,
        ttm_sales_rising=ttm_sales_rising,
        ttm_profit_rising=ttm_profit_rising,
        ocf_to_pat=ocf_to_pat,
        growth_acceleration_pp=growth_acceleration,
        margin_expansion_bps=margin_expansion_bps,
        debt_to_equity=debt_to_equity,
        interest_coverage=interest_coverage,
        record_sales_8q=record_sales,
        record_pat_8q=record_pat,
        turnaround=turnaround,
        quality_pass=quality_pass,
        triggers=tuple(dict.fromkeys(triggers)),
        flags=tuple(dict.fromkeys(flags)),
    )
