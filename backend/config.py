from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_log_dir

APP_NAME = "bitssh"


def web_root() -> Path | None:
    """Locate the built frontend bundle (source tree or frozen onedir)."""
    candidates: list[Path] = []
    env = os.environ.get("BITSSH_WEB_ROOT")
    if env:
        candidates.append(Path(env))
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.append(base / "frontend_dist")
    candidates.append(Path(__file__).resolve().parent.parent / "frontend" / "dist")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _override() -> Path | None:
    value = os.environ.get("BITSSH_CONFIG_DIR")
    return Path(value) if value else None


def config_dir() -> Path:
    base = _override()
    if base is not None:
        return _ensure(base)
    return _ensure(Path(user_config_dir(APP_NAME, appauthor=False)))


def log_dir() -> Path:
    base = _override()
    if base is not None:
        return _ensure(base / "logs")
    return _ensure(Path(user_log_dir(APP_NAME, appauthor=False)))


def cache_dir() -> Path:
    base = _override()
    if base is not None:
        return _ensure(base / "cache")
    return _ensure(Path(user_cache_dir(APP_NAME, appauthor=False)))


def default_download_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


def default_workspace_path() -> Path:
    return config_dir() / "profiles.bitssh.json"


def settings_path() -> Path:
    return config_dir() / "settings.json"
