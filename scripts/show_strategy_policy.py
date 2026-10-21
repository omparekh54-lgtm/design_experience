"""Inspect the configured research policies without opening a database connection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from packages.pms.config import DEFAULT_CONFIG, load_strategy_config
from packages.pms.module_a_context import validate_module_a_config
from packages.pms.policies import policy_summary


def main(argv=None):
    parser=argparse.ArgumentParser(description='Show provisional/validated policy values and fingerprint; no data or execution changes')
    parser.add_argument('--config',default=str(DEFAULT_CONFIG))
    parser.add_argument('--sector',help='Optional NSE sector or configured alias')
    args=parser.parse_args(argv)
    cfg=load_strategy_config(args.config)
    validate_module_a_config(cfg)
    payload=dict(strategy_version=cfg['version'],policy=policy_summary(cfg,args.sector),
        sector_limits=cfg['fundamental']['leverage_limits']['sectors'],
        sector_aliases=cfg['fundamental']['leverage_limits'].get('sector_aliases',{}),
        benchmark_name=cfg['regime'].get('benchmark_name'),performance_validated=False)
    print(json.dumps(payload,indent=2,allow_nan=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
