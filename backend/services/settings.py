from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from ..atomic import write_text_atomic
from ..config import settings_path

log = logging.getLogger("bitssh.settings")


class Settings(BaseModel):
    last_workspace: str | None = None
    recent: list[str] = Field(default_factory=list)
    language: str | None = None
    renderer: str = "dom"


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or settings_path()
        self._data = self._load()

    def _load(self) -> Settings:
        try:
            return Settings.model_validate(json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return Settings()
        except Exception as exc:
            log.warning("settings unreadable, using defaults: %s", exc)
            return Settings()

    def get(self) -> Settings:
        return self._data

    def update(self, **changes) -> Settings:
        self._data = self._data.model_copy(update=changes)
        self.save()
        return self._data

    def save(self) -> None:
        write_text_atomic(self.path, self._data.model_dump_json(indent=2) + "\n")

    def remember(self, path: Path) -> None:
        resolved = str(path)
        recent = [p for p in self._data.recent if p != resolved]
        recent.insert(0, resolved)
        recent = [p for p in recent if Path(p).exists()][:10]
        self._data = self._data.model_copy(update={"last_workspace": resolved, "recent": recent})
        self.save()
