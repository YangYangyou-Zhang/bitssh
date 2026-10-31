from __future__ import annotations

import posixpath
import time
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..security import token_matches
from ..ssh.sftp_service import sftp_client

router = APIRouter(prefix="/api/download")


async def _stream_remote(request: Request, session_id: str, path: str) -> StreamingResponse:
    session = request.app.state.sessions.get(session_id)
    if session is None or getattr(session, "conn", None) is None:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    sftp = await sftp_client(session.conn)
    handle = await sftp.open(path, "rb")
    name = posixpath.basename(path)

    async def stream():
        try:
            while True:
                data = await handle.read(262144)
                if not data:
                    break
                yield data
        finally:
            await handle.close()

    disposition = f"attachment; filename*=UTF-8''{quote(name)}"
    return StreamingResponse(
        stream(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


@router.get("/{ticket}")
async def fetch_by_ticket(ticket: str, request: Request) -> StreamingResponse:
    """Serve a file to the browser using a short-lived ticket."""
    record = request.app.state.download_tickets.get(ticket)
    if record is None or record["expires"] < time.time():
        request.app.state.download_tickets.pop(ticket, None)
        raise HTTPException(404, "TICKET_INVALID")
    return await _stream_remote(request, record["session_id"], record["path"])


@router.get("/direct/{session_id}")
async def fetch_direct(
    session_id: str, request: Request, path: str, token: str = ""
) -> StreamingResponse:
    """Drag-out download URL. Browsers cannot attach headers to a drag, so the
    token travels in the query string; loopback-only and access logs are off."""
    if not token_matches(request.app.state.token, token):
        raise HTTPException(401, "UNAUTHORIZED")
    return await _stream_remote(request, session_id, path)
