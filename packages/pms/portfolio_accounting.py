from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
from typing import Iterable

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class PositionSnapshot:
    instrument_id: int
    quantity: int
    average_price: float
    last_price: float
    market_value: float
    unrealized_pnl: float


@dataclass(frozen=True)
class PortfolioSnapshot:
    snapshot_date: date
    account_mode: str
    cash: float
    market_value: float
    realized_pnl: float
    unrealized_pnl: float
    nav: float
    open_risk: float
    portfolio_heat_pct: float
    positions: tuple[PositionSnapshot, ...]


class PortfolioAccountingService:
    def __init__(self, engine: Engine, initial_capital: float = 10_000_000):
        self.engine = engine
        self.initial_capital = initial_capital

    def snapshot(self, on_date: date, account_mode: str = "PAPER") -> PortfolioSnapshot:
        with self.engine.connect() as conn:
            cash_delta = float(conn.execute(text("select coalesce(sum(cash_delta),0) from portfolio_ledger where account_mode=:m and event_time::date<=:d"), {"m": account_mode, "d": on_date}).scalar_one())
            realized = float(conn.execute(text("select coalesce(sum(realized_pnl_delta),0) from portfolio_ledger where account_mode=:m and event_time::date<=:d"), {"m": account_mode, "d": on_date}).scalar_one())
            lots = conn.execute(text("""
                with q as (
                  select instrument_id, sum(quantity)::int quantity,
                         sum(case when quantity>0 then quantity*price else 0 end) / nullif(sum(case when quantity>0 then quantity else 0 end),0) avg_price
                  from portfolio_ledger where account_mode=:m and event_type='FILL' and event_time::date<=:d
                  group by instrument_id
                )
                select q.instrument_id,q.quantity,q.avg_price,
                       coalesce((select close from daily_prices p where p.instrument_id=q.instrument_id and p.trade_date<=:d order by p.trade_date desc limit 1),q.avg_price) last_price
                from q where q.quantity<>0
            """), {"m": account_mode, "d": on_date}).mappings().all()
        positions=[]; market_value=0.0; unrealized=0.0
        for r in lots:
            qty=int(r["quantity"]); avg=float(r["avg_price"] or 0); last=float(r["last_price"] or avg)
            mv=qty*last; upnl=qty*(last-avg); market_value+=mv; unrealized+=upnl
            positions.append(PositionSnapshot(int(r["instrument_id"]),qty,avg,last,mv,upnl))
        cash=self.initial_capital+cash_delta; nav=cash+market_value
        open_risk=0.0
        with self.engine.connect() as conn:
            rows=conn.execute(text("select entry,stop,remaining_shares from trades where state not in ('CLOSED','CANCELLED') and entry is not null and stop is not null")).mappings().all()
            for r in rows: open_risk += max(0.0,(float(r['entry'])-float(r['stop']))*int(r['remaining_shares']))
        heat=(open_risk/nav*100.0) if nav>0 else 0.0
        return PortfolioSnapshot(on_date,account_mode,round(cash,2),round(market_value,2),round(realized,2),round(unrealized,2),round(nav,2),round(open_risk,2),round(heat,4),tuple(positions))

    def persist_snapshot(self, snapshot: PortfolioSnapshot) -> None:
        import json
        with self.engine.begin() as conn:
            conn.execute(text("""
                insert into portfolio_snapshots(snapshot_date,account_mode,cash,market_value,realized_pnl,unrealized_pnl,nav,open_risk,portfolio_heat_pct,positions)
                values (:d,:m,:cash,:mv,:rp,:up,:nav,:risk,:heat,cast(:positions as jsonb))
                on conflict (snapshot_date,account_mode) do update set cash=excluded.cash,market_value=excluded.market_value,
                  realized_pnl=excluded.realized_pnl,unrealized_pnl=excluded.unrealized_pnl,nav=excluded.nav,
                  open_risk=excluded.open_risk,portfolio_heat_pct=excluded.portfolio_heat_pct,positions=excluded.positions,created_at=now()
            """), {"d":snapshot.snapshot_date,"m":snapshot.account_mode,"cash":snapshot.cash,"mv":snapshot.market_value,"rp":snapshot.realized_pnl,"up":snapshot.unrealized_pnl,"nav":snapshot.nav,"risk":snapshot.open_risk,"heat":snapshot.portfolio_heat_pct,"positions":json.dumps([asdict(p) for p in snapshot.positions])})
