from __future__ import annotations

import argparse
import json
import os
from datetime import date

from sqlalchemy import create_engine

from packages.pms.brokers import PaperBroker
from packages.pms.portfolio_accounting import PortfolioAccountingService
from packages.pms.reconciliation import ReconciliationService
from packages.pms.safety import SafetyController


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description="PMS operations control CLI")
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("status")
    m=sub.add_parser("mode"); m.add_argument("value",choices=["PAPER","LIVE_APPROVAL"])
    s=sub.add_parser("emergency-stop"); s.add_argument("--reason",default="operator_emergency_stop")
    sub.add_parser("reconcile-paper")
    snap=sub.add_parser("snapshot"); snap.add_argument("--date",type=date.fromisoformat,default=date.today())
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


if __name__=="__main__": main()
