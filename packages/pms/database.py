"""Bounded online connections; research runners keep their explicit offline profiles."""

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def online_engine(url, *, worker=False):
    parsed = make_url(url)
    args = {
        "connect_timeout": 10,
        "options": "-c search_path=pms,public -c statement_timeout="
        + ("300000" if worker else "30000")
        + " -c lock_timeout=5000 -c idle_in_transaction_session_timeout=300000",
    }
    if (parsed.host or "").lower() not in {"localhost", "127.0.0.1", "::1", ""}:
        if parsed.query.get("sslmode", "require") not in {
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise ValueError("Remote online database connections require TLS")
        if "sslmode" not in parsed.query:
            args["sslmode"] = "require"
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=3 if worker else 2,
        max_overflow=1 if worker else 3,
        pool_timeout=5,
        pool_recycle=300,
        connect_args=args,
    )
