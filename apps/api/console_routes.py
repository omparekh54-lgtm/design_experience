"""Read-only dashboard views. Stored evidence only; GET never runs strategy jobs."""

from __future__ import annotations
from datetime import date, datetime, timedelta
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from packages.pms.config import load_strategy_config
from packages.pms.module_a_context import IST, end_of_day
from packages.pms.module_e import ENGINE_VERSION

router = APIRouter(prefix="/v1/console", tags=["console"])


def _engine():
    from apps.api.main import db_engine

    return db_engine()


def requested_day(value):
    today = datetime.now(IST).date()
    if value and value > today:
        raise HTTPException(400, "Future dates are not available")
    return value or today


def _session(conn, day):
    now = datetime.now(IST)
    cutoff = (
        day
        if day < now.date() or (now.hour, now.minute) >= (15, 30)
        else day - timedelta(days=1)
    )
    return conn.execute(
        text(
            "select max(trade_date) from pms.module_e_benchmark_bars where benchmark_code='NIFTY50' and source='NSE_HISTORICAL_OR' and trade_date<=:d"
        ),
        {"d": cutoff},
    ).scalar_one_or_none()


def _snapshot(conn, day):
    row = (
        conn.execute(
            text(
                "select * from pms.portfolio_snapshots where account_mode='PAPER' and snapshot_date<=:d order by snapshot_date desc limit 1"
            ),
            {"d": day},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _run(conn, job):
    row = (
        conn.execute(
            text(
                "select id,job_name,status,started_at,finished_at,details from pms.worker_runs where job_name=:job order by started_at desc,id desc limit 1"
            ),
            {"job": job},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def normalize_regime(row, session, requested):
    if not row:
        return None
    value = dict(row)
    cfg = load_strategy_config()
    meta = value.get("inputs") or {}
    ready = (
        (meta.get("_data_quality") or {}).get("data_ready") is True
        and isinstance(value.get("max_open_trades"), int)
        and not isinstance(value.get("max_open_trades"), bool)
        and 0 <= value["max_open_trades"] <= 7
        and value.get("strategy_version") == cfg["version"]
        and meta.get("_module_e_version") == ENGINE_VERSION
        and value.get("trade_date") == session
        and (requested - session).days <= 7
    )
    value.update(
        data_ready=ready,
        reason=meta.get("_reason", "Regime coverage is unverified"),
        data_quality=meta.get("_data_quality") or {},
        implementation=meta.get("_module_e_version"),
    )
    if not ready:
        value.update(max_open_trades=0, band="UNAVAILABLE")
    return value


@router.get("/overview")
def overview(as_of: date | None = None):
    day = requested_day(as_of)
    cfg = load_strategy_config()
    with _engine().connect() as conn:
        session = _session(conn, day)
        snap = _snapshot(conn, session or day)
        history = (
            conn.execute(
                text(
                    "select snapshot_date,nav from (select snapshot_date,nav from pms.portfolio_snapshots where account_mode='PAPER' and snapshot_date<=:d order by snapshot_date desc limit 60) h order by snapshot_date"
                ),
                {"d": session or day},
            )
            .mappings()
            .all()
        )
        safety = (
            conn.execute(
                text(
                    "select execution_mode,new_orders_enabled,emergency_stop,locked_reason,updated_at from pms.safety_state where singleton=true"
                )
            )
            .mappings()
            .first()
        )
        regime = (
            conn.execute(
                text("select * from pms.market_regime_history where trade_date=:d"),
                {"d": session},
            )
            .mappings()
            .first()
            if session
            else None
        )
        daily = _run(conn, "daily_orchestrator")
        settlement = _run(conn, "formal_paper_settlement")
        c_version = (
            cfg["version"] + ":" + cfg["ranking"]["implementation_version"] + ":PAPER"
        )
        decision = conn.execute(
            text(
                "select max(trade_date) from pms.module_c_rankings where trade_date<=:d and strategy_version=:v"
            ),
            {"d": day, "v": c_version},
        ).scalar_one_or_none()
        counts = (
            conn.execute(
                text(
                    """select count(*) total,count(*) filter(where selected) selected,
            count(*) filter(where qualified) qualified from pms.module_c_rankings where trade_date=:d and strategy_version=:v"""
                ),
                {"d": decision, "v": c_version},
            )
            .mappings()
            .one()
        )
    return {
        "requested_date": day,
        "market_session": session,
        "decision_date": decision,
        "strategy_version": cfg["version"],
        "account_mode": "PAPER",
        "portfolio": snap,
        "nav_history": [dict(r) for r in history],
        "safety": dict(safety) if safety else None,
        "regime": normalize_regime(regime, session, day) if session else None,
        "daily_run": daily,
        "settlement": settlement,
        "signals": dict(counts),
        "real_money": False,
    }


SIGNAL_BASE = """from pms.module_c_rankings r join pms.instruments i on i.id=r.instrument_id
 left join lateral (select s.decision from pms.strategy_decisions s where s.instrument_id=r.instrument_id and s.module='C'
 and s.strategy_version=r.strategy_version and (s.as_of at time zone 'Asia/Kolkata')::date=r.trade_date order by s.as_of desc limit 1) detail on true
 where r.trade_date=:d and r.strategy_version=:v
 and (:q='' or coalesce(i.nse_symbol,i.trading_symbol) ilike :search)"""


@router.get("/signals")
def signals(
    as_of: date | None = None,
    q: str = Query("", max_length=80),
    view: str = Query("all", pattern="^(all|selected|watchlist|rejected)$"),
    page: int = Query(1, ge=1, le=10000),
    limit: int = Query(50, ge=1, le=100),
):
    day = requested_day(as_of)
    cfg = load_strategy_config()
    version = cfg["version"] + ":" + cfg["ranking"]["implementation_version"] + ":PAPER"
    with _engine().connect() as conn:
        d = conn.execute(
            text(
                "select max(trade_date) from pms.module_c_rankings where trade_date<=:d and strategy_version=:v"
            ),
            {"d": day, "v": version},
        ).scalar_one_or_none()
        params = {
            "d": d,
            "v": version,
            "q": q.strip(),
            "search": "%" + q.strip() + "%",
            "offset": (page - 1) * limit,
            "limit": limit,
        }
        counts = (
            conn.execute(
                text(
                    "select count(*) total,count(*) filter(where r.selected) selected,count(*) filter(where detail.decision->>'kind'='WATCHLIST') watchlist,count(*) filter(where not r.selected and coalesce(detail.decision->>'kind','')<>'WATCHLIST') rejected "
                    + SIGNAL_BASE
                ),
                params,
            )
            .mappings()
            .one()
        )
        condition = {
            "all": "",
            "selected": " and r.selected",
            "watchlist": " and detail.decision->>'kind'='WATCHLIST'",
            "rejected": " and not r.selected and coalesce(detail.decision->>'kind','')<>'WATCHLIST'",
        }[view]
        rows = (
            conn.execute(
                text(
                    "select i.exchange||':'||coalesce(i.nse_symbol,replace(i.trading_symbol,'-EQ','')) instrument_id,r.score,r.qualified,r.selected,r.components,r.rejected_reasons,detail.decision details "
                    + SIGNAL_BASE
                    + condition
                    + " order by r.selected desc,r.score desc,i.id limit :limit offset :offset"
                ),
                params,
            )
            .mappings()
            .all()
        )
    return {
        "requested_date": day,
        "decision_date": d,
        "strategy_version": version,
        "page": page,
        "limit": limit,
        "counts": {**dict(counts), "all": counts["total"]},
        "total": counts["total" if view == "all" else view],
        "rows": [dict(r) for r in rows],
        "proposal_only": True,
    }


@router.get("/universe")
def universe(
    q: str = Query("", max_length=80),
    page: int = Query(1, ge=1, le=10000),
    limit: int = Query(50, ge=1, le=100),
    as_of: date | None = None,
):
    day = requested_day(as_of)
    cfg = load_strategy_config()
    params = {
        "stamp": min(end_of_day(day), datetime.now(IST)),
        "q": q.strip(),
        "search": "%" + q.strip() + "%",
        "limit": limit,
        "offset": (page - 1) * limit,
        "version": cfg["version"],
    }
    base = """from pms.instruments i left join lateral(select eligible,score,failed_rules,as_of from pms.module_a_history h
      where h.instrument_id=i.id and h.as_of<=:stamp and h.strategy_version=:version order by h.as_of desc limit 1) a on true
      where i.exchange='NSE' and i.active and (:q='' or coalesce(i.nse_symbol,i.trading_symbol) ilike :search)"""
    with _engine().connect() as conn:
        total = int(conn.execute(text("select count(*) " + base), params).scalar_one())
        rows = (
            conn.execute(
                text(
                    "select i.exchange||':'||coalesce(i.nse_symbol,replace(i.trading_symbol,'-EQ','')) instrument_id,i.sector,a.eligible,a.score,a.failed_rules,a.as_of "
                    + base
                    + " order by i.nse_symbol,i.id limit :limit offset :offset"
                ),
                params,
            )
            .mappings()
            .all()
        )
    return {
        "as_of": day,
        "page": page,
        "limit": limit,
        "total": total,
        "rows": [dict(r) for r in rows],
    }


OWNED = """exists(select 1 from pms.broker_orders o where o.strategy_trade_id=t.id and o.mode='PAPER')
 and not exists(select 1 from pms.broker_orders o where o.strategy_trade_id=t.id and o.mode<>'PAPER')"""


@router.get("/paper")
def paper(limit: int = Query(100, ge=1, le=300)):
    with _engine().connect() as conn:
        session = _session(conn, datetime.now(IST).date())
        snap = _snapshot(conn, session or datetime.now(IST).date())
        trades = (
            conn.execute(
                text(
                    "select t.*,i.exchange||':'||coalesce(i.nse_symbol,replace(i.trading_symbol,'-EQ','')) instrument from pms.trades t join pms.instruments i on i.id=t.instrument_id where "
                    + OWNED
                    + " order by (t.remaining_shares>0) desc,t.updated_at desc limit :limit"
                ),
                {"limit": limit},
            )
            .mappings()
            .all()
        )
        orders = (
            conn.execute(
                text(
                    """select o.id,o.strategy_trade_id,o.side,o.order_type,o.quantity,o.trigger_price,o.limit_price,o.status,o.created_at,o.updated_at,
            i.exchange||':'||coalesce(i.nse_symbol,replace(i.trading_symbol,'-EQ','')) instrument,
            coalesce((select sum(f.quantity) from pms.broker_fills f where f.order_id=o.id),0) filled_quantity,
            o.raw_response->'formal_paper'->>'implementation' implementation
            from pms.broker_orders o join pms.instruments i on i.id=o.instrument_id where o.mode='PAPER' order by o.created_at desc,o.id limit :limit"""
                ),
                {"limit": limit},
            )
            .mappings()
            .all()
        )
        fills = (
            conn.execute(
                text(
                    """select f.id,f.fill_time,f.price,f.quantity,f.fees,o.side,o.strategy_trade_id,
            i.exchange||':'||coalesce(i.nse_symbol,replace(i.trading_symbol,'-EQ','')) instrument,
            f.raw_response->>'implementation' implementation from pms.broker_fills f join pms.broker_orders o on o.id=f.order_id
            join pms.instruments i on i.id=o.instrument_id where o.mode='PAPER' order by f.fill_time desc,f.id limit :limit"""
                ),
                {"limit": limit},
            )
            .mappings()
            .all()
        )
    return {
        "mode": "PAPER",
        "market_session": session,
        "snapshot": snap,
        "trades": [dict(r) for r in trades],
        "orders": [dict(r) for r in orders],
        "fills": [dict(r) for r in fills],
        "limit": limit,
        "real_money": False,
    }


@router.get("/trades/{trade_id}")
def trade_detail(trade_id: UUID):
    with _engine().connect() as conn:
        row = (
            conn.execute(
                text(
                    "select t.*,i.exchange||':'||coalesce(i.nse_symbol,replace(i.trading_symbol,'-EQ','')) instrument from pms.trades t join pms.instruments i on i.id=t.instrument_id where t.id=:id and "
                    + OWNED
                ),
                {"id": trade_id},
            )
            .mappings()
            .first()
        )
        if not row:
            raise HTTPException(404, "Formal PAPER trade not found")
        events = (
            conn.execute(
                text(
                    "select id,event_time,state,event_type,payload from pms.trade_state_events where trade_id=:id and event_type<>'PAPER_CHECKPOINT' order by event_time,id limit 300"
                ),
                {"id": trade_id},
            )
            .mappings()
            .all()
        )
        fills = (
            conn.execute(
                text(
                    "select f.fill_time,f.price,f.quantity,f.fees,o.side from pms.broker_fills f join pms.broker_orders o on o.id=f.order_id where o.strategy_trade_id=:id and o.mode='PAPER' order by f.fill_time,f.id"
                ),
                {"id": trade_id},
            )
            .mappings()
            .all()
        )
    return {
        "mode": "PAPER",
        "trade": dict(row),
        "events": [dict(r) for r in events],
        "fills": [dict(r) for r in fills],
    }


@router.get("/data")
def data_status():
    with _engine().connect() as conn:
        day = _session(conn, datetime.now(IST).date())
        metrics = (
            conn.execute(
                text(
                    """select
          (select count(*) from pms.instruments where active and exchange='NSE') active_stocks,
          (select count(*) from pms.instruments where active and exchange='NSE' and sector is not null and trim(sector)<>'') classified_stocks,
          (select count(distinct p.instrument_id) from pms.daily_prices p join pms.instruments i on i.id=p.instrument_id where p.trade_date=:d and i.active and i.exchange='NSE') stocks_with_session_bar,
          (select count(*) from pms.financial_filings) financial_filings,
          (select count(*) from pms.data_quality_issues where severity='ERROR' and resolved_at is null) critical_errors"""
                ),
                {"d": day},
            )
            .mappings()
            .one()
        )
        issues = (
            conn.execute(
                text(
                    "select dataset,severity,code,message,observed_at from pms.data_quality_issues where resolved_at is null order by case severity when 'ERROR' then 0 else 1 end,observed_at desc limit 100"
                )
            )
            .mappings()
            .all()
        )
        benchmarks = (
            conn.execute(
                text(
                    "select benchmark_code,count(*) rows,min(trade_date) first_date,max(trade_date) latest from pms.module_e_benchmark_bars where source='NSE_HISTORICAL_OR' and trade_date<=:d group by benchmark_code order by benchmark_code"
                ),
                {"d": day},
            )
            .mappings()
            .all()
        )
        reg = (
            conn.execute(
                text(
                    "select inputs from pms.market_regime_history where trade_date=:d"
                ),
                {"d": day},
            ).scalar_one_or_none()
            if day
            else None
        )
    return {
        "market_session": day,
        "metrics": dict(metrics),
        "issues": [dict(r) for r in issues],
        "issue_limit": 100,
        "benchmarks": [dict(r) for r in benchmarks],
        "regime_data_quality": (reg or {}).get("_data_quality"),
        "coverage_note": "A session bar or sector classification does not prove complete price, financial or surveillance history.",
    }
