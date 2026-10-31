"""M4 spike: chained jump host (ProxyJump) through the legacy sshd."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
KEY = ROOT / "tests" / "legacy_rsa"
MARK = "JUMP-中文-OK"


def call(base, token, method, path, payload=None):
    data = None
    headers = {"X-Bitssh-Token": token}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


async def open_and_ready(api_base, token, profile_id):
    code, snap = await asyncio.to_thread(call, api_base, token, "POST", "/api/sessions", {"profile_id": profile_id})
    if code != 200:
        return None, snap
    sid = snap["session_id"]
    for _ in range(200):
        code, s = await asyncio.to_thread(call, api_base, token, "GET", f"/api/sessions/{sid}")
        if s.get("hostkey"):
            await asyncio.to_thread(call, api_base, token, "POST", f"/api/sessions/{sid}/hostkey", {"decision": "always"})
        if s["state"] in ("ready", "closed"):
            return sid, s
        await asyncio.sleep(0.1)
    return sid, s


async def main() -> int:
    cfg = Path(tempfile.mkdtemp(prefix="bitssh-jump-"))
    env = dict(os.environ, BITSSH_CONFIG_DIR=str(cfg))
    hs = Path(tempfile.gettempdir()) / f"bitssh-hs-jump-{os.getpid()}.json"
    hs.unlink(missing_ok=True)
    proc = subprocess.Popen(
        [str(PY), "-m", "backend", "--server", "--handshake", str(hs), "--parent-pid", str(os.getpid())],
        cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 15
    info = None
    while time.monotonic() < deadline:
        if hs.exists():
            try:
                info = json.loads(hs.read_text("utf-8"))
                break
            except (json.JSONDecodeError, OSError):
                pass
        await asyncio.sleep(0.05)
    if not info:
        proc.kill()
        print("FAIL no handshake")
        return 1

    base, token = f"http://127.0.0.1:{info['port']}", info["token"]
    ok = True

    def check(label, cond, extra=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label + (f"  {extra}" if extra else ""))
        ok = ok and cond

    jump = {
        "host": "127.0.0.1", "port": 2222, "username": "root",
        "auth": {"method": "key", "key_path": str(KEY)},
    }
    code, prof = call(base, token, "POST", "/api/profiles", {
        "name": "jump", "host": "127.0.0.1", "port": 2222, "username": "root",
        "auth": {"method": "key", "key_path": str(KEY)},
        "jump": [jump],
    })
    check("create jump profile", code == 200, str(prof))

    sid, state = await open_and_ready(base, token, prof["id"])
    check("jump session ready", state["state"] == "ready", str(state))

    if state["state"] == "ready":
        url = f"ws://127.0.0.1:{info['port']}/ws/terminal/{sid}"
        async with websockets.connect(url, subprotocols=["bitssh.v1", token]) as ws:
            collected = b""
            await ws.send(f"echo {MARK}\n".encode())
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    break
                if isinstance(frame, bytes):
                    collected += frame
                    if MARK.encode() in collected.split(b"\n", 1)[-1]:
                        break
            check("pty through jump", MARK.encode() in collected, collected[-120:])
        await asyncio.to_thread(call, base, token, "DELETE", f"/api/sessions/{sid}")

    bad = dict(jump)
    bad["port"] = 2223
    code, prof2 = call(base, token, "POST", "/api/profiles", {
        "name": "jump-bad", "host": "127.0.0.1", "port": 2222, "username": "root",
        "auth": {"method": "key", "key_path": str(KEY)},
        "jump": [bad],
    })
    sid2, state2 = await open_and_ready(base, token, prof2["id"])
    check("broken jump host fails", state2["state"] == "closed" and state2["error"],
          str(state2))

    call(base, token, "POST", "/internal/shutdown")
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
