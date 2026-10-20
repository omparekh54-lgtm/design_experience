"""Independent watchdog and deduplicated GitHub incident routing. Never trades."""
import argparse
import hashlib
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import urllib.request
import urllib.error

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from packages.data_layer.cloud_sources import IST
from packages.data_layer.private_archive import PrivateArchive
from packages.pms.cloud_watchdog import database_checks
from packages.pms.database import online_engine
from scripts.copy_pms_to_neon import connection_url, BUCKET

REPO = 'omparekh54-lgtm/design_experience'
TITLE = '[PMS watchdog] Operational incident'
DRILL_TITLE = '[PMS watchdog] Alert routing drill'


def github(method, path, data=None):
    req = urllib.request.Request('https://api.github.com/repos/'+REPO+path,
        data=json.dumps(data).encode() if data is not None else None, method=method,
        headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],
                 'Accept':'application/vnd.github+json','Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as r:
        return json.load(r)


def route_incident(codes, *, drill=False, api=github):
    title = DRILL_TITLE if drill else TITLE
    issues = []
    for page in range(1,11):
        batch = api('GET',f'/issues?state=open&per_page=100&page={page}')
        issues.extend(i for i in batch if i.get('title')==title and 'pull_request' not in i
                      and i.get('user',{}).get('login')=='github-actions[bot]'
                      and '<!-- pms-watchdog-v1 -->' in (i.get('body') or ''))
        if len(batch)<100:
            break
    else:
        raise RuntimeError('incident_search_limit')
    body = '<!-- pms-watchdog-v1 -->\nOperational checks require attention.\n\nCodes: '+', '.join(sorted(set(codes)))
    run_id = os.getenv('GITHUB_RUN_ID','')
    if run_id.isdigit():
        body += '\n\nEvidence: https://github.com/'+REPO+'/actions/runs/'+run_id
    body += '\n\nNo trading permission has been changed. No private records are included.'
    if codes:
        if issues:
            issue = api('PATCH',f"/issues/{issues[0]['number']}",{'body':body})
        else:
            issue = api('POST','/issues',{'title':title,'body':body,
                                        'assignees':['omparekh54-lgtm']})
        # A drill proves issue delivery and the recovery-close path without
        # falsely closing a real incident or changing the application.
        if drill:
            api('PATCH',f"/issues/{issue['number']}",{'state':'closed','state_reason':'completed'})
        return {'status':'DRILL_DELIVERED_AND_CLOSED' if drill else 'INCIDENT_OPEN',
                'issue_number':issue['number']}
    for issue in issues:
        api('PATCH',f"/issues/{issue['number']}",{'state':'closed','state_reason':'completed',
            'body':'<!-- pms-watchdog-v1 -->\nAll configured watchdog checks passed. Recovery does not authorize trading.'})
    return {'status':'HEALTHY','closed_issues':len(issues)}


def web_checks():
    codes = []
    for path in ['/health','/ready']:
        try:
            with urllib.request.urlopen('https://pms-status-api.onrender.com'+path,timeout=60) as r:
                if r.status!=200 or r.headers.get('X-Content-Type-Options')!='nosniff':
                    codes.append('API_HEALTH_OR_SECURITY_HEADERS')
        except Exception:
            codes.append('API_UNAVAILABLE')
    try:
        req=urllib.request.Request('https://pms-status-api.onrender.com/v1/execution/disable-orders',
                                   data=b'{}',headers={'Content-Type':'application/json'})
        urllib.request.urlopen(req,timeout=30).close()
        codes.append('API_UNAUTHENTICATED_MUTATION_ACCEPTED')
    except urllib.error.HTTPError as e:
        if e.code!=401:
            codes.append('API_MUTATION_AUTH_NOT_VERIFIED')
    except Exception:
        codes.append('API_MUTATION_AUTH_NOT_VERIFIED')
    try:
        with urllib.request.urlopen('https://pms-status-dashboard.onrender.com/',timeout=60) as r:
            if r.status!=200:
                codes.append('DASHBOARD_UNAVAILABLE')
    except Exception:
        codes.append('DASHBOARD_UNAVAILABLE')
    return codes


def backup_check(archive, now):
    newest = None
    seen = 0
    for page in archive.client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET,Prefix='recovery-evidence/'):
        for obj in page.get('Contents',[]):
            seen += 1
            if seen>1000:
                return 'BACKUP_EVIDENCE_LIMIT'
            if obj['Key'].endswith('.json') and (newest is None or obj['LastModified']>newest['LastModified']):
                newest=obj
    if newest is None:
        return 'BACKUP_RESTORE_NOT_VERIFIED'
    response=archive.client.get_object(Bucket=BUCKET,Key=newest['Key'])
    body=response['Body']
    try:
        raw=body.read(16385)
    finally:
        body.close()
    if len(raw)>16384 or hashlib.sha256(raw).hexdigest()!=response.get('Metadata',{}).get('sha256'):
        return 'BACKUP_EVIDENCE_INVALID'
    r=json.loads(raw)
    if r.get('status')!='RESTORE_VERIFIED':
        return 'BACKUP_RESTORE_FAILED'
    stamp=datetime.fromisoformat(r['snapshot_at'])
    if stamp.tzinfo is None or not timedelta(0)<=now-stamp<=timedelta(days=8):
        return 'BACKUP_STALE'
    return None


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--drill',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    if a.output.exists():
        p.error('Output already exists')
    now=datetime.now(IST)
    r={'checked_at':now.isoformat(),'codes':[],'paper_activated':False}
    if a.drill:
        r['codes']=['SIMULATED_COLLECTION_FAILURE']
    else:
        engine=None
        try:
            engine=online_engine(connection_url(os.getenv('NEON_PMS_DATABASE_URL',''),destination=True),worker=True)
            db=database_checks(engine,now)
            r.update(db)
            r['tls_verified_full']=True
        except Exception:
            r['codes'].append('DATABASE_MONITOR_UNAVAILABLE')
        finally:
            if engine is not None:
                engine.dispose()
        r['codes'].extend(web_checks())
        try:
            archive=PrivateArchive.from_env()
            r['archive_privacy_verified']=True
            if archive.used>3_200_000_000:
                r['codes'].append('ARCHIVE_CAPACITY_WARNING')
            code=backup_check(archive,now)
            if code:
                r['codes'].append(code)
        except Exception:
            r['codes'].append('ARCHIVE_MONITOR_UNAVAILABLE')
        event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text()) if os.getenv('GITHUB_EVENT_PATH') else {}
        if event.get('workflow_run',{}).get('conclusion') not in (None,'success','skipped','neutral'):
            r['codes'].append('PIPELINE_FAILURE')
        try:
            runs=github('GET','/actions/workflows/pms-recovery-backup.yml/runs?per_page=1&branch=main')['workflow_runs']
            if runs and runs[0]['status']=='completed' and runs[0]['conclusion']!='success':
                r['codes'].append('BACKUP_PIPELINE_FAILED')
        except Exception:
            r['codes'].append('BACKUP_PIPELINE_STATUS_UNAVAILABLE')
    r['codes']=sorted(set(r['codes']))
    try:
        r['routing']=route_incident(r['codes'],drill=a.drill)
    except Exception:
        r['codes'].append('ALERT_DELIVERY_FAILED')
    r['status']='DRILL_VERIFIED' if a.drill and r.get('routing',{}).get('status')=='DRILL_DELIVERED_AND_CLOSED' else 'ATTENTION' if r['codes'] else 'HEALTHY'
    a.output.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r))
    return 0 if r['status'] in {'HEALTHY','DRILL_VERIFIED'} else 2


if __name__=='__main__':
    raise SystemExit(main())
