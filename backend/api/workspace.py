from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..services.workspace import ConflictError, WorkspaceError

router = APIRouter(prefix="/api/workspace")


class OpenBody(BaseModel):
    path: str


class SaveBody(BaseModel):
    path: str | None = None
    force: bool = False


def _state(request: Request) -> dict:
    store = request.app.state.store
    settings = request.app.state.settings
    return {
        "path": str(store.path),
        "dirty": store.dirty,
        "conflict": store.conflict,
        "recent": settings.get().recent,
    }


@router.get("")
async def get_workspace(request: Request) -> dict:
    return _state(request)


@router.post("/open")
async def open_workspace(body: OpenBody, request: Request) -> dict:
    store = request.app.state.store
    try:
        store.open(Path(body.path))
    except WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"WORKSPACE_UNREADABLE: {exc}") from exc
    return _state(request)


@router.post("/save")
async def save_workspace(body: SaveBody, request: Request) -> dict:
    store = request.app.state.store
    if body.path:
        store.path = Path(body.path)
    try:
        await store.save(force=body.force)
    except ConflictError as exc:
        raise HTTPException(409, "WORKSPACE_CONFLICT") from exc
    return _state(request)


@router.post("/reload")
async def reload_workspace(request: Request) -> dict:
    store = request.app.state.store
    try:
        store.reload()
    except WorkspaceError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _state(request)
