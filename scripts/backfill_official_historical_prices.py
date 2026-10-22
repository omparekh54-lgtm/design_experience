from __future__ import annotations

import argparse
import csv
import io
import json
import os
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text

from apps.api.main import _render_safe_database_url
from packages.data_layer.cloud_sources import OfficialHTTP, PrivateArchive if False else OfficialHTTP
from packages.data_layer.cloud_sources import SourceError, bhavcopy_url, parse_bhavcopy, number
from packages.data_layer.private_archive import PrivateArchive


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
    out=[]
    seen=set()
    for r in reader:
        r={str(k).strip(): (v.strip() if isinstance(v,str) else v) for k,v in r.items()}
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
        rows=[dict(r) for r in c.execute(text("""
            select id,upper(coalesce(nse_symbol,replace(trading_symbol,'-EQ',''))) symbol,
                   upper(coalesce(isin,'')) isin,listed_from,delisted_on,active
            from pms.instruments where exchange='NSE'
        """)).mappings().all()]
    return rows


def candidates(rows, day):
    by_symbol={}; by_isin={}
    for r in rows:
        if r['listed_from'] and day < r['listed_from']:
            continue
        if r['delisted_on'] and day >= r['delisted_on']:
            continue
        if r['symbol']:
            by_symbol.setdefault(r['symbol'],[]).append(r)
        if r['isin']:
            by_isin.setdefault(r['isin'],[]).append(r)
    return by_symbol,by_isin


def match(row, by_symbol, by_isin):
    sym=row['symbol'].upper(); isin=row['isin'].upper()
    exact=[r for r in by_symbol.get(sym,[]) if r['isin']==isin]
    if len(exact)==1:
        return exact[0]
    isin_only=by_isin.get(isin,[])
    if len(isin_only)==1:
        return isin_only[0]
    return None


def sessions(engine,start,end):
    with engine.connect() as c:
        return list(c.execute(text("""
          select distinct trade_date from pms.module_e_benchmark_bars
          where benchmark_code='NIFTY50' and source in ('NSE_HISTORICAL_OR','NSE_DAILY_INDEX')
            and trade_date between :s and :e order by trade_date
        """),{'s':start,'e':end}).scalars())


def ingest(start:date,end:date,output:Path):
    raw=os.environ.get('DATABASE_URL') or os.environ.get('PMS_DATABASE_URL')
    if not raw: raise RuntimeError('DATABASE_URL/PMS_DATABASE_URL is required')
    engine=create_engine(_render_safe_database_url(raw),pool_pre_ping=True,
                         connect_args={'options':'-c search_path=pms,public'})
    archive=PrivateArchive.from_env()
    http=OfficialHTTP(seconds=20000,archive=archive)
    master=instruments(engine)
    days=sessions(engine,start,end)
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
    report={'start':str(start),'end':str(end),'sessions':len(days),'completed':0,'written':0,
            'unmatched':0,'ambiguous_samples':[],'failures':[]}
    for n,day in enumerate(days,1):
        try:
            doc,rows,fmt=fetch_day(http,day)
            by_symbol,by_isin=candidates(master,day)
            payload=[]; unmatched=[]
            for r in rows:
                inst=match(r,by_symbol,by_isin)
                if not inst:
                    unmatched.append({'symbol':r['symbol'],'isin':r['isin'],'series':r['series']})
                    continue
                payload.append({'instrument_id':inst['id'],'trade_date':day,'open':r['open'],'high':r['high'],
                                'low':r['low'],'close':r['close'],'volume':r['volume'],'turnover':r['turnover'],'source':SOURCE})
            with engine.begin() as c:
                for i in range(0,len(payload),1000): c.execute(insert,payload[i:i+1000])
                c.execute(receipt,{'sha':doc.sha256,'uri':doc.url,'metadata':json.dumps({
                    'trade_date':str(day),'format':fmt,'source_rows':len(rows),'written':len(payload),
                    'unmatched':len(unmatched),'archive_key':doc.archive_key,'complete':True})})
            report['completed']+=1; report['written']+=len(payload); report['unmatched']+=len(unmatched)
            if unmatched and len(report['ambiguous_samples'])<50:
                report['ambiguous_samples'].extend(unmatched[:50-len(report['ambiguous_samples'])])
            print(json.dumps({'session':n,'of':len(days),'date':str(day),'written':len(payload),'unmatched':len(unmatched),'format':fmt}),flush=True)
        except Exception as exc:
            code=str(exc) if isinstance(exc,SourceError) else type(exc).__name__
            report['failures'].append({'date':str(day),'code':code})
            print(json.dumps({'date':str(day),'failure':code}),flush=True)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2,default=str)+'\n')
    engine.dispose()
    if report['failures']:
        raise SystemExit(2)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--start',type=date.fromisoformat,required=True)
    p.add_argument('--end',type=date.fromisoformat,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(ingest(a.start,a.end,a.output),indent=2,default=str))

if __name__=='__main__': main()
