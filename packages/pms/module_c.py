from __future__ import annotations

from dataclasses import dataclass


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


def _bounded_component(value_0_to_100: float, max_points: float) -> float:
    normalized = max(0.0, min(100.0, value_0_to_100)) / 100.0
    return normalized * max_points


def rank_candidate(inputs: RankingInputs, cfg: dict) -> RankingDecision:
    r = cfg["ranking"]
    score = sum(
        (
            _bounded_component(inputs.rally_quality, r["rally_quality_points"]),
            _bounded_component(inputs.pullback_quality, r["pullback_quality_points"]),
            _bounded_component(inputs.fundamental_strength, r["fundamental_strength_points"]),
            _bounded_component(inputs.relative_strength, r["relative_strength_points"]),
            _bounded_component(inputs.proximity_52w_high, r["proximity_52w_high_points"]),
            _bounded_component(inputs.sector_strength, r["sector_strength_points"]),
        )
    )
    score = round(score, 2)
    return RankingDecision(score=score, qualified=score >= r["minimum_score"])
