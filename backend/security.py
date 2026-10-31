from __future__ import annotations

import base64
import hmac
import secrets


def new_token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def token_matches(expected: str, supplied: str | None) -> bool:
    if not supplied:
        return False
    return hmac.compare_digest(expected, supplied)


def local_origin_ok(origin: str | None, host: str | None) -> bool:
    """Allow loopback origins and a missing Origin header (same-origin GETs omit it)."""
    if host and not _is_local_host(host):
        return False
    if origin is None or origin == "":
        return True
    return origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:")


def _is_local_host(host: str) -> bool:
    name = host.rsplit(":", 1)[0].strip("[]").lower()
    return name in {"127.0.0.1", "localhost", "::1"}
