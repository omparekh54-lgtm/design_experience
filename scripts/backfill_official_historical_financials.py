from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text

from apps.api.main import _render_safe_database_url
from packages.data_layer.cloud_sources import OfficialHTTP, SourceError
from packages.data_layer.cloud_storage import CloudStore
from packages.data_layer.exchange_financials import NSEIntegratedFinancialsProvider, filings_from_exchange_record
from packages.data_layer.private_archive import PrivateArchive
from packages.pms.historical_dataset import discover_financial_history, require_financial_period_identity


def universe(engine):
    with engine.connect() as c:
        rows=[dict(r) for r in c.execute(text("""
          select id,nse_symbol,isin,listed_from,delisted_on,active,metadata
          from pms.instruments where exchange='NSE' and nse_symbol is not null
            and btrim(nse_symbol)<>'' order by id
        """)).mappings().all()]
    counts={}
    for r in rows: counts[r['nse_symbol']]=counts.get(r['nse_symbol'],0)+1
    return [r for r in rows if counts[r['nse_symbol']]==1]


def run(start:date,end:date,shard:int,shards:int,output:Path):
    if not 0<=shard<shards: raise ValueError('invalid shard')
    raw=os.environ.get('DATABASE_URL') or os.environ.get('PMS_DATABASE_URL')
    if not raw: raise RuntimeError('DATABASE_URL/PMS_DATABASE_URL is required')
    engine=create_engine(_render_safe_database_url(raw),pool_pre_ping=True,
                         connect_args={'options':'-c search_path=pms,public'})
    require_financial_period_identity(engine)
    rows=universe(engine)
    selected=[r for n,r in enumerate(rows) if n%shards==shard]
    archive=PrivateArchive.from_env(); http=OfficialHTTP(seconds=21000,archive=archive)
    provider=NSEIntegratedFinancialsProvider(); store=CloudStore(engine)
    # Route historical, including inactive, symbols through exact unique database identities.
    store._known={r['nse_symbol']:{'id':r['id'],'symbol':r['nse_symbol'],'canonical_symbol':r['nse_symbol'],
                                  'isin':r['isin'],'listed_from':r['listed_from'],'sector':None,
                                  'metadata':r.get('metadata') or {}} for r in rows}
    report={'start':str(start),'end':str(end),'shard':shard,'shards':shards,'symbols':len(selected),
            'processed':0,'discovered':0,'stored':0,'failures':[]}
    for n,r in enumerate(selected,1):
        symbol=r['nse_symbol']
        from_date=max(start,r['listed_from']) if r['listed_from'] else start
        to_date=min(end,r['delisted_on']) if r['delisted_on'] else end
        if to_date < from_date:
            report['processed']+=1; continue
        try:
            records=discover_financial_history(provider,symbol,from_date,to_date,page_size=200,max_pages=100)
            filings=[]; docs=[]
            for rec in records:
                if not rec.xbrl_uri: continue
                doc=http.fetch(rec.xbrl_uri,financial_render=rec.xbrl_uri.endswith('_iXBRL_WEB.html'))
                parsed=filings_from_exchange_record(rec,doc.body)
                filings.extend(parsed); docs.append((doc,rec))
            if filings: store.financial(filings)
            with engine.begin() as c:
                for doc,rec in docs:
                    store.receipt(c,'HISTORICAL_FINANCIAL_XBRL',[doc],{
                        'symbol':symbol,'period_end':str(rec.period_end),'filed_at':str(rec.filed_at),
                        'complete':True})
            report['processed']+=1; report['discovered']+=len(records); report['stored']+=len(filings)
            print(json.dumps({'symbol':symbol,'n':n,'of':len(selected),'records':len(records),'filings':len(filings)}),flush=True)
        except Exception as exc:
            code=str(exc) if isinstance(exc,SourceError) else type(exc).__name__
            report['failures'].append({'symbol':symbol,'code':code})
            print(json.dumps({'symbol':symbol,'failure':code}),flush=True)
    output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(report,indent=2,default=str)+'\n')
    engine.dispose()
    if report['failures']: raise SystemExit(2)
    return report


def main():
    p=argparse.ArgumentParser(); p.add_argument('--start',type=date.fromisoformat,default=date(2016,1,1))
    p.add_argument('--end',type=date.fromisoformat,required=True); p.add_argument('--shard',type=int,required=True)
    p.add_argument('--shards',type=int,required=True); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); print(json.dumps(run(a.start,a.end,a.shard,a.shards,a.output),indent=2,default=str))

if __name__=='__main__': main()
