from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Iterable
from xml.etree import ElementTree as ET


INR_PER_CRORE = Decimal("10000000")


@dataclass(frozen=True)
class XbrlFact:
    name: str
    value: Decimal
    unit: str | None
    context_id: str | None
    period_start: date | None
    period_end: date | None
    instant: date | None


# Indian exchange XBRL taxonomies have changed over time. Keep aliases broad and
# resolve by local XML element name rather than hard-coding one namespace URI.
FACT_ALIASES: dict[str, tuple[str, ...]] = {
    "sales": (
        "RevenueFromOperations",
        "RevenueFromOperationsNet",
        "Revenue",
        "TotalRevenue",
        "IncomeFromOperations",
    ),
    "operating_profit": (
        "OperatingProfit",
        "ProfitFromOperationsBeforeOtherIncomeFinanceCostsAndExceptionalItems",
        "ProfitLossFromOperationsBeforeOtherIncomeFinanceCostsAndExceptionalItems",
    ),
    "pat": (
        "ProfitLossForPeriod",
        "ProfitLoss",
        "ProfitAfterTax",
        "NetProfitLossForThePeriod",
    ),
    "diluted_eps": (
        "DilutedEarningsLossPerShare",
        "DilutedEarningsPerShare",
        "DilutedEPS",
    ),
    "pbt": (
        "ProfitBeforeTax",
        "ProfitLossBeforeTax",
        "ProfitBeforeExceptionalItemsAndTax",
    ),
    "other_income": ("OtherIncome",),
    "exceptional_items": (
        "ExceptionalItemsBeforeTax",
        "ExceptionalItems",
        "ExceptionalItem",
    ),
    "operating_cash_flow": (
        "CashFlowsFromUsedInOperatingActivities",
        "NetCashFlowsFromUsedInOperatingActivities",
        "NetCashFromOperatingActivities",
    ),
    "debt": (
        "Borrowings",
        "TotalBorrowings",
        "Debt",
    ),
    "equity": (
        "Equity",
        "TotalEquity",
        "EquityAttributableToOwnersOfParent",
    ),
    "interest_expense": (
        "InterestExpense",
        "FinanceCosts",
        "FinanceCost",
    ),
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()[:10]
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _context_periods(root: ET.Element) -> dict[str, tuple[date | None, date | None, date | None]]:
    contexts: dict[str, tuple[date | None, date | None, date | None]] = {}
    for node in root.iter():
        if _local_name(node.tag) != "context":
            continue
        context_id = node.attrib.get("id")
        if not context_id:
            continue
        start = end = instant = None
        for child in node.iter():
            name = _local_name(child.tag)
            if name == "startDate":
                start = _parse_date(child.text)
            elif name == "endDate":
                end = _parse_date(child.text)
            elif name == "instant":
                instant = _parse_date(child.text)
        contexts[context_id] = (start, end, instant)
    return contexts


def _unit_map(root: ET.Element) -> dict[str, str]:
    units: dict[str, str] = {}
    for node in root.iter():
        if _local_name(node.tag) != "unit":
            continue
        unit_id = node.attrib.get("id")
        if not unit_id:
            continue
        measures = [
            (child.text or "").strip()
            for child in node.iter()
            if _local_name(child.tag) == "measure" and child.text
        ]
        units[unit_id] = "/".join(measures) if measures else unit_id
    return units


def parse_xbrl_facts(xml: bytes | str) -> list[XbrlFact]:
    raw = xml.encode("utf-8") if isinstance(xml, str) else xml
    root = ET.parse(BytesIO(raw)).getroot()
    contexts = _context_periods(root)
    units = _unit_map(root)
    facts: list[XbrlFact] = []

    for node in root.iter():
        context_id = node.attrib.get("contextRef")
        if not context_id or node.text is None:
            continue
        text_value = node.text.strip().replace(",", "")
        if not text_value:
            continue
        try:
            value = Decimal(text_value)
        except InvalidOperation:
            continue
        start, end, instant = contexts.get(context_id, (None, None, None))
        unit_ref = node.attrib.get("unitRef")
        facts.append(
            XbrlFact(
                name=_local_name(node.tag),
                value=value,
                unit=units.get(unit_ref, unit_ref) if unit_ref else None,
                context_id=context_id,
                period_start=start,
                period_end=end,
                instant=instant,
            )
        )
    return facts


def _period_score(fact: XbrlFact, period_end: date) -> int:
    if fact.period_end == period_end:
        return 3
    if fact.instant == period_end:
        return 3
    if fact.period_end and abs((fact.period_end - period_end).days) <= 2:
        return 2
    if fact.instant and abs((fact.instant - period_end).days) <= 2:
        return 2
    return 0


def _is_eps_unit(unit: str | None) -> bool:
    if not unit:
        return False
    lowered = unit.lower()
    return "share" in lowered or "shares" in lowered


def _is_inr_unit(unit: str | None) -> bool:
    if not unit:
        return False
    return "inr" in unit.lower()


def _normalize_value(key: str, fact: XbrlFact) -> Decimal:
    # Canonical monetary storage for PMS fundamentals is INR crore. EPS remains
    # INR/share. XBRL monetary facts with explicit INR units are normally base INR.
    if key == "diluted_eps" or _is_eps_unit(fact.unit):
        return fact.value
    if _is_inr_unit(fact.unit):
        return fact.value / INR_PER_CRORE
    return fact.value


def extract_canonical_financials(
    facts: Iterable[XbrlFact], period_end: date
) -> dict[str, Decimal | None]:
    fact_list = list(facts)
    result: dict[str, Decimal | None] = {key: None for key in FACT_ALIASES}

    for key, aliases in FACT_ALIASES.items():
        candidates = [f for f in fact_list if f.name in aliases and _period_score(f, period_end) > 0]
        if not candidates:
            continue
        candidates.sort(
            key=lambda f: (
                _period_score(f, period_end),
                1 if (key == "diluted_eps" and _is_eps_unit(f.unit)) else 0,
                1 if _is_inr_unit(f.unit) else 0,
                -(f.period_start.toordinal() if f.period_start else 0),
            ),
            reverse=True,
        )
        result[key] = _normalize_value(key, candidates[0])
    return result
