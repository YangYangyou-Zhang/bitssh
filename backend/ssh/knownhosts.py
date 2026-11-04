from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone

from asyncssh import SSHKey

from ..models import KnownHostRecord


def fingerprint(record: KnownHostRecord) -> str | None:
    """SHA256 fingerprint of the stored key blob, matching asyncssh's format."""
    try:
        blob = base64.b64decode(record.key_b64.encode("ascii"), validate=True)
    except Exception:
        return None
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def host_field(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


def _blob_b64(key: SSHKey) -> str:
    """known_hosts wants base64 of the wire blob, not the whole public key line.

    ``export_public_key()`` returns ``b"ssh-ed25519 AAAA..."`` (already textual),
    so the second field is exactly the base64 blob.
    """
    return key.export_public_key().split()[1].decode("ascii")


def render_known_hosts(records: list[KnownHostRecord]) -> str:
    lines = [f"{host_field(r.host, r.port)} {r.key_type} {r.key_b64}" for r in records]
    return ("\n".join(lines) + "\n") if lines else ""


def key_line(host: str, port: int, key: SSHKey) -> str:
    return f"{host_field(host, port)} {key.get_algorithm()} {_blob_b64(key)}"


def record_for_key(host: str, port: int, key: SSHKey) -> KnownHostRecord:
    return KnownHostRecord(
        host=host,
        port=port,
        key_type=key.get_algorithm(),
        key_b64=_blob_b64(key),
        added_at=datetime.now(timezone.utc).isoformat(),
    )


def find_record(records: list[KnownHostRecord], host: str, port: int) -> KnownHostRecord | None:
    return next((r for r in records if r.host == host and r.port == port), None)
