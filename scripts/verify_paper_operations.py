"""Measure launch preparations without changing safety, placing orders or starting a clock."""
from __future__ import annotations
import argparse,json,os,sys,urllib.request
from datetime import datetime,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from packages.pms.backtest_matrix import fingerprint
from packages.pms.config import load_strategy_config
from packages.pms.matrix_data import code_fingerprint,consistent_inputs
from packages.pms.module_a_context import IST
from packages.pms.market_calendar import MarketCalendar
from packages.pms.campaign_readiness import readiness_query,assess_readiness
from packages.pms.paper_campaign import draft_manifest,operational_proofs,universe
from packages.pms.daily_orchestrator import DailyServices
from packages.pms.database import online_engine
from packages.pms.paper_data_repair import safe_error
from scripts.copy_pms_to_neon import connection_url


def scheduled_delivery(runs,now):
    for r in runs:
        try:
            stamp=datetime.fromisoformat(r['created_at'].replace('Z','+00:00'))
            if (r['event']=='schedule' and r['head_branch']=='main' and r['status']=='completed'
                    and r['conclusion'] in {'success','failure'} and stamp.tzinfo is not None
                    and timedelta(0)<=now-stamp<=timedelta(days=3)):
                return r['id']
        except (ValueError,KeyError,TypeError):continue
    return None


def fetch_json(url,headers=None):
    with urllib.request.urlopen(urllib.request.Request(url,headers=headers or {}),timeout=60) as r:
        return json.loads(r.read(2_000_001))


def completed_session(calendar,now):
    day=now.date() if (now.hour,now.minute)>=(15,30) else now.date()-timedelta(days=1)
    for _ in range(7):
        if calendar.forward(day,1)==[day]:return day
        day-=timedelta(days=1)
    raise ValueError('completed_exchange_session_unverified')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--recovery-report',type=Path,required=True)
    p.add_argument('--preflight-report',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    if a.output.exists():p.error('Preserve existing evidence')
    now=datetime.now(IST);code=code_fingerprint();cfg=load_strategy_config()
    report=dict(version='PAPER_OPERATIONS_v1',checked_at=now.isoformat(),code_version=code,
                repository_commit=os.getenv('GITHUB_SHA'),checks={},campaign_activated=False,
                elapsed_campaign_sessions=0,paper_permissions_changed=False,errors=[])
    engine=None
    def measure(name,operation):
        try:report['checks'][name]=operation() is True
        except Exception as exc:
            report['checks'][name]=False;report['errors'].append(dict(check=name,code=safe_error(exc)))
    try:
        measure('deployed_code',lambda:fetch_json('https://pms-status-api.onrender.com/ready').get('deployed_commit')==os.environ['GITHUB_SHA'])
        pre=json.loads(a.preflight_report.read_text());restore=json.loads(a.recovery_report.read_text())
        measure('archive',lambda:pre.get('status')=='INFRA_VERIFIED' and pre.get('raw_archive',{}).get('status')=='VERIFIED'
                and pre['raw_archive'].get('anonymous_read_denied') is True)
        def restored():
            stamp=datetime.fromisoformat(restore['snapshot_at'])
            return (restore['status']=='RESTORE_VERIFIED' and restore.get('safety_locked') is True
                    and restore.get('source_writes') is False and stamp.tzinfo is not None
                    and timedelta(0)<=now-stamp<=timedelta(hours=24))
        measure('restore',restored)
        def scheduler():
            url='https://api.github.com/repos/omparekh54-lgtm/design_experience/actions/workflows/pms-market-close.yml/runs?event=schedule&branch=main&per_page=30'
            data=fetch_json(url,{'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json'})
            run=scheduled_delivery(data['workflow_runs'],now);report['scheduled_run_id']=run
            return run is not None and os.getenv('COLLECTION_ENABLED')=='true'
        measure('scheduled_delivery',scheduler)
        engine=online_engine(connection_url(os.getenv('NEON_PMS_DATABASE_URL',''),destination=True),worker=True)
        with consistent_inputs(engine) as snapshot:
            day=completed_session(MarketCalendar.from_database(snapshot,now),now)
            facts=snapshot.execute(readiness_query(day)).scalar_one()
            identities=universe(snapshot)
            class ReadOnlyEngine:
                def connect(self):return snapshot
            # No settlement, admission, journal writes or decision persistence is called.
            report['read_only_rehearsal']=DailyServices(ReadOnlyEngine(),cfg).preflight(day,now)
            report['checks']['read_only_rehearsal']=report['read_only_rehearsal']['status']=='READY'
            report['checks']['paper_locked']=(facts['safety']['execution_mode']=='PAPER' and facts['safety']['new_orders_enabled'] is False)
            proofs=operational_proofs(report,now=now,code_version=code)
            readiness=assess_readiness(facts,cfg,day=day,code_version=code,operational_proofs=proofs)
            report['activation_draft']=draft_manifest(readiness,cfg,identities,code_version=code)
        report['status']='VERIFIED' if all(report['checks'].values()) else 'BLOCKED'
    except Exception as exc:
        report.update(status='BLOCKED');report['errors'].append(dict(code=safe_error(exc)))
    finally:
        if engine:engine.dispose()
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(report,indent=2,default=str,allow_nan=False)+'\n')
        # Keep the full universe/config draft in the artifact; concise logs suffice.
        print(json.dumps({k:v for k,v in report.items() if k!='activation_draft'},default=str))
    return 0 if report['status']=='VERIFIED' else 2

if __name__=='__main__':raise SystemExit(main())
