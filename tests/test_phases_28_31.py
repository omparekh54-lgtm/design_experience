import pytest

from packages.pms.live_validation import LiveValidationLimits, LiveValidationService
from packages.pms.paper_validation import PaperValidationCriteria
from packages.pms.production_readiness import MIN_LIVE_VALIDATION_OBSERVATIONS, ProductionReadinessController
from packages.pms.safety import SafetyStatus


def test_live_validation_is_separate_from_normal_live_approval():
    validation = SafetyStatus("LIVE_VALIDATION", True, False, None)
    approval = SafetyStatus("LIVE_APPROVAL", True, False, None)
    assert validation.can_place_validation_order is True
    assert validation.can_place_live_order is False
    assert approval.can_place_live_order is True
    assert approval.can_place_validation_order is False


def test_validation_approval_requires_separate_new_orders_switch():
    class FakeSafety:
        def status(self):
            return SafetyStatus("LIVE_VALIDATION", False, False, None)

    service = object.__new__(LiveValidationService)
    service.safety = FakeSafety()
    with pytest.raises(PermissionError, match="blocked by safety state"):
        service.approve("proposal", actor="test")


def test_live_auto_cannot_be_promoted_even_without_database_access():
    controller = object.__new__(ProductionReadinessController)
    with pytest.raises(ValueError, match="LIVE_AUTO remains disabled"):
        controller.promote("LIVE_AUTO", actor="test")


def test_invalid_live_validation_envelope_fails_before_database_access():
    service = object.__new__(LiveValidationService)
    limits = LiveValidationLimits(max_capital=1000, max_order_value=2000, max_open_positions=1, max_daily_loss=100)
    with pytest.raises(ValueError, match="max_order_value cannot exceed max_capital"):
        service.create_session(limits, actor="test")


def test_live_validation_service_never_submits_to_broker():
    assert not hasattr(LiveValidationService, "submit")
    assert not hasattr(LiveValidationService, "approve_and_submit")


def test_live_approval_readiness_requires_multiple_observations():
    assert MIN_LIVE_VALIDATION_OBSERVATIONS >= 5


def test_paper_validation_defaults_require_meaningful_evidence():
    criteria = PaperValidationCriteria()
    assert criteria.min_calendar_days >= 60
    assert criteria.min_fills > 0
    assert criteria.max_reconciliation_mismatches == 0
    assert criteria.max_worker_failures == 0
    assert criteria.max_data_quality_errors == 0
