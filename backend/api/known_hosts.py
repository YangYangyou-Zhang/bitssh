from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ..models import KnownHostRecord

router = APIRouter(prefix="/api/known-hosts")


def _items(records: list[KnownHostRecord]) -> list[dict]:
    from ..ssh.knownhosts import fingerprint

    return [
        {
            "host": record.host,
            "port": record.port,
            "key_type": record.key_type,
            "fingerprint": fingerprint(record),
            "added_at": record.added_at,
        }
        for record in records
    ]


@router.get("")
async def list_known_hosts(request: Request) -> dict:
    store = request.app.state.store
    return {
        "path": str(store.path),
        "known_hosts": _items(store.data.known_hosts),
    }


@router.delete("")
async def delete_known_host(
    request: Request,
    host: str = Query(...),
    port: int = Query(22),
    key_type: str | None = Query(None),
) -> dict:
    store = request.app.state.store

    def matches(record: KnownHostRecord) -> bool:
        return (
            record.host == host
            and record.port == port
            and (key_type is None or record.key_type == key_type)
        )

    before = len(store.data.known_hosts)
    store.data.known_hosts = [r for r in store.data.known_hosts if not matches(r)]
    removed = before - len(store.data.known_hosts)
    if removed == 0:
        raise HTTPException(404, "KNOWN_HOST_NOT_FOUND")
    store.touch()
    return {
        "ok": True,
        "removed": removed,
        "path": str(store.path),
        "known_hosts": _items(store.data.known_hosts),
    }
