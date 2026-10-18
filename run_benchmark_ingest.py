from __future__ import annotations

import os

from sqlalchemy.engine import make_url

PROJECT_REF = os.getenv("SUPABASE_PROJECT_REF", "cxrkicqggsuylirzohqw").strip()
REGION = os.getenv("SUPABASE_POOLER_REGION", "ap-south-1").strip()


def _normalise_database_url(raw: str) -> str:
    url = make_url(raw)
    host = (url.host or "").lower()
    desired_user = f"postgres.{PROJECT_REF}"
    desired_host = f"aws-0-{REGION}.pooler.supabase.com"

    if host.startswith("db.") and host.endswith(".supabase.co"):
        url = url.set(host=desired_host, port=5432, username=desired_user)
    elif host.endswith(".pooler.supabase.com"):
        url = url.set(host=desired_host, port=5432, username=desired_user)

    # Preserve the password; never print the resulting URL.
    return url.render_as_string(hide_password=False)


def main() -> None:
    raw = os.getenv("DATABASE_URL") or os.getenv("PMS_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL/PMS_DATABASE_URL is required")
    os.environ["DATABASE_URL"] = _normalise_database_url(raw)

    from scripts.ingest_module_e_benchmarks import main as ingest_main

    ingest_main()


if __name__ == "__main__":
    main()
