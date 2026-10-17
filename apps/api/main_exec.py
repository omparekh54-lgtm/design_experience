from __future__ import annotations

import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

from apps.api.main import app
from apps.api.execution_routes import router as execution_router


@app.middleware("http")
async def protect_execution_mutations(request: Request, call_next):
    """Require a private operator key for execution-changing API calls.

    Read-only GET/HEAD/OPTIONS endpoints remain available to the status dashboard.
    If OPS_API_KEY is absent, all execution mutations fail closed.
    """
    path = request.url.path
    if path.startswith("/v1/execution/") and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        expected = os.getenv("OPS_API_KEY", "")
        supplied = request.headers.get("x-ops-key", "")
        if not expected or not supplied or not secrets.compare_digest(supplied, expected):
            return JSONResponse(status_code=401, content={"detail": "operator authentication required"})
    return await call_next(request)


app.include_router(execution_router)
