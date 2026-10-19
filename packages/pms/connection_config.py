"""Credential-free connection policy shared by deployment checks and runtime."""

from urllib.parse import unquote


def connection_failure_code(error):
    """Return an allowlisted diagnostic; never expose driver text or credentials."""
    cause = getattr(error, "orig", error)
    message = str(cause).lower()
    if "root certificate file" in message or "sslrootcert" in message:
        return "DATABASE_CA_CONFIGURATION"
    if "certificate verify failed" in message or "certificate verification failed" in message:
        return "DATABASE_CERTIFICATE_VERIFICATION"
    if "does not match host name" in message or "hostname mismatch" in message:
        return "DATABASE_CERTIFICATE_HOSTNAME"
    if "password authentication failed" in message:
        return "DATABASE_AUTHENTICATION"
    if "could not translate host name" in message or "name resolution" in message:
        return "DATABASE_DNS"
    if "timeout expired" in message or "connection timed out" in message:
        return "DATABASE_CONNECT_TIMEOUT"
    if "connection refused" in message:
        return "DATABASE_CONNECTION_REFUSED"
    return "DATABASE_CONNECTION_FAILED"


def placeholder_password(password):
    return unquote(password or "").upper() in {
        "[YOUR-PASSWORD]", "[YOUR_PASSWORD]", "YOUR_DATABASE_PASSWORD",
        "YOUR-PASSWORD", "YOUR_PASSWORD",
    }


def tls_connect_options(query, env):
    mode = query.get("sslmode", env.get("PGSSLMODE", "require"))
    if mode not in {"require", "verify-ca", "verify-full"}:
        raise ValueError("Remote database connections require TLS")
    if env.get("PMS_DATABASE_VERIFY_FULL") == "1":
        # Deployment policy may strengthen URL settings, never weaken them.
        return {
            "sslmode": "verify-full",
            "sslrootcert": query.get("sslrootcert")
            or env.get("PGSSLROOTCERT") or "system",
        }
    return {"sslmode": mode}
