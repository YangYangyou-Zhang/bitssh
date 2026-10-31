from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/local")


class LocalOps(BaseModel):
    op: str
    path: str
    new_path: str | None = None


def _resolve(raw: str) -> Path:
    path = Path(raw or "~").expanduser()
    if not path.is_absolute():
        path = Path.home() / path
    return path.resolve()


@router.get("/list")
async def list_local(path: str = "~") -> dict:
    base = _resolve(path)
    if not base.is_dir():
        raise HTTPException(400, "NOT_A_DIRECTORY")
    entries: list[dict] = []
    try:
        children = list(base.iterdir())
    except OSError as exc:
        raise HTTPException(400, f"UNREADABLE: {exc}") from exc
    for child in sorted(children, key=lambda c: (not c.is_dir(), c.name.lower())):
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "dir": child.is_dir(),
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
            }
        )
    return {
        "path": str(base),
        "parent": str(base.parent) if base.parent != base else None,
        "entries": entries,
    }


@router.post("/ops")
async def local_ops(body: LocalOps) -> dict:
    target = _resolve(body.path)
    try:
        if body.op == "mkdir":
            target.mkdir(parents=False, exist_ok=False)
        elif body.op == "remove":
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        elif body.op == "rename":
            if not body.new_path:
                raise HTTPException(400, "MISSING_NEW_PATH")
            target.rename(_resolve(body.new_path))
        else:
            raise HTTPException(400, "UNKNOWN_OP")
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(400, f"LOCAL_FS_ERROR: {exc}") from exc
    return {"ok": True}
