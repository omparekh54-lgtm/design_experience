"""Credential-free connection policy shared by deployment checks and runtime."""

from urllib.parse import unquote


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
