"""Fail closed before importing the API; this entrypoint never migrates or trades."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.pms.connection_config import placeholder_password, tls_connect_options


def configuration_errors(env):
    errors = []
    required = {
        "EXECUTION_MODE": "PAPER",
        "LIVE_TRADING_ENABLED": "false",
        "RUN_REFERENCE_GAP_BACKFILL": "0",
        "PMS_ALLOW_PRIVATE_HTTP": "0",
        "PMS_CLOUD_COLLECTION_REQUIRED": "1",
    }
    for name, value in required.items():
        if env.get(name) != value:
            errors.append(name)
    key = env.get("OPS_API_KEY", "")
    if not 32 <= len(key) <= 256 or key.isspace():
        errors.append("OPS_API_KEY")
    try:
        u = make_url(env.get("DATABASE_URL", ""))
        query = u.query
        if any(not isinstance(value, str) for value in query.values()):
            raise ValueError("Ambiguous connection settings")
        tls = tls_connect_options(query, env, host=u.host or "")
        if (
            u.drivername not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}
            or not u.host
            or u.host.lower() in {"localhost", "127.0.0.1", "::1"}
            or not u.username
            or not u.password
            or placeholder_password(u.password)
            or not u.database
            or tls["sslmode"] != "verify-full"
        ):
            errors.append("DATABASE_URL")
    except (ValueError, ArgumentError):
        errors.append("DATABASE_URL")
    try:
        port = env.get("PORT", "10000")
        if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
            errors.append("PORT")
    except ValueError:
        errors.append("PORT")
    return errors


def main():
    errors = configuration_errors(os.environ)
    if errors:
        print(json.dumps({"status": "BLOCKED", "invalid_configuration_keys": errors}), file=sys.stderr)
        return 2
    # No dotenv fallback, reload process, background worker or automatic migration.
    os.chdir(ROOT)
    os.execv(sys.executable, [
        sys.executable, "-m", "uvicorn", "apps.api.main_exec:app",
        "--host", "0.0.0.0", "--port", os.environ.get("PORT", "10000"),
        "--workers", "1", "--no-proxy-headers",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
