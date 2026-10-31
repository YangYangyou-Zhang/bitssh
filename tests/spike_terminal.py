"""M1 spike: connect to the legacy WSL sshd, confirm the host key, run a PTY."""

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
MARK = "MARK-中文-OK"


class Api:
    def __init__(self, base: str, token: str) -> None:
        self.base = base
        self.token = token

    def _call(self, method: str, path: str, payload=None):
        data = None
        headers = {"X-Bitssh-Token": self.token}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read()
                return resp.status, json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    async def call(self, method: str, path: str, payload=None):
        return await asyncio.to_thread(self._call, method, path, payload)


async def main() -> int:
    cfg = Path(tempfile.mkdtemp(prefix="bitssh-sess-"))
    env = dict(os.environ, BITSSH_CONFIG_DIR=str(cfg))
    hs = Path(tempfile.gettempdir()) / f"bitssh-hs-term-{os.getpid()}.json"
    hs.unlink(missing_ok=True)

    proc = subprocess.Popen(
        [str(PY), "-m", "backend", "--server", "--handshake", str(hs), "--parent-pid", str(os.getpid())],
        cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + 15
    info = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            print("FAIL backend exited", proc.returncode)
            return 1
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

    api = Api(f"http://127.0.0.1:{info['port']}", info["token"])
    ok = True

    def check(label, cond, extra=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + label + (f"  {extra}" if extra else ""))
        ok = ok and cond

    profile = {
        "name": "wsl-legacy",
        "host": "127.0.0.1",
        "port": 2222,
        "username": "root",
        "auth": {"method": "key", "key_path": str(KEY)},
    }
    code, created = await api.call("POST", "/api/profiles", profile)
    check("create profile", code == 200, str(created))
    pid = created["id"]

    code, snap = await api.call("POST", "/api/sessions", {"profile_id": pid})
    check("create session", code == 200, str(snap))
    sid = snap["session_id"]

    hostkey = None
    for _ in range(150):
        code, snap = await api.call("GET", f"/api/sessions/{sid}")
        if snap.get("hostkey"):
            hostkey = snap["hostkey"]
            break
        if snap["state"] in ("closed",):
            break
        await asyncio.sleep(0.1)
    check("hostkey prompt", hostkey is not None and hostkey["fingerprint"].startswith("SHA256:"), str(hostkey))

    if hostkey:
        code, _ = await api.call("POST", f"/api/sessions/{sid}/hostkey", {"decision": "always"})
        check("hostkey accepted", code == 200)

    state = None
    for _ in range(150):
        code, snap = await api.call("GET", f"/api/sessions/{sid}")
        state = snap["state"]
        if state in ("ready", "closed"):
            break
        await asyncio.sleep(0.1)
    check("session ready", state == "ready", str(snap))

    if state == "ready":
        url = f"ws://127.0.0.1:{info['port']}/ws/terminal/{sid}"
        async with websockets.connect(url, subprotocols=["bitssh.v1", info["token"]]) as ws:
            collected = b""
            deadline = time.monotonic() + 15
            await ws.send(f"echo {MARK}\n".encode())
            while time.monotonic() < deadline:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    break
                if isinstance(frame, bytes):
                    collected += frame
                    if MARK.encode() in collected.split(b"\n", 1)[-1]:
                        break
                else:
                    pass
            check("pty echo marker", MARK.encode() in collected, collected[-160:])

            await ws.send(json.dumps({"type": "resize", "cols": 120, "rows": 40}))
            await asyncio.sleep(0.3)

    code, ws_state = await api.call("GET", "/api/workspace")
    check("known host persisted", True)
    saved = json.loads((cfg / "profiles.bitssh.json").read_text("utf-8")) if (cfg / "profiles.bitssh.json").exists() else {}
    check("known_hosts recorded", len(saved.get("known_hosts", [])) == 1, str(saved.get("known_hosts")))

    code, _ = await api.call("DELETE", f"/api/sessions/{sid}")
    check("close session", code == 200)

    await api.call("POST", "/internal/shutdown")
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()
        ok = False
        print("FAIL backend did not exit")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
