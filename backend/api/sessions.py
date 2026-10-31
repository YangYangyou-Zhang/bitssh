from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/sessions")


class CreateBody(BaseModel):
    profile_id: str
    password: str | None = None


class HostkeyBody(BaseModel):
    decision: str  # once | always | reject


class KbdintBody(BaseModel):
    responses: list[str]


def _manager(request: Request):
    return request.app.state.sessions


def _snapshot(session) -> dict:
    return {
        "session_id": session.id,
        "state": session.state,
        "error": session.error,
        "exit_status": session.exit_status,
        "hostkey": session.pending_hostkey,
        "kbdint": session.pending_kbdint,
    }


@router.post("")
async def create_session(body: CreateBody, request: Request) -> dict:
    store = request.app.state.store
    profile = store.data.find_profile(body.profile_id)
    if profile is None:
        raise HTTPException(404, "PROFILE_NOT_FOUND")
    session = _manager(request).create(profile, body.password)
    return _snapshot(session)


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request) -> dict:
    session = _manager(request).get(session_id)
    if session is None:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    return _snapshot(session)


@router.post("/{session_id}/hostkey")
async def hostkey_decision(session_id: str, body: HostkeyBody, request: Request) -> dict:
    session = _manager(request).get(session_id)
    if session is None:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    if body.decision not in ("once", "always", "reject"):
        raise HTTPException(400, "INVALID_DECISION")
    if not session.resolve_hostkey(body.decision):
        raise HTTPException(409, "NO_PENDING_HOSTKEY")
    return {"ok": True}


@router.post("/{session_id}/kbdint")
async def kbdint_answer(session_id: str, body: KbdintBody, request: Request) -> dict:
    session = _manager(request).get(session_id)
    if session is None:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    if not session.resolve_kbdint(body.responses):
        raise HTTPException(409, "NO_PENDING_KBDINT")
    return {"ok": True}


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request) -> dict:
    if not await _manager(request).close(session_id):
        raise HTTPException(404, "SESSION_NOT_FOUND")
    request.app.state.forwards.stop_session(session_id)
    return {"ok": True}
