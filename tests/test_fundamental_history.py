from datetime import date, datetime

import pytest

from packages.pms.config import load_strategy_config
from packages.pms.fundamental_history import FinancialPeriod, calculate_financial_history
from packages.pms.module_a import FundamentalSnapshot, evaluate_fundamentals


def q(period_end, sales, op, pat, eps, **extra):
    facts = {
        "sales": sales,
        "operating_profit": op,
        "pat": pat,
        "diluted_eps": eps,
        **extra,
    }
    return FinancialPeriod(
        period_end=period_end,
        filed_at=datetime.combine(period_end, datetime.min.time()),
        filing_kind="quarter",
        consolidated=True,
        source="NSE",
        facts=facts,
    )


def test_calculates_eight_quarter_module_a_history():
    history = [
        q(date(2026, 9, 30), 200, 50, 30, 3.0, debt=100, equity=250, interest_expense=5),
        q(date(2026, 6, 30), 180, 42, 25, 2.5),
        q(date(2026, 3, 31), 170, 38, 22, 2.2),
        q(date(2025, 12, 31), 160, 35, 20, 2.0),
        q(date(2025, 9, 30), 140, 28, 18, 1.8),
        q(date(2025, 6, 30), 135, 27, 17, 1.7),
        q(date(2025, 3, 31), 130, 26, 16, 1.6),
        q(date(2024, 12, 31), 125, 25, 15, 1.5),
        FinancialPeriod(
            period_end=date(2026, 3, 31),
            filed_at=datetime(2026, 5, 15),
            filing_kind="annual",
            consolidated=True,
            source="NSE",
            facts={"operating_cash_flow": 100.0, "pat": 80.0},
        ),
    ]

    m = calculate_financial_history(history)

    assert m.quarterly_sales_cr == 200
    assert m.pat_cr == 30
    assert m.sales_yoy_pct == pytest.approx(42.8571, rel=1e-4)
    assert m.operating_profit_yoy_pct == pytest.approx(78.5714, rel=1e-4)
    assert m.pat_yoy_pct == pytest.approx(66.6667, rel=1e-4)
    assert m.eps_yoy_pct == pytest.approx(66.6667, rel=1e-4)
    assert m.positive_growth_quarters_last_4 == 4
    assert m.ttm_sales_rising is True
    assert m.ttm_profit_rising is True
    assert m.ocf_to_pat == pytest.approx(1.25)
    assert m.growth_acceleration_pp is not None and m.growth_acceleration_pp >= 10
    assert m.margin_expansion_bps == pytest.approx(500)
    assert m.debt_to_equity == pytest.approx(0.4)
    assert m.interest_coverage == pytest.approx(10.0)
    assert m.record_sales_8q is True
    assert m.record_pat_8q is True
    assert "growth_acceleration_10pp" in m.triggers
    assert "margin_expansion_200bps" in m.triggers


def test_module_a_uses_history_triggers_for_score_not_eligibility():
    cfg = load_strategy_config()
    snapshot = FundamentalSnapshot(
        market_cap_cr=2500,
        avg_traded_value_cr=10,
        asm_gsm=False,
        promoter_pledge_pct=0,
        quarterly_sales_cr=200,
        pat_cr=30,
        sales_yoy_pct=40,
        operating_profit_yoy_pct=50,
        pat_yoy_pct=60,
        eps_yoy_pct=55,
        positive_growth_quarters_last_4=4,
        ttm_sales_rising=True,
        ttm_profit_rising=True,
        ocf_to_pat=1.2,
        quality_pass=True,
        growth_acceleration_pp=12,
        margin_expansion_bps=250,
        record_sales_8q=True,
    )
    decision = evaluate_fundamentals(snapshot, cfg)
    assert decision.eligible is True
    assert decision.components["acceleration"] == 30
    assert decision.components["margin"] == 15
    assert "growth_acceleration_10pp" in decision.triggers


def test_missing_annual_cash_flow_fails_quality_guard_explicitly():
    history = [
        q(date(2026, 9, 30), 200, 50, 30, 3.0),
        q(date(2026, 6, 30), 180, 42, 25, 2.5),
        q(date(2026, 3, 31), 170, 38, 22, 2.2),
        q(date(2025, 12, 31), 160, 35, 20, 2.0),
        q(date(2025, 9, 30), 140, 28, 18, 1.8),
        q(date(2025, 6, 30), 135, 27, 17, 1.7),
        q(date(2025, 3, 31), 130, 26, 16, 1.6),
        q(date(2024, 12, 31), 125, 25, 15, 1.5),
    ]
    m = calculate_financial_history(history)
    assert m.ocf_to_pat is None
    assert m.quality_pass is False
    assert "annual_ocf_to_pat_unavailable" in m.flags
