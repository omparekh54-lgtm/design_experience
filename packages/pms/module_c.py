from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RankingInputs:
    rally_quality: float
    pullback_quality: float
    fundamental_strength: float
    relative_strength: float
    proximity_52w_high: float
    sector_strength: float


@dataclass(frozen=True)
class RankingDecision:
    score: float
    qualified: bool
    components: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Candidate:
    instrument_id: str
    sector: str
    module_a_eligible: bool
    blackout_clear: bool
    signal_age_trading_days: int
    adr20_pct: float
    position_value: float
    adtv: float
    already_held_or_pending: bool
    shares: int
    stop_risk_pct: float
    ranking: RankingInputs


@dataclass(frozen=True)
class AllocationState:
    open_positions: int
    pending_entries: int
    portfolio_heat_pct: float
    cash: float
    sector_counts: dict[str, int]


@dataclass(frozen=True)
class AllocationDecision:
    selected: tuple[Candidate, ...]
    rejected: dict[str, tuple[str, ...]]


def _bounded_component(value_0_to_100: float, max_points: float) -> float:
    normalized = max(0.0, min(100.0, value_0_to_100)) / 100.0
    return normalized * max_points


def rank_candidate(inputs: RankingInputs, cfg: dict) -> RankingDecision:
    r = cfg["ranking"]
    components = {
        "rally_quality": _bounded_component(inputs.rally_quality, r["rally_quality_points"]),
        "pullback_quality": _bounded_component(inputs.pullback_quality, r["pullback_quality_points"]),
        "fundamental_strength": _bounded_component(inputs.fundamental_strength, r["fundamental_strength_points"]),
        "relative_strength": _bounded_component(inputs.relative_strength, r["relative_strength_points"]),
        "proximity_52w_high": _bounded_component(inputs.proximity_52w_high, r["proximity_52w_high_points"]),
        "sector_strength": _bounded_component(inputs.sector_strength, r["sector_strength_points"]),
    }
    components = {k: round(v, 2) for k, v in components.items()}
    score = round(sum(components.values()), 2)
    return RankingDecision(score=score, qualified=score >= r["minimum_score"], components=components)


def candidate_rejections(candidate: Candidate, cfg: dict) -> list[str]:
    reasons: list[str] = []
    if not candidate.module_a_eligible:
        reasons.append("module_a_ineligible")
    if not candidate.blackout_clear:
        reasons.append("earnings_blackout")
    if candidate.signal_age_trading_days < 1 or candidate.signal_age_trading_days > int(cfg["technical"]["entry_window_days"]):
        reasons.append("signal_expired")
    if candidate.adr20_pct < float(cfg["technical"]["min_adr_20d_pct"]):
        reasons.append("adr")
    if candidate.adtv <= 0 or candidate.position_value > candidate.adtv * 0.02:
        reasons.append("position_gt_2pct_adtv")
    if candidate.already_held_or_pending:
        reasons.append("already_held_or_pending")
    if candidate.shares < 1:
        reasons.append("position_size_zero")
    if not rank_candidate(candidate.ranking, cfg).qualified:
        reasons.append("score_below_minimum")
    return reasons


def allocate_candidates(
    candidates: list[Candidate],
    state: AllocationState,
    max_open_trades_from_regime: int,
    cfg: dict,
) -> AllocationDecision:
    """Rank and allocate top-down under Module C and portfolio constraints."""
    rejected: dict[str, tuple[str, ...]] = {}
    eligible: list[tuple[Candidate, RankingDecision]] = []
    for c in candidates:
        reasons = candidate_rejections(c, cfg)
        if reasons:
            rejected[c.instrument_id] = tuple(dict.fromkeys(reasons))
        else:
            eligible.append((c, rank_candidate(c.ranking, cfg)))

    # Score desc, then lower stop risk, then higher traded value.
    eligible.sort(key=lambda item: (-item[1].score, item[0].stop_risk_pct, -item[0].adtv))

    selected: list[Candidate] = []
    sector_counts = dict(state.sector_counts)
    open_plus_pending = state.open_positions + state.pending_entries
    heat = float(state.portfolio_heat_pct)
    cash = float(state.cash)
    risk_cfg = cfg["risk"]
    max_total = min(int(risk_cfg["max_open_trades"]), int(max_open_trades_from_regime))

    for c, _ in eligible:
        reasons: list[str] = []
        if open_plus_pending >= max_total:
            reasons.append("capacity")
        if sector_counts.get(c.sector, 0) >= int(risk_cfg["max_positions_per_sector"]):
            reasons.append("sector_limit")
        incremental_heat = min(float(risk_cfg["risk_per_trade_pct"]), c.stop_risk_pct * c.position_value / max(cash + c.position_value, 1.0) * 100.0)
        if heat + incremental_heat > float(risk_cfg["max_portfolio_heat_pct"]):
            reasons.append("portfolio_heat")
        if c.position_value > cash:
            reasons.append("cash")
        if reasons:
            rejected[c.instrument_id] = tuple(reasons)
            continue

        selected.append(c)
        open_plus_pending += 1
        heat += incremental_heat
        cash -= c.position_value
        sector_counts[c.sector] = sector_counts.get(c.sector, 0) + 1

    return AllocationDecision(tuple(selected), rejected)
