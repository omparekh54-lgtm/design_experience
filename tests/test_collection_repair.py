import pytest
from packages.pms.collection_repair import drain_collection


def receipt(remaining,status='PARTIAL',errors=None):
    return dict(status=status,complete=status=='SUCCESS',run_id=str(remaining),
        stages={'shareholding':dict(remaining=remaining,processed=200,errors=errors or [])})


def test_sequential_real_receipts_are_required_until_actual_complete():
    results=iter([receipt(400),receipt(200),receipt(0,'SUCCESS')]);events=[]
    def collect():events.append('collect');return next(results)
    result=drain_collection(collect,lambda:events.append('locked'))
    assert events==['locked','collect']*3
    assert result['complete'] is True and len(result['collector_passes'])==3


@pytest.mark.parametrize('last',[receipt(200,'FAILED'),receipt(200,errors=[{'code':'source_failed'}]),receipt(400)])
def test_failures_source_errors_and_stalled_backlogs_do_not_loop(last):
    results=iter([receipt(400),last]);calls=[]
    def collect():calls.append(1);return next(results)
    result=drain_collection(collect,lambda:None)
    assert len(calls)==2 and result['complete'] is False


def test_bounded_partial_receipt_does_not_become_collector_success():
    r=drain_collection(lambda:receipt(900),lambda:None,max_passes=1)
    assert r['status']=='PARTIAL' and r['complete'] is False
