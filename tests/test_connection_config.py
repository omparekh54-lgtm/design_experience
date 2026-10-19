"""Regression coverage for the deployed placeholder and TLS-policy gap."""

import pytest

from packages.pms.connection_config import placeholder_password, tls_connect_options
from packages.pms.deployment_readiness import environment_checks
from scripts.start_cloud_api import configuration_errors


@pytest.mark.parametrize("message,code", [
    ('root certificate file "system" does not exist', "DATABASE_CA_CONFIGURATION"),
    ("SSL error: certificate verify failed", "DATABASE_CERTIFICATE_VERIFICATION"),
    ('server certificate does not match host name "private"', "DATABASE_CERTIFICATE_HOSTNAME"),
    ("password authentication failed for user private", "DATABASE_AUTHENTICATION"),
    ("could not translate host name private", "DATABASE_DNS"),
    ("connection timed out private", "DATABASE_CONNECT_TIMEOUT"),
    ("connection refused private", "DATABASE_CONNECTION_REFUSED"),
    ("postgresql://operator:private@host/db", "DATABASE_CONNECTION_FAILED"),
])
def test_connection_diagnostic_never_returns_driver_text(message, code):
    from packages.pms.connection_config import connection_failure_code
    from sqlalchemy.exc import OperationalError
    assert connection_failure_code(OperationalError("", {}, RuntimeError(message))) == code


@pytest.mark.parametrize("value", ["[YOUR-PASSWORD]", "%5BYOUR-PASSWORD%5D", "YOUR_DATABASE_PASSWORD"])
def test_actual_placeholder_spellings_are_rejected(value):
    assert placeholder_password(value)


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_cloud_policy_strengthens_tls(mode):
    assert tls_connect_options({"sslmode": mode}, {"PMS_DATABASE_VERIFY_FULL": "1"}) == {
        "sslmode": "verify-full", "sslrootcert": "system",
    }


def test_cloud_policy_keeps_a_reviewed_custom_ca():
    assert tls_connect_options({"sslrootcert": "/etc/secrets/database-ca.crt"},
                               {"PMS_DATABASE_VERIFY_FULL": "1"})["sslrootcert"] == "/etc/secrets/database-ca.crt"
    with pytest.raises(ValueError):
        tls_connect_options({"sslmode": "disable"}, {"PMS_DATABASE_VERIFY_FULL": "1"})


def test_runtime_applies_cloud_certificate_policy_before_connecting(monkeypatch):
    from packages.pms import database
    seen = []
    monkeypatch.setenv("PMS_DATABASE_VERIFY_FULL", "1")
    monkeypatch.setattr(database, "create_engine", lambda url, **kwargs: seen.append(kwargs))
    database.online_engine("postgresql://operator:private@pool.example/db?sslmode=require")
    assert seen[0]["connect_args"]["sslmode"] == "verify-full"
    assert seen[0]["connect_args"]["sslrootcert"] == "system"
    with pytest.raises(ValueError, match="placeholder"):
        database.online_engine("postgresql://operator:%5BYOUR-PASSWORD%5D@pool.example/db")
    assert len(seen) == 1


def test_guard_and_assessment_use_the_same_tls_policy():
    env = {
        "DATABASE_URL": "postgresql://operator:private@pool.example/db",
        "OPS_API_KEY": "x" * 40, "EXECUTION_MODE": "PAPER",
        "LIVE_TRADING_ENABLED": "false", "RUN_REFERENCE_GAP_BACKFILL": "0",
        "PMS_ALLOW_PRIVATE_HTTP": "0", "PMS_CLOUD_COLLECTION_REQUIRED": "1",
        "PMS_DATABASE_VERIFY_FULL": "1",
    }
    assert not configuration_errors(env)
    checks = {c["code"]: c for c in environment_checks(env)}
    assert checks["DATABASE_CONFIGURATION"]["status"] == "PASS"
    # Match the runtime URL parser for legitimate password punctuation.
    env["DATABASE_URL"] = "postgresql://operator:private[pass]@pool.example/db"
    assert not configuration_errors(env)
    env["DATABASE_URL"] = "postgresql://operator:%5BYOUR-PASSWORD%5D@pool.example/db"
    assert "DATABASE_URL" in configuration_errors(env)
    checks = {c["code"]: c for c in environment_checks(env)}
    assert checks["DATABASE_CONFIGURATION"]["status"] == "BLOCKED"
