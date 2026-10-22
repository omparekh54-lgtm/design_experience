from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from apps.api.main import _render_safe_database_url
from packages.data_layer.cloud_sources import OfficialHTTP, SourceError
from packages.data_layer.cloud_storage import CloudStore
from packages.data_layer.exchange_financials import NSEIntegratedFinancialsProvider, filings_from_exchange_record
from packages.data_layer.private_archive import PrivateArchive
from packages.pms.historical_dataset import discover_financial_history, require_financial_period_identity

ROOT = Path(__file__).resolve().parents[1]
COMPLETION_DATASET = 'HISTORICAL_FINANCIAL_SYMBOL_COMPLETE'
COMPLETION_URI = 'https://www.nseindia.com/companies-listing/corporate-filings-financial-results'

# Stable source observations plus their facts; exclude only ingestion clock
# metadata. A restored/deleted/changed fact must invalidate symbol completion.
FINANCIAL_STATE_SQL = """select md5(coalesce(string_agg(
    md5((to_jsonb(f)-'ingested_at')::text || coalesce((
      select string_agg(to_jsonb(v)::text,'' order by v.fact_name)
      from pms.financial_facts v where v.filing_id=f.id),'')),
    '' order by f.id),''))
    from pms.financial_filings f join pms.instruments i on i.id=f.instrument_id
    where upper(i.nse_symbol)=upper(:symbol) and f.source='NSE'
      and (f.filing_timestamp at time zone 'Asia/Kolkata')::date
          between cast(:start as date) and cast(:end as date)"""


def universe(engine):
    with engine.connect() as c:
        rows=[dict(r) for r in c.execute(text("""
          select id,nse_symbol,isin,listed_from,delisted_on,active,metadata
          from pms.instruments where exchange='NSE' and nse_symbol is not null
            and btrim(nse_symbol)<>'' and metadata->>'ingestion_duplicate_of' is null order by id
        """)).mappings().all()]
    counts={}
    for r in rows: counts[r['nse_symbol']]=counts.get(r['nse_symbol'],0)+1
    return [r for r in rows if counts[r['nse_symbol']]==1]


def optional_private_archive():
    raw=(os.environ.get('PMS_NEON_ARCHIVE_JSON') or '').strip()
    if not raw:
        print(json.dumps({'private_archive':'DISABLED','reason':'credentials_not_configured','provenance':'NSE_URL_PLUS_SHA256'}), flush=True)
        return None
    try:
        return PrivateArchive.from_env()
    except SourceError as exc:
        print(json.dumps({'private_archive':'DISABLED','reason':str(exc),'provenance':'NSE_URL_PLUS_SHA256'}), flush=True)
        return None


def _write_checkpoint(output: Path, report: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp=output.with_suffix(output.suffix+'.tmp')
    tmp.write_text(json.dumps(report,indent=2,default=str)+'\n',encoding='utf-8')
    tmp.replace(output)


def _load_checkpoint(output: Path, start: date, end: date, shard: int, shards: int) -> dict | None:
    if not output.is_file():
        return None
    try:
        data=json.loads(output.read_text(encoding='utf-8'))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if (data.get('start'),data.get('end'),data.get('shard'),data.get('shards')) != (str(start),str(end),shard,shards):
        return None
    if not isinstance(data.get('completed_symbols', []), list) or any(
            not isinstance(s, str) for s in data.get('completed_symbols', [])):
        return None
    return data


def _db_completed_symbols(engine, start: date, end: date, shard: int, shards: int) -> set[str]:
    """Use only explicit full-discovery completion receipts as resume authority."""
    with engine.connect() as c:
        state_sql = FINANCIAL_STATE_SQL.replace(':symbol', "s.metadata->>'symbol'")
        rows=c.execute(text("""
          select distinct upper(s.metadata->>'symbol') symbol
          from pms.provider_snapshots s
          where dataset=:dataset
            and s.metadata->>'complete'='true'
            and s.metadata->>'start'=:start
            and s.metadata->>'end'=:end
            and s.metadata->>'shard'=:shard
            and s.metadata->>'shards'=:shards
            and content_sha256 ~ '^[0-9a-fA-F]{64}$'
            and source_uri=:uri
            and s.id=(select max(t.id) from pms.provider_snapshots t
                where t.dataset=s.dataset and t.metadata->>'symbol'=s.metadata->>'symbol'
                  and t.metadata->>'start'=s.metadata->>'start' and t.metadata->>'end'=s.metadata->>'end'
                  and t.metadata->>'shard'=s.metadata->>'shard' and t.metadata->>'shards'=s.metadata->>'shards')
            and (s.metadata->>'stored_db_digest' is null or s.metadata->>'stored_db_digest'=(
        """ + state_sql + """))
            and (
              s.metadata->>'filings_stored'='0'
              or (
                case when s.metadata->>'filings_stored' ~ '^[0-9]+$'
                     then (s.metadata->>'filings_stored')::bigint else -1 end > 0
                and (select count(*) from pms.financial_filings f
                     join pms.instruments i on i.id=f.instrument_id
                     where upper(i.nse_symbol)=upper(s.metadata->>'symbol')
                       and f.source='NSE'
                       and (f.filing_timestamp at time zone 'Asia/Kolkata')::date between cast(:start as date) and cast(:end as date))
                    >= case when s.metadata->>'filings_stored' ~ '^[0-9]+$'
                            then (s.metadata->>'filings_stored')::bigint else -1 end
              )
            )
        """),{'dataset':COMPLETION_DATASET,'start':str(start),'end':str(end),
               'shard':str(shard),'shards':str(shards),'uri':COMPLETION_URI}).scalars().all()
    return {str(s).upper() for s in rows if s}


def _record_symbol_complete(conn, symbol: str, start: date, end: date, shard: int, shards: int,
                            records: int, filings: int) -> None:
    metadata={'symbol':symbol,'start':str(start),'end':str(end),'shard':shard,'shards':shards,
              'records_discovered':records,'filings_stored':filings,'complete':True}
    metadata['stored_db_digest'] = conn.execute(text(FINANCIAL_STATE_SQL),
        {'symbol':symbol,'start':str(start),'end':str(end)}).scalar_one()
    digest=hashlib.sha256(json.dumps(metadata,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    conn.execute(text("""
      insert into pms.provider_snapshots(provider,dataset,content_sha256,source_uri,metadata)
      values('PMS',:dataset,:sha,:uri,cast(:metadata as jsonb))
    """),{'dataset':COMPLETION_DATASET,'sha':digest,'uri':COMPLETION_URI,
           'metadata':json.dumps(metadata,sort_keys=True)})


def run(start:date,end:date,shard:int,shards:int,output:Path):
    if not 0<=shard<shards: raise ValueError('invalid shard')
    if end < start: raise ValueError('historical financial window is reversed')
    load_dotenv(ROOT / '.env')
    raw=os.environ.get('DATABASE_URL') or os.environ.get('PMS_DATABASE_URL')
    if not raw: raise RuntimeError('DATABASE_URL/PMS_DATABASE_URL is required')
    engine=create_engine(_render_safe_database_url(raw),pool_pre_ping=True,
                         connect_args={'options':'-c search_path=pms,public'})
    require_financial_period_identity(engine)
    rows=universe(engine)
    selected=[r for n,r in enumerate(rows) if n%shards==shard]
    selected_symbols={str(r['nse_symbol']).upper() for r in selected}
    archive=optional_private_archive()
    provider=NSEIntegratedFinancialsProvider(); store=CloudStore(engine)
    store._known={r['nse_symbol']:{'id':r['id'],'symbol':r['nse_symbol'],'canonical_symbol':r['nse_symbol'],
                                  'isin':r['isin'],'listed_from':r['listed_from'],'sector':None,
                                  'metadata':r.get('metadata') or {}} for r in rows}

    old=_load_checkpoint(output,start,end,shard,shards)
    local_symbols={str(s).upper() for s in ((old or {}).get('completed_symbols') or [])}
    db_symbols=_db_completed_symbols(engine,start,end,shard,shards) & selected_symbols
    completed_symbols=set(db_symbols)
    stale_local=sorted((local_symbols-selected_symbols) | (local_symbols-db_symbols))
    if stale_local:
        print(json.dumps({'checkpoint_reconciled':'DATABASE_WINS','stale_or_unverified_local_symbols':len(stale_local),'sample':stale_local[:10]}),flush=True)
    report={'start':str(start),'end':str(end),'shard':shard,'shards':shards,'symbols':len(selected),
            'processed':len(completed_symbols),'discovered':0,'stored':0,'failures':[],
            'completed_symbols':sorted(completed_symbols),'private_archive':bool(archive),
            'resumed':bool(completed_symbols),'checkpoint_authority':'DATABASE_SYMBOL_COMPLETION_RECEIPTS'}
    _write_checkpoint(output,report)
    try:
        for n,r in enumerate(selected,1):
            symbol=str(r['nse_symbol']).upper()
            if symbol in completed_symbols:
                print(json.dumps({'symbol':symbol,'n':n,'of':len(selected),'status':'database-complete'}),flush=True)
                continue
            from_date=max(start,r['listed_from']) if r['listed_from'] else start
            to_date=min(end,r['delisted_on']) if r['delisted_on'] else end
            if to_date < from_date:
                with engine.begin() as c:
                    _record_symbol_complete(c,symbol,start,end,shard,shards,0,0)
                completed_symbols.add(symbol); report['processed']+=1
                report['completed_symbols']=sorted(completed_symbols); _write_checkpoint(output,report)
                continue

            last_exc=None
            success=False
            for attempt in range(1,4):
                try:
                    http=OfficialHTTP(seconds=3600,archive=archive)
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
                                'complete':True,
                                'raw_source_preservation':'PRIVATE_ARCHIVE' if doc.archive_key else 'OFFICIAL_URL_PLUS_SHA256'})
                        _record_symbol_complete(c,symbol,start,end,shard,shards,len(records),len(filings))
                    report['processed']+=1; report['discovered']+=len(records); report['stored']+=len(filings)
                    completed_symbols.add(symbol); report['completed_symbols']=sorted(completed_symbols)
                    _write_checkpoint(output,report)
                    print(json.dumps({'symbol':symbol,'n':n,'of':len(selected),'records':len(records),'filings':len(filings)}),flush=True)
                    success=True
                    break
                except Exception as exc:
                    last_exc=exc
                    if attempt<3:
                        delay=min(10,2**(attempt-1))
                        print(json.dumps({'symbol':symbol,'attempt':attempt,'retry_after_seconds':delay,'error_type':type(exc).__name__}),flush=True)
                        time.sleep(delay)
            if not success:
                code=str(last_exc) if isinstance(last_exc,SourceError) else type(last_exc).__name__
                report['failures'].append({'symbol':symbol,'code':code})
                _write_checkpoint(output,report)
                print(json.dumps({'symbol':symbol,'failure':code}),flush=True)
        _write_checkpoint(output,report)
        if report['failures']: raise SystemExit(2)
        if report['processed'] != len(selected):
            raise RuntimeError(f"historical financial shard incomplete: {report['processed']}/{len(selected)}")
        return report
    finally:
        engine.dispose()


def main():
    p=argparse.ArgumentParser(); p.add_argument('--start',type=date.fromisoformat,default=date(2016,1,1))
    p.add_argument('--end',type=date.fromisoformat,required=True); p.add_argument('--shard',type=int,required=True)
    p.add_argument('--shards',type=int,required=True); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); print(json.dumps(run(a.start,a.end,a.shard,a.shards,a.output),indent=2,default=str))

if __name__=='__main__': main()
