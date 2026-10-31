"""M3 spike: local port forward and SOCKS5 dynamic forward."""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
KEY = ROOT / "tests" / "legacy_rsa"


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


def read_banner(port: int) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        return sock.recv(64)


def main() -> int:
    cfg = Path(tempfile.mkdtemp(prefix="bitssh-fwd-"))
    env = dict(os.environ, BITSSH_CONFIG_DIR=str(cfg))
    hs = Path(tempfile.gettempdir()) / f"bitssh-hs-fwd-{os.getpid()}.json"
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

    code, prof = call(base, token, "POST", "/api/profiles", {
        "name": "fwd", "host": "127.0.0.1", "port": 2222, "username": "root",
        "auth": {"method": "key", "key_path": str(KEY)},
    })
    code, snap = call(base, token, "POST", "/api/sessions", {"profile_id": prof["id"]})
    sid = snap["session_id"]
    for _ in range(150):
        code, s = call(base, token, "GET", f"/api/sessions/{sid}")
        if s.get("hostkey"):
            call(base, token, "POST", f"/api/sessions/{sid}/hostkey", {"decision": "always"})
        if s["state"] == "ready":
            break
        time.sleep(0.1)
    check("session ready", s["state"] == "ready", str(s))

    code, fwd = call(base, token, "POST", "/api/forwards", {
        "session_id": sid, "type": "local", "bind_host": "127.0.0.1", "bind_port": 0,
        "dest_host": "127.0.0.1", "dest_port": 2222,
    })
    check("local forward", code == 200 and fwd["bind_port"] > 0, str(fwd))
    banner = read_banner(fwd["bind_port"])
    check("local forward reaches sshd", banner.startswith(b"SSH-2.0"), repr(banner))

    code, socks = call(base, token, "POST", "/api/forwards", {
        "session_id": sid, "type": "socks", "bind_host": "127.0.0.1", "bind_port": 0,
    })
    check("socks forward", code == 200 and socks["bind_port"] > 0, str(socks))

    with socket.create_connection(("127.0.0.1", socks["bind_port"]), timeout=5) as sock:
        sock.sendall(b"\x05\x01\x00")
        greeting = sock.recv(2)
        sock.sendall(b"\x05\x01\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack("!H", 2222))
        reply = sock.recv(10)
        banner = sock.recv(64)
    check("socks greeting", greeting == b"\x05\x00", repr(greeting))
    check("socks connect ok", reply[:2] == b"\x05\x00", repr(reply))
    check("socks reaches sshd", banner.startswith(b"SSH-2.0"), repr(banner))

    code, remote = call(base, token, "POST", "/api/forwards", {
        "session_id": sid, "type": "remote", "bind_host": "127.0.0.1", "bind_port": 0,
        "dest_host": "127.0.0.1", "dest_port": 2222,
    })
    check("remote forward", code == 200 and remote["bind_port"] > 0, str(remote))
    if code == 200 and remote["bind_port"] > 0:
        probe = subprocess.run(
            ["wsl", "-d", "Ubuntu-20.04", "--", "bash", "-lc",
             f"timeout 5 bash -c 'exec 3<>/dev/tcp/127.0.0.1/{remote['bind_port']}; head -c 20 <&3'"],
            capture_output=True, timeout=30,
        )
        check("remote forward reaches dest", b"SSH-2.0" in probe.stdout, repr(probe.stdout[:40]))

    code, items = call(base, token, "GET", "/api/forwards")
    check("list forwards", code == 200 and len(items) == 3, str(items))

    code, _ = call(base, token, "DELETE", f"/api/forwards/{fwd['id']}")
    check("stop forward", code == 200)

    call(base, token, "DELETE", f"/api/sessions/{sid}")
    code, items = call(base, token, "GET", "/api/forwards")
    check("forwards cleaned with session", code == 200 and len(items) == 0, str(items))

    call(base, token, "POST", "/internal/shutdown")
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
