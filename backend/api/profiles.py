from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from ..models import WORKSPACE_FORMAT, WORKSPACE_VERSION, Folder, Profile, Workspace, new_id

router = APIRouter(prefix="/api/profiles")


def _store(request: Request):
    return request.app.state.store


@router.get("")
async def list_profiles(request: Request) -> dict:
    store = _store(request)
    return {
        "workspace": store.serialize() and json.loads(store.serialize()),
        "path": str(store.path),
        "dirty": store.dirty,
        "conflict": store.conflict,
    }


@router.post("")
async def create_profile(profile: Profile, request: Request) -> Profile:
    store = _store(request)
    if store.data.find_profile(profile.id):
        profile = profile.model_copy(update={"id": new_id()})
    store.data.profiles.append(profile)
    store.touch()
    return profile


@router.put("/{profile_id}")
async def update_profile(profile_id: str, profile: Profile, request: Request) -> Profile:
    store = _store(request)
    for index, existing in enumerate(store.data.profiles):
        if existing.id == profile_id:
            store.data.profiles[index] = profile.model_copy(update={"id": profile_id})
            store.touch()
            return store.data.profiles[index]
    raise HTTPException(404, "PROFILE_NOT_FOUND")


@router.delete("/{profile_id}")
async def delete_profile(profile_id: str, request: Request) -> dict:
    store = _store(request)
    before = len(store.data.profiles)
    store.data.profiles = [p for p in store.data.profiles if p.id != profile_id]
    if len(store.data.profiles) == before:
        raise HTTPException(404, "PROFILE_NOT_FOUND")
    store.touch()
    return {"ok": True}


@router.post("/{profile_id}/duplicate")
async def duplicate_profile(profile_id: str, request: Request) -> Profile:
    store = _store(request)
    source = store.data.find_profile(profile_id)
    if source is None:
        raise HTTPException(404, "PROFILE_NOT_FOUND")
    copy = source.model_copy(
        update={
            "id": new_id(),
            "name": f"{source.name} (2)",
            "forwards": [],
        }
    )
    store.data.profiles.append(copy)
    store.touch()
    return copy


@router.get("/export")
async def export_profiles(request: Request, include_known_hosts: bool = False) -> dict:
    store = _store(request)
    payload = store.data.model_dump(mode="json")
    payload["exported_at"] = datetime.now(timezone.utc).isoformat()
    if not include_known_hosts:
        payload["known_hosts"] = []
    return payload


@router.post("/import")
async def import_profiles(payload: dict, request: Request, mode: str = "copy") -> dict:
    if payload.get("format") != WORKSPACE_FORMAT:
        raise HTTPException(400, "IMPORT_INVALID_FORMAT")
    if int(payload.get("version", 0)) > WORKSPACE_VERSION:
        raise HTTPException(400, "IMPORT_VERSION_TOO_NEW")
    try:
        incoming = Workspace.model_validate(payload)
    except Exception as exc:
        raise HTTPException(400, f"IMPORT_INVALID_FORMAT: {exc}") from exc

    store = _store(request)
    if mode == "overwrite":
        by_id = {p.id: p for p in store.data.profiles}
        for profile in incoming.profiles:
            profile = profile.model_copy(update={"name": profile.name})
            by_id[profile.id] = profile
        store.data.profiles = list(by_id.values())
        folder_ids = {f.id for f in store.data.folders}
        for folder in incoming.folders:
            if folder.id not in folder_ids:
                store.data.folders.append(folder)
        added = len(incoming.profiles)
    else:
        folder_map: dict[str, str] = {}
        for folder in incoming.folders:
            clone = Folder(id=new_id(), name=folder.name)
            folder_map[folder.id] = clone.id
            store.data.folders.append(clone)
        added = 0
        for profile in incoming.profiles:
            clone = profile.model_copy(
                update={
                    "id": new_id(),
                    "folder_id": folder_map.get(profile.folder_id or "", None),
                }
            )
            store.data.profiles.append(clone)
            added += 1

    store.touch()
    return {"ok": True, "imported": added, "mode": mode}
