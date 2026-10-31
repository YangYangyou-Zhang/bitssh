from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..security import token_matches

log = logging.getLogger("bitssh.ws")
router = APIRouter()

SUBPROTOCOL = "bitssh.v1"


def _supplied_token(ws: WebSocket) -> str | None:
    offered = ws.scope.get("subprotocols") or []
    if len(offered) >= 2 and offered[0] == SUBPROTOCOL:
        return offered[1]
    return offered[0] if offered else None


@router.websocket("/ws/terminal/{session_id}")
async def terminal_ws(ws: WebSocket, session_id: str) -> None:
    expected = ws.app.state.token
    if not token_matches(expected, _supplied_token(ws)):
        await ws.accept()
        await ws.close(4401)
        return

    session = ws.app.state.sessions.get(session_id)
    if session is None:
        await ws.accept()
        await ws.close(4404)
        return

    await ws.accept(subprotocol=SUBPROTOCOL)
    await _pump(ws, session)


async def _pump(ws: WebSocket, session) -> None:
    # Replay the current state so a late-attaching client is never blind.
    await ws.send_text(
        json.dumps(
            {
                "type": "state",
                "state": session.state,
                "hostkey": session.pending_hostkey,
                "kbdint": session.pending_kbdint,
            }
        )
    )
    recent = getattr(session, "recent", b"")
    if recent:
        await ws.send_bytes(bytes(recent))

    send_task = asyncio.create_task(_send_loop(ws, session))
    recv_task = asyncio.create_task(_recv_loop(ws, session))
    done, pending = await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        try:
            task.result()
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:
            log.debug("ws pump ended with error", exc_info=True)


async def _send_loop(ws: WebSocket, session) -> None:
    while True:
        item = await session.events.get()
        if isinstance(item, (bytes, bytearray)):
            await ws.send_bytes(bytes(item))
        else:
            await ws.send_text(json.dumps(item))


async def _recv_loop(ws: WebSocket, session) -> None:
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return
        data = message.get("bytes")
        if data is not None:
            session.write(data)
            continue
        text = message.get("text")
        if not text:
            continue
        try:
            control = json.loads(text)
        except json.JSONDecodeError:
            continue
        kind = control.get("type")
        if kind == "ack":
            session.ack(int(control.get("bytes", 0)))
        elif kind == "resize":
            session.resize(int(control.get("cols", 0)), int(control.get("rows", 0)))
        elif kind == "pause":
            session.set_paused(True)
        elif kind == "resume":
            session.set_paused(False)
        elif kind == "hostkey":
            session.resolve_hostkey(str(control.get("decision", "reject")))
        elif kind == "kbdint":
            responses = control.get("responses") or []
            session.resolve_kbdint([str(r) for r in responses])
        elif kind == "serial.line":
            setter = getattr(session, "set_line", None)
            if setter is not None:
                dtr = control.get("dtr")
                rts = control.get("rts")
                setter(dtr if isinstance(dtr, bool) else None,
                       rts if isinstance(rts, bool) else None)
        elif kind == "serial.break":
            breaker = getattr(session, "send_break", None)
            if breaker is not None:
                breaker()
