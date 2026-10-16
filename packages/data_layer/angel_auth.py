from __future__ import annotations

import os
from dataclasses import dataclass

import pyotp
from SmartApi import SmartConnect


@dataclass(frozen=True)
class AngelCredentials:
    api_key: str
    client_id: str
    pin: str
    totp_secret: str

    @classmethod
    def from_env(cls) -> "AngelCredentials":
        values = {
            "api_key": os.getenv("ANGEL_API_KEY", ""),
            "client_id": os.getenv("ANGEL_CLIENT_ID", ""),
            "pin": os.getenv("ANGEL_PASSWORD", ""),
            "totp_secret": os.getenv("ANGEL_TOTP_SECRET", ""),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise RuntimeError(f"Missing Angel One environment variables: {', '.join(missing)}")
        return cls(**values)


@dataclass(frozen=True)
class AngelSessionTokens:
    jwt_token: str
    refresh_token: str
    feed_token: str


def create_angel_session(
    credentials: AngelCredentials | None = None,
) -> tuple[SmartConnect, AngelSessionTokens]:
    """Create an authenticated SmartAPI session.

    Angel sessions expire at midnight, so deployment workers should create a
    fresh session each trading day and never persist these tokens in GitHub.
    """

    credentials = credentials or AngelCredentials.from_env()
    client = SmartConnect(api_key=credentials.api_key)
    totp = pyotp.TOTP(credentials.totp_secret).now()
    result = client.generateSession(credentials.client_id, credentials.pin, totp)
    if not result or result.get("status") is not True:
        message = result.get("message") if result else "empty response"
        code = result.get("errorcode") if result else ""
        raise RuntimeError(f"Angel One authentication failed: {code} {message}".strip())

    data = result.get("data") or {}
    refresh = data.get("refreshToken")
    jwt = data.get("jwtToken")
    feed = client.getfeedToken()
    if not jwt or not refresh or not feed:
        raise RuntimeError("Angel One authentication succeeded but tokens are incomplete")

    return client, AngelSessionTokens(
        jwt_token=str(jwt),
        refresh_token=str(refresh),
        feed_token=str(feed),
    )
