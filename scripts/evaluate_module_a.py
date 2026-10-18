from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from sqlalchemy import create_engine
from packages.pms.config import load_strategy_config, DEFAULT_CONFIG
from packages.pms.module_a_context import IST, as_ist
from packages.pms.module_a_pipeline import ModuleAPipeline


def main():
    parser = argparse.ArgumentParser(description='Database-backed Module A scanner; no manually supplied financial inputs.')
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--instrument',help='For example NSE:RELIANCE')
    target.add_argument('--all',action='store_true',help='Scan every active NSE instrument')
    parser.add_argument('--as-of',help='ISO timestamp; naive timestamps are interpreted as IST')
    parser.add_argument('--config',default=str(DEFAULT_CONFIG),help='Strategy YAML, including explicit sector leverage limits')
    parser.add_argument('--persist',action='store_true',help='Save decisions and reasons in module_a_history')
    parser.add_argument('--details',action='store_true',help='Include every stock snapshot in all-stock output')
    args = parser.parse_args()
    load_dotenv(ROOT/'.env')
    os.environ.setdefault('PGOPTIONS','-c search_path=pms,public')
    url = os.getenv('DATABASE_URL') or os.getenv('PMS_DATABASE_URL')
    if not url:
        raise SystemExit('DATABASE_URL/PMS_DATABASE_URL is required in .env')
    as_of = as_ist(datetime.fromisoformat(args.as_of)) if args.as_of else datetime.now(IST)
    if as_of>datetime.now(IST):
        raise SystemExit('--as-of must not be in the future')
    db = create_engine(url,pool_pre_ping=True)
    pipeline = ModuleAPipeline(db,load_strategy_config(args.config))
    if args.all:
        result = pipeline.scan(as_of,persist=args.persist)
        if not args.details:
            result['results'] = [{'instrument_id':r['instrument_id'],**r['decision']} for r in result['results'] if r['decision']['eligible']]
            result['detail_hint'] = 'Use --details for every stock; --persist saves all rejections as well.'
    else:
        snapshot,decision = pipeline.evaluate(args.instrument.upper(),as_of,persist=args.persist)
        result = {'instrument_id':args.instrument.upper(),'as_of':as_of,'persisted':args.persist,
                  **pipeline.serialize(snapshot,decision)}
    print(json.dumps(result,indent=2,default=str,allow_nan=False))


if __name__=='__main__':
    main()
