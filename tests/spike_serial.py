"""M2.5 spike: serial port enumeration and error mapping."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")


def call(base, token, method, path, payload=None):
    data = None
    headers = {"X-Bitssh-Token": token}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def main() -> int:
    cfg = Path(tempfile.mkdtemp(prefix="bitssh-serial-"))
    env = dict(os.environ, BITSSH_CONFIG_DIR=str(cfg))
    hs = Path(tempfile.gettempdir()) / f"bitssh-hs-ser-{os.getpid()}.json"
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
        time.sleep(0.05)
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

    code, ports = call(base, token, "GET", "/api/serial/ports")
    check("list ports", code == 200 and isinstance(ports, list), f"{code} n={len(ports) if isinstance(ports, list) else '?'}")
    if isinstance(ports, list) and ports:
        print("     sample:", ports[0])

    code, prof = call(base, token, "POST", "/api/profiles", {
        "name": "bad-serial", "protocol": "serial",
        "serial": {"port": "COM_NOT_EXIST", "baudrate": 115200},
    })
    check("create serial profile", code == 200, str(prof))

    code, sess = call(base, token, "POST", "/api/serial/sessions", {"profile_id": prof["id"]})
    check("create serial session", code == 200, str(sess))
    sid = sess["session_id"]

    state = None
    for _ in range(80):
        code, s = call(base, token, "GET", f"/api/sessions/{sid}")
        state = s
        if s["state"] == "closed":
            break
        time.sleep(0.1)
    check("failure mapped", state is not None and state["state"] == "closed" and state["error"],
          str(state))

    code, _ = call(base, token, "POST", "/internal/shutdown")
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
