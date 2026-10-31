"""Stress the exact endpoints the UI uses and report real errors."""

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
KEY = ROOT / "tests" / "legacy_rsa"


def call(base, token, method, path, payload=None, raw=None):
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    headers = {"X-Bitssh-Token": token}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if raw is not None:
        headers["X-File-Size"] = str(len(raw))
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body[:1] in b"{[" else body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def wait(base, token, tid, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        code, items = call(base, token, "GET", "/api/transfers")
        item = next((i for i in items if i["id"] == tid), None)
        if item and item["status"] != "running":
            return item
        time.sleep(0.15)
    return {"status": "timeout"}


def main() -> int:
    cfg = Path(tempfile.mkdtemp(prefix="bitssh-stress-"))
    env = dict(os.environ, BITSSH_CONFIG_DIR=str(cfg), BITSSH_NO_LAUNCH="1")
    hs = Path(tempfile.gettempdir()) / f"bitssh-hs-stress-{os.getpid()}.json"
    hs.unlink(missing_ok=True)
    proc = subprocess.Popen(
        [str(PY), "-m", "backend", "--server", "--handshake", str(hs), "--parent-pid", str(os.getpid())],
        cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    end = time.monotonic() + 15
    info = None
    while time.monotonic() < end:
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
    code, prof = call(base, token, "POST", "/api/profiles", {
        "name": "stress", "host": "127.0.0.1", "port": 2222, "username": "root",
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
    if s["state"] != "ready":
        print("FAIL session", s)
        proc.kill()
        return 1

    remote_dir = "/tmp/bitssh-stress"
    call(base, token, "POST", f"/api/sftp/{sid}/ops", {"op": "remove", "path": remote_dir, "is_dir": True})
    call(base, token, "POST", f"/api/sftp/{sid}/ops", {"op": "mkdir", "path": remote_dir})

    failures = []
    names = [f"file {i:03d}.txt" for i in range(40)] + ["中文 名.txt", "a&b#c.txt"]
    for index, name in enumerate(names):
        payload = (f"payload-{index}-" * (index % 20 + 1)).encode("utf-8")
        code, body = call(base, token, "PUT",
                          f"/api/sftp/{sid}/upload-stream?path=/tmp/bitssh-stress/{urllib.request.quote(name)}",
                          raw=payload)
        if code != 200:
            failures.append((name, code, body))
    print(f"single uploads: {len(names) - len(failures)}/{len(names)} ok")
    for failure in failures[:5]:
        print("  FAILS", failure)

    # Folder tree upload (the backend path used by "upload folder").
    tree = cfg / "bigtree"
    for i in range(30):
        sub = tree / f"d{i % 5}"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / f"f{i}.bin").write_bytes(b"x" * (1024 * (i + 1)))
    code, up = call(base, token, "POST", f"/api/sftp/{sid}/upload-local",
                    {"local_path": str(tree), "remote_dir": "/tmp/bitssh-stress/tree"})
    print("upload-local start:", code, up)
    if code == 200:
        item = await_wait = wait(base, token, up["id"])
        print("upload-local result:", item["status"], item.get("error"), item.get("copied"))

    # Download over an existing local file.
    remote = "/tmp/bitssh-stress/file%20000.txt"
    local = cfg / "existing.txt"
    local.write_text("old", encoding="utf-8")
    code, body = call(base, token, "POST", f"/api/sftp/{sid}/download",
                      {"path": "/tmp/bitssh-stress/file 000.txt", "local_path": str(local)})
    print("download no-overwrite:", code, body if code != 200 else "")
    code, body = call(base, token, "POST", f"/api/sftp/{sid}/download",
                      {"path": "/tmp/bitssh-stress/file 000.txt", "local_path": str(local),
                       "overwrite": True})
    print("download overwrite:", code, body if code != 200 else "")
    if code == 200:
        item = wait(base, token, body["id"])
        print("overwrite transfer:", item["status"], item.get("error"), local.stat().st_size)

    call(base, token, "POST", "/internal/shutdown")
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
