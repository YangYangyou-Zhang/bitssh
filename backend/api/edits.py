from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api")


class EditBody(BaseModel):
    path: str


class ResolveBody(BaseModel):
    action: str  # overwrite | reload


@router.post("/sftp/{session_id}/edit")
async def start_edit(session_id: str, body: EditBody, request: Request) -> dict:
    session = request.app.state.sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    if getattr(session, "conn", None) is None:
        raise HTTPException(409, "SESSION_NOT_READY")
    try:
        return await request.app.state.edits.start(session, body.path)
    except Exception as exc:
        raise HTTPException(400, f"SFTP_ERROR: {exc}") from exc


@router.get("/edits")
async def list_edits(request: Request) -> list[dict]:
    return request.app.state.edits.list()


@router.post("/edits/{edit_id}/upload")
async def upload_edit(edit_id: str, request: Request) -> dict:
    edit = request.app.state.edits.items.get(edit_id)
    if edit is None:
        raise HTTPException(404, "EDIT_NOT_FOUND")
    session = request.app.state.sessions.get(edit["session_id"])
    if session is None:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    if not await request.app.state.edits.upload_now(edit_id, session):
        raise HTTPException(400, "TRANSFER_FAILED")
    return {"ok": True}


@router.post("/edits/{edit_id}/resolve")
async def resolve_edit(edit_id: str, body: ResolveBody, request: Request) -> dict:
    edit = request.app.state.edits.items.get(edit_id)
    if edit is None:
        raise HTTPException(404, "EDIT_NOT_FOUND")
    if body.action not in ("overwrite", "reload"):
        raise HTTPException(400, "INVALID_DECISION")
    session = request.app.state.sessions.get(edit["session_id"])
    if session is None:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    if not await request.app.state.edits.resolve(edit_id, body.action, session):
        raise HTTPException(400, "TRANSFER_FAILED")
    return {"ok": True}


@router.delete("/edits/{edit_id}")
async def stop_edit(edit_id: str, request: Request) -> dict:
    if not request.app.state.edits.stop(edit_id):
        raise HTTPException(404, "EDIT_NOT_FOUND")
    return {"ok": True}
