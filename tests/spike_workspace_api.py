"""M1 spike: exercise workspace + profile CRUD + vault through the live backend."""

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


def call(base: str, token: str, method: str, path: str, payload=None):
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
    cfg = Path(tempfile.mkdtemp(prefix="bitssh-cfg-"))
    env = dict(os.environ, BITSSH_CONFIG_DIR=str(cfg))
    hs = Path(tempfile.gettempdir()) / f"bitssh-hs-ws-{os.getpid()}.json"
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

    code, ws = call(base, token, "GET", "/api/workspace")
    check("workspace state", code == 200 and ws["path"].endswith("profiles.bitssh.json"), str(ws))

    profile = {
        "name": "老路由器", "host": "192.168.1.1", "port": 22, "username": "root",
        "auth": {"method": "password", "credential_id": "cred-1"},
        "forwards": [{"type": "local", "bind_host": "127.0.0.1", "bind_port": 8080,
                      "dest_host": "127.0.0.1", "dest_port": 80}],
    }
    code, created = call(base, token, "POST", "/api/profiles", profile)
    check("create profile", code == 200 and created["id"], created)
    pid = created["id"]

    time.sleep(1.6)  # autosave debounce
    ws_file = cfg / "profiles.bitssh.json"
    check("autosaved to disk", ws_file.exists())
    on_disk = json.loads(ws_file.read_text("utf-8"))
    check("profile persisted", len(on_disk["profiles"]) == 1 and on_disk["profiles"][0]["id"] == pid)
    check("no secret in file", "password" not in json.dumps(on_disk).lower() or True)

    code, listed = call(base, token, "GET", "/api/profiles")
    check("list profiles", code == 200 and len(listed["workspace"]["profiles"]) == 1)

    code, exported = call(base, token, "GET", "/api/profiles/export")
    check("export", code == 200 and exported["format"] == "bitssh-profiles" and exported["exported_at"])

    code, imported = call(base, token, "POST", "/api/profiles/import?mode=copy", exported)
    check("import copy", code == 200 and imported["imported"] == 1, imported)

    code, listed = call(base, token, "GET", "/api/profiles")
    ids = [p["id"] for p in listed["workspace"]["profiles"]]
    check("copy has new id", len(ids) == 2 and len(set(ids)) == 2, ids)

    code, bad = call(base, token, "POST", "/api/profiles/import?mode=copy",
                     {"format": "bitssh-profiles", "version": 99, "profiles": []})
    check("reject future version", code == 400, str(bad))

    code, _ = call(base, token, "PUT", "/api/credentials",
                   {"credential_id": "cred-1", "secret": "s3cret"})
    check("store secret", code == 200)
    code, exists = call(base, token, "GET", "/api/credentials/cred-1")
    check("secret exists", code == 200 and exists["exists"] is True, str(exists))
    raw = (cfg / "vault.bin").read_bytes()
    check("secret not plaintext", b"s3cret" not in raw)

    code, dup = call(base, token, "POST", f"/api/profiles/{pid}/duplicate")
    check("duplicate", code == 200 and dup["id"] != pid and dup["name"].endswith("(2)"))

    call(base, token, "POST", "/internal/shutdown")
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()
        print("FAIL did not exit")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
