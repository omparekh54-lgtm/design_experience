from __future__ import annotations

import argparse
import csv
import io
import json
import os
import time
import zipfile
from datetime import date, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from apps.api.main import _render_safe_database_url
from packages.data_layer.cloud_sources import OfficialHTTP, SourceError, bhavcopy_url, parse_bhavcopy, number
from packages.data_layer.private_archive import PrivateArchive

ROOT=Path(__file__).resolve().parents[1]
SOURCE = "NSE_UDIFF"


def legacy_url(day: date) -> str:
    return (
        "https://archives.nseindia.com/content/historical/EQUITIES/"
        + day.strftime("%Y/%b").upper()
        + "/cm"
        + day.strftime("%d%b%Y").upper()
        + "bhav.csv.zip"
    )


def parse_legacy(body: bytes, day: date) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members = archive.infolist()
        expected = "cm" + day.strftime("%d%b%Y").upper() + "bhav.csv"
        if len(members) != 1 or members[0].filename != expected:
            raise SourceError("legacy_bhavcopy_archive_invalid")
        raw = archive.read(members[0]).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    required = {"SYMBOL","SERIES","OPEN","HIGH","LOW","CLOSE","TOTTRDQTY","TOTTRDVAL","TIMESTAMP","ISIN"}
    if not reader.fieldnames or not required.issubset({x.strip() for x in reader.fieldnames}):
        raise SourceError("legacy_bhavcopy_schema_changed")
    out=[]; seen=set()
    for row in reader:
        r={str(k).strip(): (v.strip() if isinstance(v,str) else v) for k,v in row.items()}
        if r["SERIES"] not in {"EQ","BE","BZ","SM","ST","SZ"}:
            continue
        try:
            source_day=datetime.strptime(r["TIMESTAMP"],"%d-%b-%Y").date()
        except ValueError:
            raise SourceError("legacy_bhavcopy_wrong_date")
        if source_day != day:
            raise SourceError("legacy_bhavcopy_wrong_date")
        key=(r["SYMBOL"],r["SERIES"])
        if key in seen:
            raise SourceError("legacy_bhavcopy_duplicate_identity")
        seen.add(key)
        o,h,l,c=[number(r[k]) for k in ("OPEN","HIGH","LOW","CLOSE")]
        vol=number(r["TOTTRDQTY"],integer=True)
        turnover=number(r["TOTTRDVAL"])
        if vol == 0 and not any((o,h,l,c)):
            continue
        if min(o,h,l,c)<=0 or l>min(o,c) or h<max(o,c):
            raise SourceError("legacy_bhavcopy_invalid_ohlc")
        out.append(dict(symbol=r["SYMBOL"],series=r["SERIES"],isin=r["ISIN"],trade_date=day,
                        open=o,high=h,low=l,close=c,volume=vol,turnover=turnover))
    if not out:
        raise SourceError("legacy_bhavcopy_no_equities")
    return out


def fetch_day(http: OfficialHTTP, day: date):
    try:
        doc=http.fetch(bhavcopy_url(day))
        return doc, parse_bhavcopy(doc.body,day), "UDIFF"
    except SourceError as exc:
        if str(exc) not in {"source_http_404","bhavcopy_archive_invalid","csv_schema_changed","csv_header_invalid"}:
            raise
    doc=http.fetch(legacy_url(day))
    return doc, parse_legacy(doc.body,day), "LEGACY"


def instruments(engine):
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text("""
            select id,upper(coalesce(nse_symbol,replace(trading_symbol,'-EQ',''))) symbol,
                   upper(coalesce(isin,'')) isin,listed_from,delisted_on,active
            from pms.instruments where exchange='NSE'
              and metadata->>'ingestion_duplicate_of' is null
        """)).mappings().all()]


def candidates(rows, day):
    by_symbol={}; by_isin={}
    for r in rows:
        if r['listed_from'] and day < r['listed_from']: continue
        if r['delisted_on'] and day >= r['delisted_on']: continue
        if r['symbol']: by_symbol.setdefault(r['symbol'],[]).append(r)
        if r['isin']: by_isin.setdefault(r['isin'],[]).append(r)
    return by_symbol,by_isin


def match(row, by_symbol, by_isin):
    sym=row['symbol'].upper(); isin=row['isin'].upper()
    exact=[r for r in by_symbol.get(sym,[]) if r['isin']==isin]
    if len(exact)==1: return exact[0]
    isin_only=by_isin.get(isin,[])
    if len(isin_only)==1: return isin_only[0]
    return None


def sessions(engine,start,end):
    with engine.connect() as c:
        return list(c.execute(text("""
          select distinct trade_date from pms.module_e_benchmark_bars
          where benchmark_code='NIFTY50' and source in ('NSE_HISTORICAL_OR','NSE_DAILY_INDEX')
            and trade_date between :s and :e order by trade_date
        """),{'s':start,'e':end}).scalars())


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


def _write(output: Path, report: dict) -> None:
    output.parent.mkdir(parents=True,exist_ok=True)
    tmp=output.with_suffix(output.suffix+'.tmp')
    tmp.write_text(json.dumps(report,indent=2,default=str)+'\n',encoding='utf-8')
    tmp.replace(output)


def _checkpoint(output: Path,start: date,end: date) -> dict | None:
    if not output.is_file(): return None
    try: data=json.loads(output.read_text(encoding='utf-8'))
    except Exception: return None
    if data.get('start')!=str(start) or data.get('end')!=str(end): return None
    return data


def _db_completed_dates(engine, start: date, end: date) -> set[str]:
    """Database truth for resumability; local JSON is advisory only.

    A date is complete only when an official receipt exists and at least one
    official NSE price row for that date is still present. This prevents a stale
    local checkpoint from skipping work after a Neon restore/reset.
    """
    with engine.connect() as c:
        rows=c.execute(text("""
          select distinct (s.metadata->>'trade_date')::date trade_date
          from pms.provider_snapshots s
          where s.dataset='HISTORICAL_NSE_PRICES'
            and s.metadata->>'complete'='true'
            and (s.metadata->>'trade_date')::date between :start and :end
            and s.content_sha256 ~ '^[0-9a-fA-F]{64}$'
            and s.source_uri is not null and btrim(s.source_uri)<>''
            and exists (
              select 1 from pms.daily_prices p
              where p.trade_date=(s.metadata->>'trade_date')::date
                and lower(p.source) like 'nse%'
            )
        """),{'start':start,'end':end}).scalars().all()
    return {str(d) for d in rows}


def ingest(start:date,end:date,output:Path):
    load_dotenv(ROOT/'.env')
    raw=os.environ.get('DATABASE_URL') or os.environ.get('PMS_DATABASE_URL')
    if not raw: raise RuntimeError('DATABASE_URL/PMS_DATABASE_URL is required')
    engine=create_engine(_render_safe_database_url(raw),pool_pre_ping=True,
                         connect_args={'options':'-c search_path=pms,public'})
    archive=optional_private_archive()
    master=instruments(engine); days=sessions(engine,start,end)
    if not days: raise RuntimeError('official_nifty50_session_calendar_empty')
    insert=text("""
      insert into pms.daily_prices(instrument_id,trade_date,open,high,low,close,volume,turnover,source)
      values(:instrument_id,:trade_date,:open,:high,:low,:close,:volume,:turnover,:source)
      on conflict(instrument_id,trade_date,source) do update set
        open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
        volume=excluded.volume,turnover=excluded.turnover,ingested_at=now()
    """)
    receipt=text("""
      insert into pms.provider_snapshots(provider,dataset,content_sha256,source_uri,metadata)
      values('NSE','HISTORICAL_NSE_PRICES',:sha,:uri,cast(:metadata as jsonb))
    """)
    old=_checkpoint(output,start,end)
    local_dates=set((old or {}).get('completed_dates') or [])
    db_dates=_db_completed_dates(engine,start,end)
    completed_dates={str(d) for d in days if str(d) in db_dates}
    stale_local=sorted(local_dates-completed_dates)
    if stale_local:
        print(json.dumps({'checkpoint_reconciled':'DATABASE_WINS','stale_local_dates':len(stale_local),'sample':stale_local[:10]}),flush=True)
    report={'start':str(start),'end':str(end),'sessions':len(days),'completed':len(completed_dates),
            'completed_dates':sorted(completed_dates),'written':0,
            'unmatched':0,'ambiguous_samples':[],
            'failures':[],'private_archive':bool(archive),'resumed':bool(completed_dates),
            'checkpoint_authority':'DATABASE_RECEIPT_PLUS_PRICE_ROW'}
    _write(output,report)
    try:
        for n,day in enumerate(days,1):
            key=str(day)
            if key in completed_dates:
                print(json.dumps({'session':n,'of':len(days),'date':key,'status':'database-complete'}),flush=True)
                continue
            last_exc=None
            success=False
            for attempt in range(1,4):
                try:
                    http=OfficialHTTP(seconds=1200,archive=archive)
                    doc,rows,fmt=fetch_day(http,day); by_symbol,by_isin=candidates(master,day)
                    payload=[]; unmatched=[]
                    for r in rows:
                        inst=match(r,by_symbol,by_isin)
                        if not inst:
                            unmatched.append({'symbol':r['symbol'],'isin':r['isin'],'series':r['series']}); continue
                        payload.append({'instrument_id':inst['id'],'trade_date':day,'open':r['open'],'high':r['high'],
                                        'low':r['low'],'close':r['close'],'volume':r['volume'],'turnover':r['turnover'],'source':SOURCE})
                    with engine.begin() as c:
                        for i in range(0,len(payload),1000): c.execute(insert,payload[i:i+1000])
                        c.execute(receipt,{'sha':doc.sha256,'uri':doc.url,'metadata':json.dumps({
                            'trade_date':key,'format':fmt,'source_rows':len(rows),'written':len(payload),
                            'unmatched':len(unmatched),'archive_key':doc.archive_key,'complete':True,
                            'raw_source_preservation':'PRIVATE_ARCHIVE' if doc.archive_key else 'OFFICIAL_URL_PLUS_SHA256'})})
                    report['completed']+=1; report['written']+=len(payload); report['unmatched']+=len(unmatched)
                    completed_dates.add(key); report['completed_dates']=sorted(completed_dates)
                    if unmatched and len(report['ambiguous_samples'])<50:
                        report['ambiguous_samples'].extend(unmatched[:50-len(report['ambiguous_samples'])])
                    _write(output,report)
                    print(json.dumps({'session':n,'of':len(days),'date':key,'written':len(payload),'unmatched':len(unmatched),'format':fmt}),flush=True)
                    success=True
                    break
                except Exception as exc:
                    last_exc=exc
                    if attempt<3:
                        delay=min(10,2**(attempt-1))
                        print(json.dumps({'date':key,'attempt':attempt,'retry_after_seconds':delay,'error_type':type(exc).__name__}),flush=True)
                        time.sleep(delay)
            if not success:
                code=str(last_exc) if isinstance(last_exc,SourceError) else type(last_exc).__name__
                report['failures'].append({'date':key,'code':code})
                _write(output,report)
                print(json.dumps({'date':key,'failure':code}),flush=True)
        _write(output,report)
        if report['failures']: raise SystemExit(2)
        if report['completed'] != len(days):
            raise RuntimeError(f"historical price checkpoint incomplete: {report['completed']}/{len(days)}")
        return report
    finally:
        engine.dispose()


def main():
    p=argparse.ArgumentParser(); p.add_argument('--start',type=date.fromisoformat,required=True)
    p.add_argument('--end',type=date.fromisoformat,required=True); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); print(json.dumps(ingest(a.start,a.end,a.output),indent=2,default=str))

if __name__=='__main__': main()
