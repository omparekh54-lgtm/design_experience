from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Engine, text

from packages.pms.safety import SafetyController


@dataclass(frozen=True)
class ReconciliationResult:
    run_id: str
    orders_match: bool
    positions_match: bool
    cash_match: bool
    discrepancies: tuple[dict, ...]

    @property
    def ok(self) -> bool:
        return self.orders_match and self.positions_match and self.cash_match


class ReconciliationService:
    def __init__(self, engine: Engine, broker, *, cash_tolerance: float = 1.0):
        self.engine = engine
        self.broker = broker
        self.cash_tolerance = cash_tolerance
        self.safety = SafetyController(engine)

    def run(self, account_mode: str = "PAPER") -> ReconciliationResult:
        run_id = str(uuid4()); started = datetime.now(timezone.utc)
        discrepancies: list[dict] = []
        with self.engine.begin() as conn:
            conn.execute(text("insert into reconciliation_runs(id,broker,account_mode,started_at,status) values (:id,:b,:m,:t,'RUNNING')"), {"id":run_id,"b":self.broker.name,"m":account_mode,"t":started})

        broker_orders = self.broker.get_orders()
        broker_positions = self.broker.get_positions()
        broker_cash = float(self.broker.get_cash())

        with self.engine.connect() as conn:
            local_orders = [dict(r) for r in conn.execute(text("select id,status,external_order_id from broker_orders where mode=:m order by created_at"), {"m":account_mode}).mappings().all()]
            local_positions = [dict(r) for r in conn.execute(text("""
                select coalesce(i.nse_symbol,replace(i.trading_symbol,'-EQ','')) symbol,sum(l.quantity)::int quantity
                from portfolio_ledger l join instruments i on i.id=l.instrument_id
                where l.account_mode=:m and l.event_type='FILL'
                group by i.id,i.nse_symbol,i.trading_symbol having sum(l.quantity)<>0
            """), {"m":account_mode}).mappings().all()]
            local_cash_delta = float(conn.execute(text("select coalesce(sum(cash_delta),0) from portfolio_ledger where account_mode=:m"), {"m":account_mode}).scalar_one())

        # Order comparison is intentionally conservative: external order IDs must be present for submitted live orders.
        if account_mode == "LIVE_APPROVAL":
            ext_ids = {str(o.get("orderid") or o.get("orderId") or "") for o in broker_orders}
            missing = [o for o in local_orders if o.get("external_order_id") and str(o["external_order_id"]) not in ext_ids]
            orders_match = not missing
            if missing: discrepancies.append({"type":"orders","missing_external_orders":missing})
        else:
            orders_match = True

        def norm_positions(rows):
            out={}
            for r in rows:
                symbol=str(r.get("symbol") or r.get("tradingsymbol") or r.get("tradingSymbol") or "").replace("-EQ","")
                qty=int(float(r.get("quantity") or r.get("netqty") or r.get("netQty") or 0))
                if symbol and qty: out[symbol]=qty
            return out
        lp=norm_positions(local_positions); bp=norm_positions(broker_positions)
        positions_match = lp == bp
        if not positions_match: discrepancies.append({"type":"positions","local":lp,"broker":bp})

        if account_mode == "PAPER":
            # Paper broker cash is generated from the same ledger and should match exactly.
            expected_cash = float(getattr(self.broker, "initial_cash", 0.0)) + local_cash_delta
        else:
            expected_cash = broker_cash
        cash_match = abs(expected_cash - broker_cash) <= self.cash_tolerance
        if not cash_match: discrepancies.append({"type":"cash","expected":expected_cash,"broker":broker_cash})

        status = "MATCHED" if orders_match and positions_match and cash_match else "MISMATCH"
        with self.engine.begin() as conn:
            conn.execute(text("""
                update reconciliation_runs set completed_at=:t,status=:s,orders_match=:o,positions_match=:p,cash_match=:c,
                    discrepancies=cast(:d as jsonb) where id=:id
            """), {"t":datetime.now(timezone.utc),"s":status,"o":orders_match,"p":positions_match,"c":cash_match,"d":__import__('json').dumps(discrepancies),"id":run_id})
        if status == "MISMATCH": self.safety.lock("broker_reconciliation_mismatch")
        return ReconciliationResult(run_id,orders_match,positions_match,cash_match,tuple(discrepancies))
