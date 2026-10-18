"""Runtime-only database URL normalization for hosted Python processes.

This module is imported automatically by Python's site initialization when the
repository root is on sys.path. It never logs credentials and only rewrites the
Supabase connection address/user needed for IPv4-compatible session pooling.
"""
from __future__ import annotations

import os
from urllib.parse import quote, unquote, urlsplit, urlunsplit


def _normalize_supabase_database_url() -> None:
    raw = os.getenv("DATABASE_URL", "").strip()
    project_ref = os.getenv("SUPABASE_PROJECT_REF", "").strip()
    if not raw or not project_ref:
        return
    try:
        parsed = urlsplit(raw)
    except Exception:
        return

    host = (parsed.hostname or "").lower()
    if not host:
        return

    is_direct = host == f"db.{project_ref}.supabase.co"
    is_pooler = host.endswith(".pooler.supabase.com")
    if not (is_direct or is_pooler):
        return

    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if not password:
        return

    if is_direct:
        region = os.getenv("SUPABASE_POOLER_REGION", "ap-south-1").strip() or "ap-south-1"
        host = os.getenv("SUPABASE_POOLER_HOST", f"aws-0-{region}.pooler.supabase.com").strip()
        port = 5432
    else:
        port = parsed.port or 5432

    if username in {"", "postgres"}:
        username = f"postgres.{project_ref}"

    userinfo = f"{quote(username, safe='.') }:{quote(password, safe='')}"
    netloc = f"{userinfo}@{host}:{port}"
    os.environ["DATABASE_URL"] = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


_normalize_supabase_database_url()
