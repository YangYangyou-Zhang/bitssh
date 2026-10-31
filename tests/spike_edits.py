"""Edit-sync + drag-drop plumbing: edit, auto-upload on change, ticket download."""

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
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body and body[:1] in b"{[" else body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    cfg = Path(tempfile.mkdtemp(prefix="bitssh-edit-"))
    env = dict(os.environ, BITSSH_CONFIG_DIR=str(cfg), BITSSH_NO_LAUNCH="1")
    hs = Path(tempfile.gettempdir()) / f"bitssh-hs-edit-{os.getpid()}.json"
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
        "name": "edit", "host": "127.0.0.1", "port": 2222, "username": "root",
        "auth": {"method": "key", "key_path": str(KEY)},
    })
    code, snap = call(base, token, "POST", "/api/sessions", {"profile_id": prof["id"]})
    sid = snap["session_id"]
    state = None
    for _ in range(150):
        code, s = call(base, token, "GET", f"/api/sessions/{sid}")
        if s.get("hostkey"):
            call(base, token, "POST", f"/api/sessions/{sid}/hostkey", {"decision": "always"})
        state = s
        if s["state"] == "ready":
            break
        time.sleep(0.1)
    check("session ready", state["state"] == "ready", str(state))

    remote = "/tmp/bitssh-edit-sync.txt"
    code, up = call(base, token, "PUT", f"/api/sftp/{sid}/upload-stream?path={remote}",
                    raw=b"v1-original\n")
    check("stream upload", code == 200, str(up))

    code, edit = call(base, token, "POST", f"/api/sftp/{sid}/edit", {"path": remote})
    check("start edit", code == 200 and Path(edit["local_path"]).exists(), str(edit))
    local = Path(edit["local_path"])

    local.write_text("v2-已修改\n", encoding="utf-8")
    status = None
    for _ in range(80):
        time.sleep(0.25)
        code, items = call(base, token, "GET", "/api/edits")
        item = next((i for i in items if i["id"] == edit["id"]), None)
        status = item["status"] if item else None
        if status == "synced":
            break
    code, all_items = call(base, token, "GET", "/api/edits")
    detail = next((i for i in all_items if i["id"] == edit["id"]), None)
    check("auto upload after save", status == "synced", f"{status} {detail}")

    dst = cfg / "check.txt"
    code, down = call(base, token, "POST", f"/api/sftp/{sid}/download",
                      {"path": remote, "local_path": str(dst)})
    for _ in range(80):
        code, items = call(base, token, "GET", "/api/transfers")
        item = next((i for i in items if i["id"] == down["id"]), None)
        if item and item["status"] != "running":
            break
        time.sleep(0.2)
    expected = "v2-已修改\n".encode("utf-8")
    check("remote content updated", dst.read_bytes().replace(b"\r\n", b"\n") == expected,
          repr(dst.read_bytes()))

    code, ticket = call(base, token, "POST", f"/api/sftp/{sid}/download-ticket", {"path": remote})
    check("ticket issued", code == 200 and ticket["url"].startswith("/api/download/"), str(ticket))
    # Ticket download must work without the token header (browser drag-out).
    with urllib.request.urlopen(base + ticket["url"], timeout=10) as resp:
        body = resp.read()
        disposition = resp.headers.get("Content-Disposition", "")
    check("ticket download without token", body.replace(b"\r\n", b"\n") == expected, repr(body))
    check("content-disposition set", "attachment" in disposition, disposition)

    code, items = call(base, token, "GET", "/api/edits")
    check("edit listed", any(i["id"] == edit["id"] for i in items))

    def wait_status(want: str, timeout: float = 20.0) -> str | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            time.sleep(0.25)
            code, all_items = call(base, token, "GET", "/api/edits")
            item = next((i for i in all_items if i["id"] == edit["id"]), None)
            if item and item["status"] == want:
                return want
        return item["status"] if item else None

    # Remote changed behind our back -> conflict
    call(base, token, "PUT", f"/api/sftp/{sid}/upload-stream?path={remote}",
         raw=b"v3-remote-changed-longer\n")
    local.write_text("v4-local\n", encoding="utf-8")
    status = wait_status("conflict")
    code, all_items = call(base, token, "GET", "/api/edits")
    detail = next((i for i in all_items if i["id"] == edit["id"]), None)
    check("conflict detected", status == "conflict", f"{status} {detail}")

    code, _ = call(base, token, "POST", f"/api/edits/{edit['id']}/resolve", {"action": "overwrite"})
    check("resolve overwrite", code == 200)
    check("overwrite synced", wait_status("synced") == "synced")

    # Remote changes again, choose reload instead
    call(base, token, "PUT", f"/api/sftp/{sid}/upload-stream?path={remote}",
         raw=b"v5-remote-after-overwrite-xx\n")
    local.write_text("v6-local-should-be-discarded\n", encoding="utf-8")
    check("conflict again", wait_status("conflict") == "conflict")
    code, _ = call(base, token, "POST", f"/api/edits/{edit['id']}/resolve", {"action": "reload"})
    check("resolve reload", code == 200)
    time.sleep(0.5)
    reloaded = local.read_bytes().replace(b"\r\n", b"\n")
    check("reload pulled remote content", reloaded == b"v5-remote-after-overwrite-xx\n", repr(reloaded))

    call(base, token, "DELETE", f"/api/edits/{edit['id']}")

    if not ok:
        logfile = cfg / "logs" / "bitssh-backend.log"
        if logfile.exists():
            print("--- backend log tail ---")
            print(logfile.read_text(encoding="utf-8", errors="replace")[-2500:])

    call(base, token, "DELETE", f"/api/sessions/{sid}")
    call(base, token, "POST", "/internal/shutdown")
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
