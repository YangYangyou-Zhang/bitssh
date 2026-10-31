from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..atomic import write_text_atomic
from ..config import config_dir

router = APIRouter(prefix="/api/theme")

DEFAULT_THEME: dict[str, str] = {
    "bg": "#18211e",
    "bgElev": "#202c28",
    "bgPanel": "#1c2722",
    "bgHover": "#22332e",
    "border": "#303835",
    "borderStrong": "#4b5250",
    "text": "#cccccc",
    "textStrong": "#ffffff",
    "textDim": "#8f9693",
    "textFaint": "#6f7a75",
    "accent": "#16825d",
    "accentHover": "#1f9d71",
    "accentBg": "#13704f",
    "selection": "#304802",
    "ok": "#16825d",
    "warn": "#d7ba7d",
    "err": "#e5674f",
    "termBg": "#18211e",
    "termFg": "#cccccc",
    "termCursor": "#6fa904",
    "termSel": "#304802",
    "statusBg": "#202c28",
    "statusFg": "#cccccc",
    "buttonBg": "#273831",
    "buttonHover": "#31463e",
    "buttonBorder": "#3d554b",
    "errorBg": "#3a1f1b",
    "errorFg": "#f0a08c",
    "selectionText": "#ffffff",
    "focusRing": "#6fa904",
    "opacity": "1.00",
}

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class ThemeBody(BaseModel):
    theme: dict[str, str]


def theme_path() -> Path:
    return config_dir() / "theme.json"


def load_theme() -> dict[str, str]:
    theme = dict(DEFAULT_THEME)
    try:
        stored = json.loads(theme_path().read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key not in theme or not isinstance(value, str):
                    continue
                if key == "opacity":
                    try:
                        number = float(value)
                    except ValueError:
                        continue
                    if 0.2 <= number <= 1.0:
                        theme[key] = f"{number:.2f}"
                elif HEX.match(value):
                    theme[key] = value
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return theme


@router.get("")
async def get_theme() -> dict:
    return {
        "path": str(theme_path()),
        "theme": load_theme(),
        "defaults": DEFAULT_THEME,
    }


@router.put("")
async def put_theme(body: ThemeBody) -> dict:
    cleaned: dict[str, str] = {}
    for key, value in body.theme.items():
        if key not in DEFAULT_THEME:
            raise HTTPException(400, "THEME_UNKNOWN_KEY")
        if key == "opacity":
            try:
                number = float(value)
            except ValueError as exc:
                raise HTTPException(400, "THEME_INVALID_COLOR") from exc
            if not 0.2 <= number <= 1.0:
                raise HTTPException(400, "THEME_INVALID_COLOR")
            cleaned[key] = f"{number:.2f}"
            continue
        if not HEX.match(value):
            raise HTTPException(400, "THEME_INVALID_COLOR")
        cleaned[key] = value
    if len(cleaned) > 64:
        raise HTTPException(400, "THEME_TOO_LARGE")
    payload = dict(load_theme())
    payload.update(cleaned)
    write_text_atomic(theme_path(), json.dumps(payload, indent=2) + "\n")
    return {"path": str(theme_path()), "theme": payload}


@router.delete("")
async def reset_theme() -> dict:
    try:
        theme_path().unlink()
    except FileNotFoundError:
        pass
    return {"path": str(theme_path()), "theme": DEFAULT_THEME}
