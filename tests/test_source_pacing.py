from packages.data_layer.source_pacing import SourcePacer
import pytest


def test_source_clients_share_host_schedule_without_bursting():
    now = [10.0]
    waits = []
    def sleep(delay):
        waits.append(delay)
        now[0] += delay
    pacer = SourcePacer(interval=.55, clock=lambda: now[0], sleep=sleep)
    pacer.wait('https://www.nseindia.com/api/a', 100)
    pacer.wait('https://www.nseindia.com/api/b', 100)
    assert waits == pytest.approx([.55])
    pacer.wait('https://nsearchives.nseindia.com/a.xml', 100)
    assert len(waits) == 1
    pacer.wait('https://www.nseindia.com/api/c', 100)
    assert waits == pytest.approx([.55, .55])


def test_pacing_does_not_wait_beyond_collection_deadline():
    pacer = SourcePacer(clock=lambda: 10, sleep=lambda _: pytest.fail('deadline exceeded'))
    with pytest.raises(TimeoutError):
        pacer.wait('https://www.nseindia.com/api/a', 10)
