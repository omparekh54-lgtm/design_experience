from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from statistics import median
from typing import Any

from sqlalchemy import Engine, text

from packages.data_layer.queries import PointInTimeQueries
from packages.pms.config import load_strategy_config
from packages.pms.module_a_pipeline import ModuleAPipeline
from packages.pms.module_b_pipeline import ModuleBPipeline
from packages.pms.module_c import (
    AllocationState,
    Candidate,
    RankingInputs,
    allocate_candidates,
    rank_candidate,
)
from packages.pms.risk import size_position


@dataclass(frozen=True)
class ModuleCGenerationResult:
    trade_date: date
    universe_size: int
    a_eligible: int
    b_signals: int
    ranked: int
    qualified: int
    selected: int
    rejected: int
    regime_capacity: int


def _clamp100(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _percentile_map(values: dict[int, float], *, reverse: bool = False) -> dict[int, float]:
    """Return deterministic 0..100 percentile ranks, averaging ties.

    Higher raw values rank better by default. ``reverse=True`` makes smaller raw
    values better (used for stop width). A one-item cross section receives a
    neutral 50 rather than an artificial 100.
    """
    if not values:
        return {}
    if len(values) == 1:
        key = next(iter(values))
        return {key: 50.0}

    ordered = sorted(values.items(), key=lambda kv: (kv[1], kv[0]), reverse=not reverse)
    n = len(ordered)
    out: dict[int, float] = {}
    i = 0
    while i < n:
        j = i
        raw = ordered[i][1]
        while j + 1 < n and ordered[j + 1][1] == raw:
            j += 1
        avg_position = (i + j) / 2.0
        score = 100.0 * (1.0 - avg_position / (n - 1))
        for k in range(i, j + 1):
            out[ordered[k][0]] = round(score, 4)
        i = j + 1
    return out


class ModuleCPipeline:
    """Generate and persist daily Module C rankings from Modules A/B and portfolio state.

    This pipeline is deliberately fail-closed:
    * instruments without a trustworthy sector are not admitted to the candidate universe;
    * Financial Services is excluded per PMS v1 scope;
    * Module A must pass before Module B is evaluated;
    * only a valid Setup-1 signal can enter Module C;
    * allocation uses the persisted market-regime capacity plus the shared risk limits.
    """

    def __init__(self, engine: Engine):
        self.engine = engine
        self.cfg = load_strategy_config()
        self.a = ModuleAPipeline(engine)
        self.b = ModuleBPipeline(engine)
        self.prices = PointInTimeQueries(engine)

    @staticmethod
    def _as_of_end(d: date) -> datetime:
        return datetime.combine(d, time(23, 59, 59))

    def _universe(self, d: date) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    select i.id,
                           i.exchange,
                           coalesce(i.nse_symbol, replace(i.trading_symbol,'-EQ','')) as symbol,
                           i.sector
                    from instruments i
                    where i.exchange='NSE'
                      and i.active=true
                      and (i.listed_from is null or i.listed_from<=:d)
                      and (i.delisted_on is null or i.delisted_on>:d)
                      and i.sector is not null
                      and btrim(i.sector)<>''
                      and upper(btrim(i.sector)) <> 'FINANCIAL SERVICES'
                      and exists (
                        select 1 from daily_prices p
                        where p.instrument_id=i.id and p.trade_date<=:d
                      )
                    order by symbol
                    """
                ),
                {"d": d},
            ).mappings().all()
        return [dict(r) for r in rows]

    def _market_stats(self, instrument_pk: int, d: date) -> dict[str, float] | None:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    select trade_date, high, low, close, volume
                    from daily_prices
                    where instrument_id=:id and trade_date<=:d
                    order by trade_date desc
                    limit 252
                    """
                ),
                {"id": instrument_pk, "d": d},
            ).mappings().all()
        if len(rows) < 63:
            return None
        latest = rows[0]
        sixty_third = rows[min(62, len(rows) - 1)]
        latest_close = float(latest["close"])
        old_close = float(sixty_third["close"])
        return_63 = ((latest_close / old_close) - 1.0) * 100.0 if old_close > 0 else 0.0
        high_52w = max(float(r["high"]) for r in rows)
        distance_52w = ((high_52w - latest_close) / high_52w * 100.0) if high_52w > 0 else 100.0
        last20 = rows[:20]
        adr20 = (
            sum(100.0 * ((float(r["high"]) / float(r["low"])) - 1.0) for r in last20 if float(r["low"]) > 0)
            / max(sum(1 for r in last20 if float(r["low"]) > 0), 1)
        )
        adtv = sum(float(r["close"]) * float(r["volume"]) for r in rows[:63]) / min(63, len(rows))
        return {
            "close": latest_close,
            "return_63": return_63,
            "distance_52w": distance_52w,
            "adr20": adr20,
            "adtv": adtv,
        }

    def _signal_age_sessions(self, instrument_pk: int, signal_date: date, d: date) -> int:
        with self.engine.connect() as conn:
            value = conn.execute(
                text(
                    """
                    select count(distinct trade_date)
                    from daily_prices
                    where instrument_id=:id
                      and trade_date between :s and :d
                    """
                ),
                {"id": instrument_pk, "s": signal_date, "d": d},
            ).scalar_one()
        return int(value or 0)

    def _portfolio_state(self, d: date) -> tuple[AllocationState, float, set[int]]:
        cfg = self.cfg
        initial = float(cfg.get("backtest", {}).get("initial_capital", 10_000_000))
        with self.engine.connect() as conn:
            snap = conn.execute(
                text(
                    """
                    select cash,nav,portfolio_heat_pct
                    from portfolio_snapshots
                    where account_mode='PAPER' and snapshot_date<=:d
                    order by snapshot_date desc, created_at desc
                    limit 1
                    """
                ),
                {"d": d},
            ).mappings().first()
            trades = conn.execute(
                text(
                    """
                    select t.instrument_id,t.state,coalesce(i.sector,'') sector
                    from trades t
                    join instruments i on i.id=t.instrument_id
                    where t.state not in ('CLOSED','CANCELLED')
                    """
                )
            ).mappings().all()

        cash = float(snap["cash"]) if snap else initial
        nav = float(snap["nav"]) if snap and snap["nav"] is not None else initial
        heat = float(snap["portfolio_heat_pct"]) if snap and snap["portfolio_heat_pct"] is not None else 0.0
        pending_states = {"SIGNAL_CREATED", "PENDING_ENTRY"}
        pending = sum(1 for r in trades if str(r["state"]).upper() in pending_states)
        open_positions = sum(1 for r in trades if str(r["state"]).upper() not in pending_states)
        sector_counts: dict[str, int] = {}
        held_or_pending: set[int] = set()
        for r in trades:
            held_or_pending.add(int(r["instrument_id"]))
            sector = str(r["sector"] or "").strip()
            if sector:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
        return (
            AllocationState(
                open_positions=open_positions,
                pending_entries=pending,
                portfolio_heat_pct=heat,
                cash=cash,
                sector_counts=sector_counts,
            ),
            nav,
            held_or_pending,
        )

    def _regime_capacity(self, d: date) -> int:
        with self.engine.connect() as conn:
            value = conn.execute(
                text("select max_open_trades from market_regime_history where trade_date=:d"),
                {"d": d},
            ).scalar_one_or_none()
        if value is None:
            return 0
        return int(value)

    def _persist(
        self,
        d: date,
        rows: list[dict[str, Any]],
        selected_ids: set[int],
        allocation_rejections: dict[str, tuple[str, ...]],
    ) -> None:
        version = str(self.cfg["version"])
        sql = text(
            """
            insert into module_c_rankings(
                trade_date,instrument_id,score,qualified,selected,
                components,rejected_reasons,strategy_version
            ) values (
                :trade_date,:instrument_id,:score,:qualified,:selected,
                cast(:components as jsonb),cast(:rejected_reasons as jsonb),:strategy_version
            )
            on conflict (trade_date,instrument_id,strategy_version)
            do update set score=excluded.score,
                          qualified=excluded.qualified,
                          selected=excluded.selected,
                          components=excluded.components,
                          rejected_reasons=excluded.rejected_reasons
            """
        )
        payloads = []
        for row in rows:
            reasons = list(row.get("rejected_reasons") or [])
            reasons.extend(allocation_rejections.get(row["instrument_key"], ()))
            reasons = list(dict.fromkeys(reasons))
            payloads.append(
                {
                    "trade_date": d,
                    "instrument_id": row["instrument_pk"],
                    "score": row["score"],
                    "qualified": bool(row["qualified"]),
                    "selected": row["instrument_pk"] in selected_ids,
                    "components": json.dumps(row["components"], default=str),
                    "rejected_reasons": json.dumps(reasons),
                    "strategy_version": version,
                }
            )
        if not payloads:
            return
        with self.engine.begin() as conn:
            conn.execute(sql, payloads)

    def generate(self, d: date) -> ModuleCGenerationResult:
        universe = self._universe(d)
        as_of = self._as_of_end(d)
        a_pass = 0
        raw: list[dict[str, Any]] = []

        for item in universe:
            instrument_key = f"NSE:{item['symbol']}"
            try:
                _, a_decision = self.a.evaluate(instrument_key, as_of)
            except Exception:
                continue
            if not a_decision.eligible:
                continue
            a_pass += 1
            try:
                b_result = self.b.evaluate(instrument_key, d, require_module_a=True)
            except Exception:
                continue
            if getattr(b_result,'research_only',False) or not b_result.eligible_universe or not b_result.signal:
                continue

            stats = self._market_stats(int(item["id"]), d)
            if stats is None:
                continue
            signal = dict(b_result.signal)
            signal_date = date.fromisoformat(str(signal["signal_date"]))
            signal_high = float(signal["signal_high"])
            signal_low = float(signal["signal_low"])
            if signal_high <= 0 or signal_low <= 0:
                continue
            raw.append(
                {
                    "instrument_pk": int(item["id"]),
                    "instrument_key": instrument_key,
                    "sector": str(item["sector"]),
                    "a_score": float(a_decision.score),
                    "signal": signal,
                    "signal_date": signal_date,
                    "signal_high": signal_high,
                    "signal_low": signal_low,
                    **stats,
                }
            )

        rally_pct = {r["instrument_pk"]: float(r["signal"].get("rally_pct", 0.0)) for r in raw}
        velocity = {r["instrument_pk"]: float(r["signal"].get("velocity", 0.0)) for r in raw}
        returns = {r["instrument_pk"]: float(r["return_63"]) for r in raw}
        stop_widths: dict[int, float] = {}
        for r in raw:
            capped_stop = max(
                r["signal_low"] * (1.0 - float(self.cfg["risk"]["raw_stop_buffer_pct"]) / 100.0),
                r["signal_high"] * (1.0 - float(self.cfg["risk"]["max_stop_risk_pct"]) / 100.0),
            )
            stop_widths[r["instrument_pk"]] = max(0.0, (r["signal_high"] - capped_stop) / r["signal_high"] * 100.0)

        rally_rank = _percentile_map(rally_pct)
        velocity_rank = _percentile_map(velocity)
        relative_rank = _percentile_map(returns)
        pullback_rank = _percentile_map(stop_widths, reverse=True)

        sector_returns: dict[str, list[float]] = {}
        for r in raw:
            sector_returns.setdefault(r["sector"], []).append(float(r["return_63"]))
        sector_medians = {s: float(median(v)) for s, v in sector_returns.items()}
        sector_rank_raw = _percentile_map({i: v for i, v in enumerate(sector_medians.values())})
        sector_names = list(sector_medians)
        sector_score = {sector_names[i]: sector_rank_raw[i] for i in range(len(sector_names))}

        state, nav, held_or_pending = self._portfolio_state(d)
        capacity = self._regime_capacity(d)
        candidates: list[Candidate] = []
        persisted: list[dict[str, Any]] = []

        for r in raw:
            pk = r["instrument_pk"]
            age = self._signal_age_sessions(pk, r["signal_date"], d)
            try:
                pos = size_position(
                    capital=nav,
                    entry=r["signal_high"],
                    signal_low=r["signal_low"],
                    risk_per_trade_pct=float(self.cfg["risk"]["risk_per_trade_pct"]),
                    max_position_pct=float(self.cfg["risk"]["max_position_pct"]),
                    raw_stop_buffer_pct=float(self.cfg["risk"]["raw_stop_buffer_pct"]),
                    max_stop_risk_pct=float(self.cfg["risk"]["max_stop_risk_pct"]),
                )
            except ValueError:
                continue

            proximity = _clamp100(100.0 - float(r["distance_52w"]) * 2.0)
            ranking_inputs = RankingInputs(
                rally_quality=_clamp100((rally_rank.get(pk, 50.0) + velocity_rank.get(pk, 50.0)) / 2.0),
                pullback_quality=_clamp100(pullback_rank.get(pk, 50.0)),
                fundamental_strength=_clamp100(r["a_score"]),
                relative_strength=_clamp100(relative_rank.get(pk, 50.0)),
                proximity_52w_high=proximity,
                sector_strength=_clamp100(sector_score.get(r["sector"], 50.0)),
            )
            candidate = Candidate(
                instrument_id=r["instrument_key"],
                sector=r["sector"],
                module_a_eligible=True,
                blackout_clear=True,
                signal_age_trading_days=age,
                adr20_pct=float(r["adr20"]),
                position_value=float(pos.position_value),
                adtv=float(r["adtv"]),
                already_held_or_pending=pk in held_or_pending,
                shares=int(pos.shares),
                stop_risk_pct=float(stop_widths[pk]),
                ranking=ranking_inputs,
            )
            decision = rank_candidate(ranking_inputs, self.cfg)
            candidates.append(candidate)
            persisted.append(
                {
                    "instrument_pk": pk,
                    "instrument_key": r["instrument_key"],
                    "score": decision.score,
                    "qualified": decision.qualified,
                    "components": decision.components,
                    "rejected_reasons": [],
                }
            )

        allocation = allocate_candidates(candidates, state, capacity, self.cfg)
        selected_keys = {c.instrument_id for c in allocation.selected}
        key_to_pk = {r["instrument_key"]: r["instrument_pk"] for r in persisted}
        selected_ids = {key_to_pk[k] for k in selected_keys if k in key_to_pk}
        self._persist(d, persisted, selected_ids, allocation.rejected)

        qualified = sum(1 for r in persisted if r["qualified"])
        return ModuleCGenerationResult(
            trade_date=d,
            universe_size=len(universe),
            a_eligible=a_pass,
            b_signals=len(raw),
            ranked=len(persisted),
            qualified=qualified,
            selected=len(selected_ids),
            rejected=max(0, len(persisted) - len(selected_ids)),
            regime_capacity=capacity,
        )
