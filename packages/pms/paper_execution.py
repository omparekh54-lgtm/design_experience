from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Engine, text

from packages.pms.brokers import PaperBroker
from packages.pms.config import load_strategy_config
from packages.pms.costs import estimate_delivery_costs


@dataclass(frozen=True)
class PaperBar:
    instrument_id: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float


class PaperExecutionService:
    """Execute persisted PAPER orders against market bars using production cost assumptions."""

    def __init__(self, engine: Engine, broker: PaperBroker | None = None, cfg: dict | None = None):
        self.engine = engine
        self.broker = broker or PaperBroker(engine)
        self.cfg = cfg or load_strategy_config()

    def _instrument_pk(self, instrument_id: str) -> int:
        exchange, symbol = instrument_id.split(":", 1)
        trading_symbol = f"{symbol}-EQ" if exchange == "NSE" else symbol
        with self.engine.connect() as conn:
            value = conn.execute(text("select id from instruments where exchange=:e and trading_symbol=:s order by valid_from desc limit 1"), {"e":exchange,"s":trading_symbol}).scalar_one()
        return int(value)

    def process_bar(self, bar: PaperBar) -> list[dict]:
        pk = self._instrument_pk(bar.instrument_id)
        with self.engine.connect() as conn:
            orders = [dict(r) for r in conn.execute(text("select * from broker_orders where broker='PAPER' and instrument_id=:id and status='OPEN' order by created_at"), {"id":pk}).mappings().all()]
        fills=[]
        for order in orders:
            side=str(order['side']).upper(); kind=str(order['order_type']).upper()
            trigger=float(order['trigger_price']) if order['trigger_price'] is not None else None
            limit=float(order['limit_price']) if order['limit_price'] is not None else None
            fill_price=None
            if kind in {'MARKET'}:
                fill_price=bar.open
            elif kind in {'STOP','STOP_MARKET','SL-M'} and trigger is not None:
                if side=='BUY' and bar.high>=trigger: fill_price=max(bar.open,trigger)
                if side=='SELL' and bar.low<=trigger: fill_price=min(bar.open,trigger)
            elif kind in {'LIMIT'} and limit is not None:
                if side=='BUY' and bar.low<=limit: fill_price=min(bar.open,limit)
                if side=='SELL' and bar.high>=limit: fill_price=max(bar.open,limit)
            if fill_price is None: continue
            qty=int(order['quantity'])
            costs=estimate_delivery_costs(trade_date=bar.trade_date,side=side,price=fill_price,quantity=qty,cfg=self.cfg['backtest'])
            # Slippage and impact are represented in cost accounting; fill remains observable market price.
            fill=self.broker.fill_order(str(order['id']),fill_price,qty,costs.total)
            fill.update({'side':side,'reason':kind,'costs':costs.total}); fills.append(fill)
        return fills
