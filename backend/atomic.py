from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path


def _replace_with_retry(src: str, dst: Path) -> None:
    delay = 0.05
    for attempt in range(5):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == 4:
                raise
            time.sleep(delay)
            delay *= 2


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write via a sibling temp file so a crash never leaves a half file.

    The temp file must stay in the destination directory: os.replace across
    filesystems is not atomic, and shutil.move can fall back to copy+unlink.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        _replace_with_retry(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def write_text_atomic(path: Path, text: str) -> None:
    write_bytes_atomic(path, text.encode("utf-8"))
