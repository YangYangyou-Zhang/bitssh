from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..ssh.forwarding import ForwardError

router = APIRouter(prefix="/api/forwards")


class ForwardBody(BaseModel):
    session_id: str
    type: str
    bind_host: str = "127.0.0.1"
    bind_port: int = 0
    dest_host: str = ""
    dest_port: int = 0


@router.get("")
async def list_forwards(request: Request) -> list[dict]:
    return request.app.state.forwards.list()


@router.post("")
async def create_forward(body: ForwardBody, request: Request) -> dict:
    session = request.app.state.sessions.get(body.session_id)
    if session is None:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    try:
        return await request.app.state.forwards.start(
            session,
            body.type,
            body.bind_host,
            body.bind_port,
            body.dest_host,
            body.dest_port,
        )
    except ForwardError as exc:
        raise HTTPException(400, exc.code) from exc


@router.delete("/{forward_id}")
async def delete_forward(forward_id: str, request: Request) -> dict:
    if not request.app.state.forwards.stop(forward_id):
        raise HTTPException(404, "FORWARD_NOT_FOUND")
    return {"ok": True}
