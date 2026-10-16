import pytest

from packages.pms.brokers import AngelOneBroker, BrokerOrderRequest
from packages.pms.safety import SafetyStatus


def test_live_order_requires_explicit_live_approval_and_enabled_state():
    assert SafetyStatus("PAPER", True, False, None).can_place_live_order is False
    assert SafetyStatus("LIVE_APPROVAL", False, False, None).can_place_live_order is False
    assert SafetyStatus("LIVE_APPROVAL", True, True, "stop").can_place_live_order is False
    assert SafetyStatus("LIVE_APPROVAL", True, False, None).can_place_live_order is True


class _FakeSafety:
    def __init__(self, status): self._status = status
    def status(self): return self._status


def test_angel_adapter_refuses_order_before_touching_broker_when_not_approved():
    broker = object.__new__(AngelOneBroker)
    broker.safety = _FakeSafety(SafetyStatus("PAPER", True, False, None))
    broker.client = object()
    broker.lookup = object()
    with pytest.raises(PermissionError):
        broker.place_order(BrokerOrderRequest("NSE:ABC", "BUY", 1, idempotency_key="x"))


def test_broker_order_request_defaults_to_market():
    req = BrokerOrderRequest("NSE:ABC", "BUY", 10)
    assert req.order_type == "MARKET"
    assert req.quantity == 10
