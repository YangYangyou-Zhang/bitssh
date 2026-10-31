from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..serial.session import SerialSession

router = APIRouter(prefix="/api/serial")


class CreateBody(BaseModel):
    profile_id: str


class LineBody(BaseModel):
    dtr: bool | None = None
    rts: bool | None = None
    send_break: bool | None = None


@router.get("/ports")
async def list_ports() -> list[dict]:
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    ports = []
    for item in lp.comports():
        ports.append(
            {
                "device": item.device,
                "description": item.description,
                "hwid": item.hwid,
                "vid": item.vid,
                "pid": item.pid,
                "serial": item.serial_number,
            }
        )
    return ports


@router.post("/sessions")
async def create_serial_session(body: CreateBody, request: Request) -> dict:
    store = request.app.state.store
    profile = store.data.find_profile(body.profile_id)
    if profile is None:
        raise HTTPException(404, "PROFILE_NOT_FOUND")
    if profile.protocol != "serial":
        raise HTTPException(400, "UNSUPPORTED_PROTOCOL")
    session = SerialSession(profile)
    request.app.state.sessions.register(session)
    session.start()
    return {"session_id": session.id, "state": session.state}


@router.post("/{session_id}/line")
async def set_line(session_id: str, body: LineBody, request: Request) -> dict:
    session = request.app.state.sessions.get(session_id)
    if session is None or not hasattr(session, "set_line"):
        raise HTTPException(404, "SESSION_NOT_FOUND")
    session.set_line(body.dtr, body.rts)
    if body.send_break:
        session.send_break()
    return {"ok": True}
