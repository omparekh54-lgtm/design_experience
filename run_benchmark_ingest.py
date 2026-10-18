from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

PROJECT_REF = os.getenv("SUPABASE_PROJECT_REF", "cxrkicqggsuylirzohqw").strip()
REGION = os.getenv("SUPABASE_POOLER_REGION", "ap-south-1").strip()


def _forced_pooler_url(raw: str) -> URL:
    """Build the Supabase session-pooler URL from parsed secret parts.

    We deliberately force the project-qualified pooler username instead of
    relying on string rewriting. Password/database/query are preserved from
    the GitHub secret and are never printed.
    """
    parsed = make_url(raw)
    host = parsed.host or f"aws-0-{REGION}.pooler.supabase.com"
    if not host.endswith(".pooler.supabase.com"):
        host = f"aws-0-{REGION}.pooler.supabase.com"

    return URL.create(
        drivername="postgresql+psycopg2",
        username=f"postgres.{PROJECT_REF}",
        password=parsed.password,
        host=host,
        port=5432,
        database=parsed.database or "postgres",
        query=dict(parsed.query),
    )


def main() -> None:
    raw = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL/PMS_DATABASE_URL is required")

    forced = _forced_pooler_url(raw)

    # Import after parsing the secret, then replace the script's engine factory
    # so it cannot fall back to an unqualified `postgres` pooler user.
    import scripts.ingest_module_e_benchmarks as ingest_module

    ingest_module._engine = lambda: create_engine(forced, pool_pre_ping=True)
    ingest_module.main()


if __name__ == "__main__":
    main()
