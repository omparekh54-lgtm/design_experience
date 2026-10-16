from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Any
from uuid import uuid4

from sqlalchemy import Engine, text

from packages.data_layer.angel_auth import create_angel_session
from packages.pms.safety import SafetyController


@dataclass(frozen=True)
class BrokerOrderRequest:
    instrument_id: str
    side: str
    quantity: int
    order_type: str = "MARKET"
    limit_price: float | None = None
    trigger_price: float | None = None
    idempotency_key: str = ""


@dataclass(frozen=True)
class BrokerOrderResult:
    order_id: str
    status: str
    external_order_id: str | None = None


class Broker(Protocol):
    name: str
    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult: ...
    def cancel_order(self, order_id: str) -> dict[str, Any]: ...
    def get_orders(self) -> list[dict[str, Any]]: ...
    def get_positions(self) -> list[dict[str, Any]]: ...
    def get_cash(self) -> float: ...


class _InstrumentLookup:
    def __init__(self, engine: Engine): self.engine = engine

    def row(self, instrument_id: str) -> dict[str, Any]:
        exchange, symbol = instrument_id.split(":", 1)
        trading_symbol = f"{symbol}-EQ" if exchange == "NSE" else symbol
        with self.engine.connect() as conn:
            row = conn.execute(text("""
                select id, exchange, trading_symbol, angel_token
                from instruments where exchange=:e and trading_symbol=:s
                order by valid_from desc limit 1
            """), {"e": exchange, "s": trading_symbol}).mappings().first()
        if not row: raise KeyError(instrument_id)
        return dict(row)


class PaperBroker:
    name = "PAPER"

    def __init__(self, engine: Engine, initial_cash: float = 10_000_000):
        self.engine = engine; self.lookup = _InstrumentLookup(engine); self.initial_cash = initial_cash

    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        if request.quantity <= 0: raise ValueError("quantity must be positive")
        instrument = self.lookup.row(request.instrument_id)
        order_id = str(uuid4())
        status = "OPEN"
        with self.engine.begin() as conn:
            existing = conn.execute(text("select id,status from broker_orders where idempotency_key=:k"), {"k": request.idempotency_key}).mappings().first() if request.idempotency_key else None
            if existing: return BrokerOrderResult(str(existing["id"]), str(existing["status"]))
            conn.execute(text("""
                insert into broker_orders(id,broker,mode,instrument_id,side,order_type,quantity,limit_price,trigger_price,status,idempotency_key)
                values (:id,'PAPER','PAPER',:instrument,:side,:type,:qty,:limit,:trigger,:status,:key)
            """), {"id": order_id, "instrument": instrument["id"], "side": request.side.upper(), "type": request.order_type.upper(), "qty": request.quantity, "limit": request.limit_price, "trigger": request.trigger_price, "status": status, "key": request.idempotency_key or order_id})
        return BrokerOrderResult(order_id, status)

    def fill_order(self, order_id: str, price: float, quantity: int | None = None, fees: float = 0.0) -> dict[str, Any]:
        with self.engine.begin() as conn:
            order = conn.execute(text("select * from broker_orders where id=:id for update"), {"id": order_id}).mappings().one()
            qty = int(quantity or order["quantity"])
            if qty <= 0 or qty > int(order["quantity"]): raise ValueError("invalid fill quantity")
            fill_id = str(uuid4()); now = datetime.now(timezone.utc)
            conn.execute(text("insert into broker_fills(id,order_id,external_trade_id,fill_time,price,quantity,fees) values (:id,:oid,:tid,:t,:p,:q,:f)"), {"id": fill_id, "oid": order_id, "tid": fill_id, "t": now, "p": price, "q": qty, "f": fees})
            conn.execute(text("update broker_orders set status='FILLED',updated_at=:t where id=:id"), {"t": now, "id": order_id})
            cash_delta = -(price * qty + fees) if order["side"] == "BUY" else (price * qty - fees)
            conn.execute(text("""
                insert into portfolio_ledger(account_mode,event_type,instrument_id,order_id,quantity,price,cash_delta,fees,metadata)
                values ('PAPER','FILL',:instrument,:oid,:qty,:price,:cash,:fees,jsonb_build_object('side',:side))
            """), {"instrument": order["instrument_id"], "oid": order_id, "qty": qty if order["side"] == "BUY" else -qty, "price": price, "cash": cash_delta, "fees": fees, "side": order["side"]})
        return {"fill_id": fill_id, "price": price, "quantity": qty}

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            conn.execute(text("update broker_orders set status='CANCELLED',updated_at=now() where id=:id and status='OPEN'"), {"id": order_id})
        return {"order_id": order_id, "status": "CANCELLED"}

    def get_orders(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("select * from broker_orders where broker='PAPER' order by created_at desc")).mappings().all()
        return [dict(r) for r in rows]

    def get_positions(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(text("""
                select i.exchange, coalesce(i.nse_symbol,replace(i.trading_symbol,'-EQ','')) symbol,
                       sum(l.quantity) quantity,
                       sum(l.quantity*l.price) / nullif(sum(l.quantity),0) avg_price
                from portfolio_ledger l join instruments i on i.id=l.instrument_id
                where l.account_mode='PAPER' and l.event_type='FILL'
                group by i.id,i.exchange,i.nse_symbol,i.trading_symbol having sum(l.quantity)<>0
            """)).mappings().all()
        return [dict(r) for r in rows]

    def get_cash(self) -> float:
        with self.engine.connect() as conn:
            delta = conn.execute(text("select coalesce(sum(cash_delta),0) from portfolio_ledger where account_mode='PAPER'")).scalar_one()
        return float(self.initial_cash) + float(delta)


class AngelOneBroker:
    name = "ANGEL_ONE"

    def __init__(self, engine: Engine, client=None):
        self.engine = engine; self.lookup = _InstrumentLookup(engine); self.safety = SafetyController(engine)
        self.client = client or create_angel_session()[0]

    def _require_approval_mode(self):
        status = self.safety.status()
        if not status.can_place_live_order:
            raise PermissionError("Angel order blocked: LIVE_APPROVAL with enabled, unlocked safety state is required")

    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        self._require_approval_mode()
        instrument = self.lookup.row(request.instrument_id)
        if not instrument.get("angel_token"): raise RuntimeError("Angel token unavailable")
        params = {
            "variety": "NORMAL", "tradingsymbol": instrument["trading_symbol"], "symboltoken": str(instrument["angel_token"]),
            "transactiontype": request.side.upper(), "exchange": instrument["exchange"], "ordertype": request.order_type.upper(),
            "producttype": "DELIVERY", "duration": "DAY", "price": str(request.limit_price or 0),
            "triggerprice": str(request.trigger_price or 0), "squareoff": "0", "stoploss": "0", "quantity": str(request.quantity),
        }
        response = self.client.placeOrderFullResponse(params)
        if not response or response.get("status") is not True: raise RuntimeError(f"Angel order failed: {response}")
        external_id = str((response.get("data") or {}).get("orderid"))
        local_id = str(uuid4())
        with self.engine.begin() as conn:
            conn.execute(text("""
                insert into broker_orders(id,broker,mode,instrument_id,side,order_type,quantity,limit_price,trigger_price,status,external_order_id,idempotency_key,raw_response)
                values (:id,'ANGEL_ONE','LIVE_APPROVAL',:instrument,:side,:type,:qty,:limit,:trigger,'SUBMITTED',:external,:key,cast(:raw as jsonb))
            """), {"id": local_id, "instrument": instrument["id"], "side": request.side.upper(), "type": request.order_type.upper(), "qty": request.quantity, "limit": request.limit_price, "trigger": request.trigger_price, "external": external_id, "key": request.idempotency_key or local_id, "raw": __import__('json').dumps(response)})
        return BrokerOrderResult(local_id, "SUBMITTED", external_id)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        self._require_approval_mode()
        with self.engine.connect() as conn:
            row = conn.execute(text("select external_order_id from broker_orders where id=:id"), {"id": order_id}).mappings().one()
        response = self.client.cancelOrder(str(row["external_order_id"]), "NORMAL")
        return response or {}

    def get_orders(self) -> list[dict[str, Any]]:
        response = self.client.orderBook() or {}; return list(response.get("data") or [])

    def get_positions(self) -> list[dict[str, Any]]:
        response = self.client.position() or {}; return list(response.get("data") or [])

    def get_cash(self) -> float:
        response = self.client.rmsLimit() or {}; data = response.get("data") or {}
        for key in ("availablecash", "availableCash", "net"):
            if data.get(key) is not None: return float(data[key])
        return 0.0
