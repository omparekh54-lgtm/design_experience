"""Repair original financial/sector sources only; PAPER entries stay disabled."""
from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
from datetime import datetime
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from packages.data_layer.cloud_sources import IST,OfficialHTTP
from packages.data_layer.private_archive import PrivateArchive
from packages.pms.database import online_engine
from packages.pms.paper_data_repair import PaperDataRepair,safe_error
from scripts.copy_pms_to_neon import connection_url


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['financials','sectors','collector','collector-drain','identities','readiness'])
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    if a.output.exists():p.error('Preserve existing report; choose a new path')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    report=dict(id=str(uuid4()),mode=a.mode,status='RUNNING',started_at=datetime.now(IST).isoformat(),
                campaign_started=False,paper_permissions_changed=False)
    engine=None
    def save(data):
        report.update(data)
        tmp=a.output.with_suffix('.tmp');tmp.write_text(json.dumps(report,default=str,indent=2));tmp.replace(a.output)
    save({})
    try:
        engine=online_engine(connection_url(os.environ.get('DATABASE_URL',''),destination=True),worker=True)
        archive=PrivateArchive.from_env()
        http=OfficialHTTP(archive=archive,seconds=10000)
        service=PaperDataRepair(engine,http,archive)
        service.locked()
        if a.mode in {'collector','collector-drain','readiness'}:
            from packages.pms.cloud_collection import run_collection
            from packages.pms.market_calendar import MarketCalendar
            from datetime import timedelta
            now=datetime.now(IST)
            calendar=MarketCalendar.from_database(engine,now)
            day=now.date() if (now.hour,now.minute)>=(15,30) else now.date()-timedelta(days=1)
            for _ in range(7):
                if calendar.forward(day,1)==[day]:break
                day-=timedelta(days=1)
            else:raise ValueError('No verified completed exchange session')
            if a.mode in {'collector','collector-drain'}:
                def once():
                    stamp=datetime.now(IST)
                    return run_collection(engine,now=stamp,max_filings=200 if a.mode=='collector-drain' else 500,
                        http=OfficialHTTP(archive=archive,seconds=1800 if a.mode=='collector-drain' else 10000),require_archive=True,
                        catch_up_date=day if day<stamp.date() else None)
                if a.mode=='collector-drain':
                    from packages.pms.collection_repair import drain_collection
                    result=drain_collection(once,service.locked,on_progress=lambda r:(
                        save({'progress':r}),print(json.dumps(r,default=str),flush=True)))
                else:result=once()
            else:
                from packages.pms.campaign_readiness import readiness_query,assess_readiness
                from packages.pms.config import load_strategy_config
                from packages.pms.matrix_data import code_fingerprint,consistent_inputs
                from packages.pms.daily_orchestrator import DailyServices
                cfg=load_strategy_config()
                with consistent_inputs(engine) as snapshot:
                    facts=snapshot.execute(readiness_query(day)).scalar_one()
                    class ReadOnlyEngine:
                        def connect(self):return snapshot
                    result=assess_readiness(facts,cfg,day=day,code_version=code_fingerprint())
                    result['read_only_rehearsal']=DailyServices(ReadOnlyEngine(),cfg).preflight(day,now)
        else:
            method=getattr(service,a.mode)
            result=method(on_progress=lambda r:(save({'progress':r}),print(json.dumps(r,default=str),flush=True)))
        report.update(result)
        service.locked()
        report['archive']=archive.summary()
    except Exception as exc:report.update(status='FAILED',error_code=safe_error(exc))
    finally:
        report['finished_at']=datetime.now(IST).isoformat();save({})
        if engine:
            try:
                with engine.begin() as c:
                    c.execute(text("""insert into pms.worker_runs(id,worker_name,job_name,status,started_at,finished_at,details)
                        values(:id,'targeted_source_repair','paper_data_repair',:status,:started_at,:finished_at,cast(:details as jsonb))"""),
                        {**report,'details':json.dumps(report,default=str)})
            except Exception as exc:
                report.update(status='FAILED',journal_error=safe_error(exc));save({})
            finally:engine.dispose()
    return 0 if report['status'] in {'SUCCESS','READY_FOR_ACTIVATION'} else 2


if __name__=='__main__':raise SystemExit(main())
