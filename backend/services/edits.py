from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import posixpath
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..config import cache_dir
from ..ssh import sftp_service

log = logging.getLogger("bitssh.edits")


def open_with_default_app(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class EditSyncManager:
    """Download a remote file, open it locally, and push saves back.

    Status is polled by the frontend: downloading -> ready -> uploading -> synced
    (or error). This is the "where do I see whether it worked" surface.
    """

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    async def _remote_stamp(session, remote: str):  # noqa: ANN001, ANN205
        try:
            info = await sftp_service.stat_path(session.conn, remote)
            return (int(info["mtime"]), int(info["size"]))
        except Exception:
            return None

    def list(self) -> list[dict]:
        return sorted(self.items.values(), key=lambda item: item["updated_at"], reverse=True)

    def _touch(self, item: dict, status: str, error: str | None = None) -> None:
        item["status"] = status
        item["error"] = error
        item["updated_at"] = datetime.now(timezone.utc).isoformat()

    async def start(self, session, remote_path: str) -> dict:
        remote = sftp_service.norm_remote(remote_path)
        local_dir = cache_dir() / "edits" / session.id[:8]
        local_dir.mkdir(parents=True, exist_ok=True)
        local = local_dir / posixpath.basename(remote)

        edit = {
            "id": uuid.uuid4().hex,
            "session_id": session.id,
            "remote_path": remote,
            "local_path": str(local),
            "status": "downloading",
            "error": None,
            "remote_mtime": None,
            "remote_size": None,
            "last_hash": "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.items[edit["id"]] = edit
        self._locks[edit["id"]] = asyncio.Lock()

        await sftp_service.download(session.conn, remote, str(local), None)
        stamp = await self._remote_stamp(session, remote)
        if stamp is not None:
            edit["remote_mtime"], edit["remote_size"] = stamp
        edit["last_hash"] = _hash(local) if local.exists() else ""
        self._touch(edit, "ready")
        if not os.environ.get("BITSSH_NO_LAUNCH"):
            try:
                open_with_default_app(local)
            except Exception as exc:
                log.warning("cannot open %s: %s", local, exc)

        self._tasks[edit["id"]] = asyncio.create_task(self._watch(session, edit, local, remote))
        return edit

    async def _watch(self, session, edit: dict, local: Path, remote: str) -> None:
        while edit["id"] in self.items:
            await asyncio.sleep(1.0)
            if not local.exists():
                continue
            try:
                current = _hash(local)
            except OSError:
                continue
            if current == edit["last_hash"]:
                continue
            edit["last_hash"] = current
            if edit["status"] == "conflict":
                continue  # wait for the user to choose overwrite / reload
            conn = getattr(session, "conn", None)
            if conn is None:
                self._touch(edit, "error", "SESSION_CLOSED")
                continue

            stamp = await self._remote_stamp(session, remote)
            baseline = (edit["remote_mtime"], edit["remote_size"])
            if stamp is not None and baseline[0] is not None and stamp != baseline:
                self._touch(edit, "conflict")
                log.info("edit conflict detected on %s (remote %s vs %s)", remote, stamp, baseline)
                continue

            self._touch(edit, "uploading")
            try:
                async with self._locks[edit["id"]]:
                    await sftp_service.upload(conn, str(local), remote, None)
                await self._refresh_baseline(session, edit, remote)
                self._touch(edit, "synced")
                log.info("edit synced: %s -> %s", local, remote)
            except Exception as exc:
                log.warning("edit upload failed: %s", exc, exc_info=True)
                self._touch(edit, "error", str(exc))

    async def _refresh_baseline(self, session, edit: dict, remote: str) -> None:
        stamp = await self._remote_stamp(session, remote)
        if stamp is not None:
            edit["remote_mtime"], edit["remote_size"] = stamp

    async def resolve(self, edit_id: str, action: str, session) -> bool:
        edit = self.items.get(edit_id)
        if edit is None:
            return False
        local = Path(edit["local_path"])
        remote = edit["remote_path"]
        if action == "overwrite":
            if getattr(session, "conn", None) is None:
                return False
            self._touch(edit, "uploading")
            try:
                async with self._locks[edit_id]:
                    await sftp_service.upload(session.conn, str(local), remote, None)
                edit["last_hash"] = _hash(local) if local.exists() else edit["last_hash"]
                await self._refresh_baseline(session, edit, remote)
                self._touch(edit, "synced")
                return True
            except Exception as exc:
                self._touch(edit, "error", str(exc))
                return False
        if action == "reload":
            try:
                await sftp_service.download(session.conn, remote, str(local), None)
                edit["last_hash"] = _hash(local) if local.exists() else ""
                await self._refresh_baseline(session, edit, remote)
                self._touch(edit, "ready")
                return True
            except Exception as exc:
                self._touch(edit, "error", str(exc))
                return False
        return False

    async def upload_now(self, edit_id: str, session) -> bool:
        edit = self.items.get(edit_id)
        if edit is None or getattr(session, "conn", None) is None:
            return False
        self._touch(edit, "uploading")
        try:
            async with self._locks[edit_id]:
                await sftp_service.upload(session.conn, edit["local_path"], edit["remote_path"], None)
            local = Path(edit["local_path"])
            edit["last_hash"] = _hash(local) if local.exists() else edit["last_hash"]
            await self._refresh_baseline(session, edit, edit["remote_path"])
            self._touch(edit, "synced")
            return True
        except Exception as exc:
            self._touch(edit, "error", str(exc))
            return False

    def stop(self, edit_id: str) -> bool:
        task = self._tasks.pop(edit_id, None)
        if task is not None:
            task.cancel()
        self._locks.pop(edit_id, None)
        return self.items.pop(edit_id, None) is not None

    async def stop_all(self) -> None:
        for edit_id in list(self._tasks):
            self._tasks.pop(edit_id).cancel()
        self.items.clear()
