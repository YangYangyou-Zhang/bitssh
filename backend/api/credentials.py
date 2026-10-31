from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..services.vault import VaultLocked

router = APIRouter(prefix="/api/credentials")


class SecretBody(BaseModel):
    credential_id: str
    secret: str


def _vault(request: Request):
    return request.app.state.vault


@router.put("")
async def put_secret(body: SecretBody, request: Request) -> dict:
    vault = _vault(request)
    try:
        vault.set(body.credential_id, body.secret.encode("utf-8"))
    except VaultLocked as exc:
        raise HTTPException(423, "VAULT_LOCKED") from exc
    return {"ok": True}


@router.get("/{credential_id}")
async def has_secret(credential_id: str, request: Request) -> dict:
    vault = _vault(request)
    try:
        return {"exists": vault.get(credential_id) is not None}
    except VaultLocked:
        return {"exists": False, "locked": True}


@router.delete("/{credential_id}")
async def delete_secret(credential_id: str, request: Request) -> dict:
    _vault(request).delete(credential_id)
    return {"ok": True}
