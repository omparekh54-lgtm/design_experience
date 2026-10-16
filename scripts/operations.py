from __future__ import annotations

import argparse
import json
import os
from datetime import date

from sqlalchemy import create_engine

from packages.pms.audit import AuditTrail
from packages.pms.brokers import BrokerOrderRequest, PaperBroker
from packages.pms.live_validation import LiveValidationLimits, LiveValidationService
from packages.pms.paper_validation import PaperValidationService
from packages.pms.portfolio_accounting import PortfolioAccountingService
from packages.pms.production_readiness import ProductionReadinessController
from packages.pms.reconciliation import ReconciliationService
from packages.pms.safety import SafetyController


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="PMS operations control CLI")
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("status")
    m=sub.add_parser("mode"); m.add_argument("value",choices=["PAPER","LIVE_VALIDATION","LIVE_APPROVAL"])
    s=sub.add_parser("emergency-stop"); s.add_argument("--reason",default="operator_emergency_stop")
    sub.add_parser("reconcile-paper")
    snap=sub.add_parser("snapshot"); snap.add_argument("--date",type=date.fromisoformat,default=date.today())

    pv=sub.add_parser("paper-validate")
    pv.add_argument("--start",type=date.fromisoformat,required=True)
    pv.add_argument("--end",type=date.fromisoformat,required=True)

    sub.add_parser("readiness")
    promote=sub.add_parser("promote")
    promote.add_argument("stage",choices=["PAPER","LIVE_VALIDATION","LIVE_APPROVAL"])

    audit=sub.add_parser("audit")
    audit.add_argument("--entity-type")
    audit.add_argument("--entity-id")
    audit.add_argument("--limit",type=int,default=50)

    lvc=sub.add_parser("live-session-create")
    lvc.add_argument("--max-capital",type=float,required=True)
    lvc.add_argument("--max-order-value",type=float,required=True)
    lvc.add_argument("--max-open-positions",type=int,default=2)
    lvc.add_argument("--max-daily-loss",type=float,required=True)
    lvc.add_argument("--symbols",default="")
    lvc.add_argument("--notes")

    lva=sub.add_parser("live-session-activate"); lva.add_argument("session_id")
    lvcx=sub.add_parser("live-session-close"); lvcx.add_argument("session_id"); lvcx.add_argument("--aborted",action="store_true"); lvcx.add_argument("--notes")

    prop=sub.add_parser("live-propose")
    prop.add_argument("session_id"); prop.add_argument("instrument")
    prop.add_argument("side",choices=["BUY","SELL"]); prop.add_argument("quantity",type=int)
    prop.add_argument("--estimated-price",type=float,required=True)
    prop.add_argument("--order-type",default="MARKET")
    prop.add_argument("--limit-price",type=float); prop.add_argument("--trigger-price",type=float)
    prop.add_argument("--reason")

    approve=sub.add_parser("live-approve"); approve.add_argument("approval_id")
    reject=sub.add_parser("live-reject"); reject.add_argument("approval_id"); reject.add_argument("--reason",required=True)
    return p


def main():
    args=parser().parse_args(); url=os.getenv("DATABASE_URL")
    if not url: raise RuntimeError("DATABASE_URL is required")
    engine=create_engine(url,pool_pre_ping=True); safety=SafetyController(engine)
    if args.command=="status":
        print(json.dumps(safety.status().__dict__,indent=2,default=str)); return
    if args.command=="mode":
        safety.set_mode(args.value,"cli_operator"); print(f"mode={args.value} new_orders_enabled=false"); return
    if args.command=="emergency-stop":
        safety.emergency_stop(args.reason,"cli_operator"); print(f"stopped reason={args.reason}"); return
    if args.command=="reconcile-paper":
        r=ReconciliationService(engine,PaperBroker(engine)).run("PAPER"); print(json.dumps({"run_id":r.run_id,"ok":r.ok,"discrepancies":r.discrepancies},indent=2,default=str)); return
    if args.command=="snapshot":
        svc=PortfolioAccountingService(engine); snap=svc.snapshot(args.date,"PAPER"); svc.persist_snapshot(snap); print(json.dumps(snap.__dict__,indent=2,default=str)); return
    if args.command=="paper-validate":
        r=PaperValidationService(engine).evaluate(args.start,args.end,actor="cli_operator"); print(json.dumps({"run_id":r.run_id,"passed":r.passed,"metrics":r.metrics,"gates":r.gates,"criteria":r.criteria.__dict__},indent=2,default=str)); return
    if args.command=="readiness":
        ctl=ProductionReadinessController(engine); r=ctl.evaluate(); ctl.persist(r,actor="cli_operator"); print(json.dumps(r.__dict__,indent=2,default=str)); return
    if args.command=="promote":
        r=ProductionReadinessController(engine).promote(args.stage,actor="cli_operator"); print(json.dumps({"promoted_to":args.stage,"readiness":r.__dict__},indent=2,default=str)); return
    if args.command=="audit":
        rows=AuditTrail(engine).history(entity_type=args.entity_type,entity_id=args.entity_id,limit=args.limit); print(json.dumps(rows,indent=2,default=str)); return
    if args.command=="live-session-create":
        symbols=tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
        limits=LiveValidationLimits(args.max_capital,args.max_order_value,args.max_open_positions,args.max_daily_loss,symbols)
        sid=LiveValidationService(engine).create_session(limits,actor="cli_operator",notes=args.notes); print(sid); return
    if args.command=="live-session-activate":
        LiveValidationService(engine).activate_session(args.session_id,actor="cli_operator"); print("activated"); return
    if args.command=="live-session-close":
        LiveValidationService(engine).close_session(args.session_id,actor="cli_operator",completed=not args.aborted,notes=args.notes); print("closed"); return
    if args.command=="live-propose":
        req=BrokerOrderRequest(args.instrument.upper(),args.side,args.quantity,args.order_type,args.limit_price,args.trigger_price)
        aid=LiveValidationService(engine).propose_order(args.session_id,req,estimated_price=args.estimated_price,actor="cli_operator",reason=args.reason); print(aid); return
    if args.command=="live-approve":
        LiveValidationService(engine).approve(args.approval_id,actor="cli_operator"); print("approved_for_manual_submission"); return
    if args.command=="live-reject":
        LiveValidationService(engine).reject(args.approval_id,actor="cli_operator",reason=args.reason); print("rejected"); return


if __name__=="__main__": main()
