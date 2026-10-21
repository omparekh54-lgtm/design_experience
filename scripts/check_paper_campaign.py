"""Read-only PAPER readiness; never clears locks, creates fills or starts a clock."""
from __future__ import annotations
import argparse,json,sys
from datetime import date
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from packages.pms.config import load_strategy_config
from packages.pms.campaign_readiness import assess_readiness,readiness_query
from packages.pms.matrix_data import consistent_inputs,code_fingerprint
from scripts.run_market_close import engine_from_env


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of',type=date.fromisoformat,required=True)
    parser.add_argument('--operational-report',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.report.exists():parser.error('Preserve previous evidence; select a new report path')
    engine=engine_from_env()
    try:
        with consistent_inputs(engine) as snapshot:
            facts=snapshot.execute(readiness_query(args.as_of)).scalar_one()
        from datetime import datetime
        from packages.pms.paper_campaign import operational_proofs
        from packages.pms.module_a_context import IST
        code=code_fingerprint()
        receipt=json.loads(args.operational_report.read_text()) if args.operational_report else {}
        proofs=operational_proofs(receipt,now=datetime.now(IST),code_version=code)
        report=assess_readiness(facts,load_strategy_config(),day=args.as_of,code_version=code,operational_proofs=proofs)
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,indent=2,allow_nan=False))
        print(json.dumps(report,indent=2,allow_nan=False))
        return 2 if report['blockers'] else 0
    finally:engine.dispose()


if __name__=='__main__':raise SystemExit(main())
