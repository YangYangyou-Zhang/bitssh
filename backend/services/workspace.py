from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from ..atomic import write_text_atomic
from ..config import default_workspace_path
from ..models import WORKSPACE_FORMAT, WORKSPACE_VERSION, Workspace
from .settings import SettingsStore

log = logging.getLogger("bitssh.workspace")


class WorkspaceError(Exception):
    pass


class ConflictError(WorkspaceError):
    pass


class VersionError(WorkspaceError):
    pass


class WorkspaceStore:
    """The open workspace file is the single source of truth for profiles.

    Secrets never live here; profiles only carry credential ids resolved
    through the vault.
    """

    def __init__(self, settings: SettingsStore) -> None:
        self.settings = settings
        self.path: Path = default_workspace_path()
        self.data = Workspace()
        self.dirty = False
        self.conflict = False
        self._hash = ""
        self._lock = asyncio.Lock()

    # ---------- loading ----------

    @staticmethod
    def parse(raw: bytes) -> tuple[Workspace, str]:
        obj = json.loads(raw.decode("utf-8"))
        if not isinstance(obj, dict) or obj.get("format") != WORKSPACE_FORMAT:
            raise WorkspaceError("IMPORT_INVALID_FORMAT")
        if int(obj.get("version", 0)) > WORKSPACE_VERSION:
            raise VersionError("IMPORT_VERSION_TOO_NEW")
        return Workspace.model_validate(obj), hashlib.sha256(raw).hexdigest()

    def load_initial(self) -> None:
        last = self.settings.get().last_workspace
        if last:
            try:
                self.open(Path(last), remember=False)
                log.info("restored workspace %s", last)
                return
            except Exception as exc:
                log.warning("cannot open last workspace %s: %s", last, exc)
        try:
            self.open(default_workspace_path(), remember=False)
        except FileNotFoundError:
            self.path = default_workspace_path()
            self.data = Workspace()
            self._hash = ""
            log.info("starting with a new workspace at %s", self.path)

    def open(self, path: Path, remember: bool = True) -> None:
        path = path.expanduser()
        if path.exists():
            data, digest = self.parse(path.read_bytes())
        else:
            data, digest = Workspace(), ""
        self.path = path
        self.data = data
        self._hash = digest
        self.dirty = False
        self.conflict = False
        if remember:
            self.settings.remember(path)

    def reload(self) -> None:
        if not self.path.exists():
            raise WorkspaceError("WORKSPACE_MISSING")
        self.open(self.path, remember=False)

    # ---------- saving ----------

    def serialize(self) -> str:
        payload = self.data.model_dump(mode="json")
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    def check_conflict(self) -> bool:
        if not self.path.exists():
            return False
        current = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if self._hash and current != self._hash:
            self.conflict = True
        return self.conflict

    def touch(self) -> None:
        self.dirty = True

    async def save(self, force: bool = False) -> None:
        async with self._lock:
            if not force and self.check_conflict():
                raise ConflictError("WORKSPACE_CONFLICT")
            text = self.serialize()
            await asyncio.to_thread(write_text_atomic, self.path, text)
            self._hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            self.dirty = False
            self.conflict = False
            self.settings.remember(self.path)
            log.info("workspace saved (%d profiles)", len(self.data.profiles))

    async def autosave_loop(self) -> None:
        while True:
            await asyncio.sleep(0.8)
            if self.dirty and not self.conflict:
                try:
                    await self.save()
                except ConflictError:
                    log.warning("workspace changed on disk; autosave paused")
                except Exception:
                    log.exception("autosave failed")
