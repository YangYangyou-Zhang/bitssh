from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/system")


class OpenPathBody(BaseModel):
    path: str


@router.post("/open-path")
async def open_path(body: OpenPathBody) -> dict:
    target = Path(body.path).expanduser()
    if not target.exists():
        raise HTTPException(404, "PATH_NOT_FOUND")
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as exc:
        raise HTTPException(400, f"OPEN_FAILED: {exc}") from exc
    return {"ok": True}
