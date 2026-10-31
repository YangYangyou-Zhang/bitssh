from __future__ import annotations

import posixpath
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import default_download_dir
from ..ssh import sftp_service
from ..ssh.sftp_service import norm_remote

router = APIRouter(prefix="/api/sftp")


class OpsBody(BaseModel):
    op: str
    path: str
    new_path: str | None = None
    mode: int | None = None
    is_dir: bool = False


class DownloadBody(BaseModel):
    path: str
    local_path: str | None = None
    overwrite: bool = False


class UploadBody(BaseModel):
    local_path: str
    path: str


class UploadLocalBody(BaseModel):
    local_path: str
    remote_dir: str


class DownloadTreeBody(BaseModel):
    path: str
    local_dir: str
    overwrite: bool = False


def _session(request: Request, session_id: str):
    session = request.app.state.sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    if session.conn is None:
        raise HTTPException(409, "SESSION_NOT_READY")
    return session


@router.get("/{session_id}/list")
async def list_remote(session_id: str, request: Request, path: str = "/") -> dict:
    session = _session(request, session_id)
    try:
        entries = await sftp_service.list_dir(session.conn, path)
    except Exception as exc:
        raise HTTPException(400, f"SFTP_ERROR: {exc}") from exc
    return {"path": norm_remote(path), "entries": entries}


@router.get("/{session_id}/home")
async def remote_home(session_id: str, request: Request) -> dict:
    session = _session(request, session_id)
    try:
        return {"path": await sftp_service.home_dir(session.conn)}
    except Exception as exc:
        raise HTTPException(400, f"SFTP_ERROR: {exc}") from exc


@router.get("/{session_id}/stat")
async def stat_remote(session_id: str, request: Request, path: str) -> dict:
    session = _session(request, session_id)
    try:
        return await sftp_service.stat_path(session.conn, path)
    except Exception as exc:
        raise HTTPException(400, f"SFTP_ERROR: {exc}") from exc


@router.post("/{session_id}/ops")
async def remote_ops(session_id: str, body: OpsBody, request: Request) -> dict:
    session = _session(request, session_id)
    try:
        if body.op == "mkdir":
            await sftp_service.mkdir(session.conn, body.path)
        elif body.op == "remove":
            await sftp_service.remove(session.conn, body.path, body.is_dir)
        elif body.op == "rename":
            if not body.new_path:
                raise HTTPException(400, "MISSING_NEW_PATH")
            await sftp_service.rename(session.conn, body.path, body.new_path)
        elif body.op == "chmod":
            if body.mode is None:
                raise HTTPException(400, "MISSING_MODE")
            await sftp_service.chmod(session.conn, body.path, body.mode)
        else:
            raise HTTPException(400, "UNKNOWN_OP")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"SFTP_ERROR: {exc}") from exc
    return {"ok": True}


@router.post("/{session_id}/download")
async def download(session_id: str, body: DownloadBody, request: Request) -> dict:
    session = _session(request, session_id)
    if body.local_path:
        local = Path(body.local_path).expanduser()
    else:
        local = default_download_dir() / posixpath.basename(norm_remote(body.path))
    local.parent.mkdir(parents=True, exist_ok=True)
    if local.exists() and not body.overwrite:
        raise HTTPException(409, "LOCAL_FILE_EXISTS")
    manager = request.app.state.transfers

    async def job(progress) -> None:
        await sftp_service.download(session.conn, body.path, str(local), progress)

    transfer_id = manager.start("download", local.name, job, target=str(local))
    return {"id": transfer_id, "local_path": str(local)}


@router.put("/{session_id}/upload-stream")
async def upload_stream(session_id: str, request: Request, path: str) -> dict:
    """Consume a raw request body and write it straight to the remote file.

    Used by drag & drop, where the browser has the bytes but no local path.
    """
    session = _session(request, session_id)
    target = norm_remote(path)
    manager = request.app.state.transfers
    transfer_id = uuid.uuid4().hex
    item = {
        "id": transfer_id,
        "kind": "upload",
        "label": posixpath.basename(target),
        "target": target,
        "status": "running",
        "copied": 0,
        "total": 0,
        "error": None,
    }
    try:
        item["total"] = int(request.headers.get("x-file-size", "0"))
    except ValueError:
        item["total"] = 0
    manager.items[transfer_id] = item
    try:
        sftp = await sftp_service.sftp_client(session.conn)
        handle = await sftp.open(target, "wb")
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                await handle.write(chunk)
                item["copied"] += len(chunk)
        finally:
            await handle.close()
    except Exception as exc:
        item["status"] = "failed"
        item["error"] = str(exc)
        raise HTTPException(400, "TRANSFER_FAILED") from exc
    item["status"] = "done"
    item["total"] = item["copied"]
    return {"id": transfer_id, "bytes": item["copied"]}


@router.post("/{session_id}/download-ticket")
async def download_ticket(session_id: str, body: DownloadBody, request: Request) -> dict:
    session = _session(request, session_id)
    ticket = uuid.uuid4().hex
    request.app.state.download_tickets[ticket] = {
        "session_id": session.id,
        "path": norm_remote(body.path),
        "expires": time.time() + 300,
    }
    return {"ticket": ticket, "url": f"/api/download/{ticket}"}


@router.post("/{session_id}/download-tree")
async def download_tree(session_id: str, body: DownloadTreeBody, request: Request) -> dict:
    session = _session(request, session_id)
    local = Path(body.local_dir).expanduser()
    if local.exists() and any(local.iterdir()) and not body.overwrite:
        raise HTTPException(409, "LOCAL_FILE_EXISTS")
    local.mkdir(parents=True, exist_ok=True)
    manager = request.app.state.transfers

    async def job(progress) -> None:
        await sftp_service.download_tree(session.conn, body.path, local, progress)

    transfer_id = manager.start("download", local.name, job, target=str(local))
    return {"id": transfer_id, "local_path": str(local)}


@router.post("/{session_id}/upload-local")
async def upload_local(session_id: str, body: UploadLocalBody, request: Request) -> dict:
    """Upload a local file *or directory tree* (the browser cannot read dirs)."""
    session = _session(request, session_id)
    local = Path(body.local_path).expanduser()
    if not local.exists():
        raise HTTPException(400, "LOCAL_FILE_MISSING")
    manager = request.app.state.transfers

    async def job(progress) -> None:
        await sftp_service.upload_tree(session.conn, local, body.remote_dir, progress)

    transfer_id = manager.start("upload", local.name, job, target=norm_remote(body.remote_dir))
    return {"id": transfer_id}


@router.post("/{session_id}/upload")
async def upload(session_id: str, body: UploadBody, request: Request) -> dict:
    session = _session(request, session_id)
    local = Path(body.local_path).expanduser()
    if not local.is_file():
        raise HTTPException(400, "LOCAL_FILE_MISSING")
    manager = request.app.state.transfers

    async def job(progress) -> None:
        await sftp_service.upload(session.conn, str(local), body.path, progress)

    transfer_id = manager.start("upload", local.name, job, target=norm_remote(body.path))
    return {"id": transfer_id}
