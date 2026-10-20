from datetime import date,datetime,timedelta
import io
from pathlib import Path
import pytest
from packages.data_layer.cloud_sources import IST,SourceError
from packages.pms.cloud_watchdog import expected_session,assess_collection
from scripts.run_cloud_watchdog import route_incident
from scripts.rehearse_cloud_recovery import local_target,download_verified,CopyBlocked

NOW=datetime(2026,10,20,20,30,tzinfo=IST)


def test_expected_session_holiday_weekend_deadline_and_unknown_year():
    years={'2026':['2026-10-20']}
    assert expected_session(NOW,years)==date(2026,10,19)
    assert expected_session(datetime(2026,10,21,20,14,tzinfo=IST),years)==date(2026,10,19)
    assert expected_session(datetime(2026,10,21,20,15,tzinfo=IST),years)==date(2026,10,21)
    assert expected_session(datetime(2026,10,25,21,tzinfo=IST),years)==date(2026,10,23)
    assert expected_session(datetime(2026,10,25,21,tzinfo=IST),years,['2026-10-25'])==date(2026,10,25)
    with pytest.raises(SourceError):expected_session(NOW,{})
    with pytest.raises(SourceError):expected_session(NOW,years,(),['2026-10-19'])


def test_collection_missing_failed_stale_and_verified():
    assert assess_collection(None,NOW)=='COLLECTION_MISSED'
    r={'status':'RUNNING','started_at':NOW-timedelta(minutes=10)}
    assert assess_collection(r,NOW)=='COLLECTION_IN_PROGRESS'
    r['started_at']=NOW-timedelta(minutes=33)
    assert assess_collection(r,NOW)=='COLLECTION_STALE'
    r.update(status='PARTIAL',details={'complete':True})
    assert assess_collection(r,NOW)=='COLLECTION_INCOMPLETE'
    r.update(status='SUCCESS',finished_at=NOW,details={'complete':True,'version':'CLOUD_COLLECTION_v1',
        'raw_archive':{'status':'VERIFIED','anonymous_read_denied':True}})
    assert assess_collection(r,NOW) is None
    r['details']['raw_archive']['anonymous_read_denied']=False
    assert assess_collection(r,NOW)=='COLLECTION_INCOMPLETE'


class Issues:
    def __init__(self):self.rows=[];self.calls=[]
    def __call__(self,method,path,data=None):
        self.calls.append((method,path,data))
        if method=='GET':return [r for r in self.rows if r['state']=='open']
        if method=='POST':
            r={'number':len(self.rows)+1,'state':'open','user':{'login':'github-actions[bot]'},**data};self.rows.append(r);return r
        r=self.rows[int(path.rsplit('/',1)[-1])-1];r.update(data);return r


def test_alert_is_deduplicated_and_recovery_closes_it():
    api=Issues()
    assert route_incident(['COLLECTION_MISSED'],api=api)['status']=='INCIDENT_OPEN'
    route_incident(['COLLECTION_STALE'],api=api)
    assert len(api.rows)==1 and 'COLLECTION_STALE' in api.rows[0]['body']
    assert route_incident([],api=api)['closed_issues']==1
    assert api.rows[0]['state']=='closed'


def test_drill_delivery_cannot_close_real_incident():
    api=Issues();route_incident(['REAL_FAILURE'],api=api)
    assert route_incident(['SIMULATED_COLLECTION_FAILURE'],drill=True,api=api)['status']=='DRILL_DELIVERED_AND_CLOSED'
    assert api.rows[0]['state']=='open' and api.rows[1]['state']=='closed'


def test_alert_cannot_modify_someone_elses_issue_with_the_same_title():
    api=Issues();route_incident(['REAL_FAILURE'],api=api)
    api.rows[0]['user']['login']='outside-contributor'
    route_incident([],api=api)
    assert api.rows[0]['state']=='open'
    route_incident(['REAL_FAILURE'],api=api)
    assert len(api.rows)==2


@pytest.mark.parametrize('url',[
    'postgresql://postgres:password@production.example/pms_recovery',
    'postgresql://postgres:password@localhost/pms',
    'postgresql://postgres:password@localhost:6432/pms_recovery'])
def test_restore_refuses_nonisolated_target(url):
    with pytest.raises(CopyBlocked):local_target(url)


def test_restore_download_verifies_actual_bytes_and_refuses_tampering(tmp_path):
    class S3:
        def get_object(self,**kw):return {'Body':io.BytesIO(b'tampered')}
    with pytest.raises(CopyBlocked):
        download_verified(S3(),'reviewed/key',tmp_path/'archive','0'*64,8)
    with pytest.raises(CopyBlocked):
        download_verified(S3(),'reviewed/key',tmp_path/'oversize','0'*64,200*1024*1024)
