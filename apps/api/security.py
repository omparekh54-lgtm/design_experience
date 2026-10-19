"""Bounded transport and fail-closed mutation access for both API entry points."""

from __future__ import annotations
import asyncio
import logging
import os
import secrets
from uuid import uuid4
from starlette.responses import JSONResponse
from starlette.routing import Match

LOG = logging.getLogger("pms.security")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CALCULATIONS = {
    "/v1/module-a/evaluate",
    "/v1/risk/position-size",
    "/v1/module-e/regime",
}
MAX_BODY = 1024 * 1024
BODY_TIMEOUT = 15


def authorized(headers):
    configured = os.getenv("OPS_API_KEY", "")
    supplied = headers.get("x-ops-key", "")
    return bool(
        configured
        and supplied
        and len(configured) <= 256
        and len(supplied) <= 256
        and secrets.compare_digest(configured.encode(), supplied.encode())
    )


class APISecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = str(uuid4())
        started = False
        headers = {
            k.decode("latin1").lower(): v.decode("latin1")
            for k, v in scope.get("headers", [])
        }

        async def secure_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                updated = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower()
                    not in {
                        b"cache-control",
                        b"x-request-id",
                        b"x-content-type-options",
                        b"x-frame-options",
                        b"referrer-policy",
                    }
                ]
                updated += [
                    (b"cache-control", b"no-store"),
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                message = {**message, "headers": updated}
            await send(message)

        async def reject(status, detail):
            await JSONResponse(
                {"detail": detail, "request_id": request_id}, status_code=status
            )(scope, receive, secure_send)

        method = scope["method"].upper()
        if method not in SAFE_METHODS:
            registered = any(
                route.matches(scope)[0] == Match.FULL for route in scope["app"].routes
            )
            requires_key = scope["path"].startswith("/v1/execution") or registered
            if (
                requires_key
                and not (method == "POST" and scope["path"] in CALCULATIONS)
                and not authorized(headers)
            ):
                return await reject(401, "operator authentication required")
            try:
                content_length = int(headers.get("content-length", "0"))
                if content_length < 0:
                    raise ValueError()
            except ValueError:
                return await reject(400, "Invalid request length")
            if content_length > MAX_BODY:
                return await reject(413, "Request body too large")
            body = bytearray()

            async def collect():
                while True:
                    item = await receive()
                    if item["type"] == "http.disconnect":
                        return False
                    chunk = item.get("body", b"")
                    if len(body) + len(chunk) > MAX_BODY:
                        return None
                    body.extend(chunk)
                    if not item.get("more_body", False):
                        return True

            try:
                ok = await asyncio.wait_for(collect(), timeout=BODY_TIMEOUT)
            except asyncio.TimeoutError:
                return await reject(408, "Request body timed out")
            if ok is None:
                return await reject(413, "Request body too large")
            if not ok:
                return
            consumed = False
            original_receive = receive

            async def buffered_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": False,
                    }
                return await original_receive()

            downstream = buffered_receive
        else:
            downstream = receive
        try:
            await self.app(scope, downstream, secure_send)
        except Exception as exc:
            # No exception message, SQL, headers or payload are logged or returned.
            LOG.error(
                "request_failed request_id=%s error_type=%s",
                request_id,
                type(exc).__name__,
            )
            if not started:
                from sqlalchemy.exc import (
                    OperationalError,
                    InterfaceError,
                    TimeoutError as DBTimeout,
                )

                await reject(
                    (
                        503
                        if isinstance(
                            exc, (OperationalError, InterfaceError, DBTimeout)
                        )
                        else 500
                    ),
                    "Service temporarily unavailable; review state before retrying",
                )
            else:
                # No streaming success body is fabricated after a partial response.
                raise RuntimeError(
                    "Response interrupted; request_id=" + request_id
                ) from None


async def safe_validation_error(request, exc):
    # Pydantic errors normally echo input and exception context; omit both.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {k: item[k] for k in ("type", "loc", "msg") if k in item}
                for item in exc.errors()
            ]
        },
    )


async def safe_http_error(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": (
                "Service temporarily unavailable"
                if exc.status_code >= 500
                else exc.detail
            )
        },
        headers=exc.headers,
    )
