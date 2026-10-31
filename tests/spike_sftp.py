"""M2 spike: SFTP listing, upload, download and local browsing over the live API."""

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
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


async def wait_transfer(base, token, tid, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, items = await asyncio.to_thread(call, base, token, "GET", "/api/transfers")
        item = next((i for i in items if i["id"] == tid), None)
        if item and item["status"] != "running":
            return item
        await asyncio.sleep(0.2)
    return {"status": "timeout"}


async def main() -> int:
    cfg = Path(tempfile.mkdtemp(prefix="bitssh-sftp-"))
    env = dict(os.environ, BITSSH_CONFIG_DIR=str(cfg))
    hs = Path(tempfile.gettempdir()) / f"bitssh-hs-sftp-{os.getpid()}.json"
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

    code, created = call(base, token, "POST", "/api/profiles", {
        "name": "sftp", "host": "127.0.0.1", "port": 2222, "username": "root",
        "auth": {"method": "key", "key_path": str(KEY)},
    })
    code, snap = call(base, token, "POST", "/api/sessions", {"profile_id": created["id"]})
    sid = snap["session_id"]

    for _ in range(150):
        code, s = call(base, token, "GET", f"/api/sessions/{sid}")
        if s.get("hostkey"):
            call(base, token, "POST", f"/api/sessions/{sid}/hostkey", {"decision": "always"})
        if s["state"] == "ready":
            break
        await asyncio.sleep(0.1)
    check("session ready", s["state"] == "ready", str(s))

    code, listing = call(base, token, "GET", f"/api/sftp/{sid}/list?path=/")
    check("list /", code == 200 and any(e["name"] == "tmp" for e in listing["entries"]), f"{code} {len(listing.get('entries', []))}")

    code, home = call(base, token, "GET", f"/api/sftp/{sid}/home")
    check("remote home", code == 200 and home["path"].startswith("/"), str(home))

    for leftover in ("/tmp/bitssh-spike-dir", "/tmp/bitssh-tree", "/tmp/rmtree-debug"):
        call(base, token, "POST", f"/api/sftp/{sid}/ops",
             {"op": "remove", "path": leftover, "is_dir": True})

    payload = (ROOT / "tests" / "legacy_rsa.pub").read_bytes() * 200
    src = cfg / "upload.bin"
    src.write_bytes(payload)
    remote = "/tmp/bitssh-sftp-spike.bin"

    code, up = call(base, token, "POST", f"/api/sftp/{sid}/upload",
                    {"local_path": str(src), "path": remote})
    check("start upload", code == 200, str(up))
    item = await wait_transfer(base, token, up["id"])
    check("upload done", item["status"] == "done", str(item))

    code, listing = call(base, token, "GET", f"/api/sftp/{sid}/list?path=/tmp")
    entry = next((e for e in listing["entries"] if e["name"] == "bitssh-sftp-spike.bin"), None)
    check("remote file listed", entry is not None and entry["size"] == len(payload), str(entry))

    from urllib.parse import quote

    direct_url = f"{base}/api/download/direct/{sid}?path={quote(remote)}&token={token}"
    with urllib.request.urlopen(direct_url, timeout=10) as resp:
        direct_body = resp.read()
    check("direct download (drag-out) without header", direct_body == payload, f"{len(direct_body)}B")
    try:
        urllib.request.urlopen(f"{direct_url[:-12]}", timeout=5)
        check("direct download rejects bad token", False)
    except urllib.error.HTTPError as exc:
        check("direct download rejects bad token", exc.code in (401, 403), str(exc.code))

    dst = cfg / "download.bin"
    code, down = call(base, token, "POST", f"/api/sftp/{sid}/download",
                      {"path": remote, "local_path": str(dst)})
    check("start download", code == 200, str(down))
    item = await wait_transfer(base, token, down["id"])
    check("download done", item["status"] == "done", str(item))
    check("content matches", dst.exists() and dst.read_bytes() == payload)

    # Default destination (no local_path) must land in an absolute path that exists.
    code, d2 = call(base, token, "POST", f"/api/sftp/{sid}/download", {"path": remote})
    check("default download dir", code == 200 and Path(d2["local_path"]).is_absolute(), str(d2))
    item2 = await wait_transfer(base, token, d2["id"])
    downloaded = Path(d2["local_path"])
    check("default download file exists", item2["status"] == "done" and downloaded.is_file()
          and downloaded.stat().st_size == len(payload), str(item2))
    if downloaded.is_file():
        downloaded.unlink()

    code, again = call(base, token, "POST", f"/api/sftp/{sid}/download",
                       {"path": remote, "local_path": str(dst)})
    check("existing local file -> 409", code == 409 and again.get("detail") == "LOCAL_FILE_EXISTS",
          f"{code} {again}")
    code, forced = call(base, token, "POST", f"/api/sftp/{sid}/download",
                        {"path": remote, "local_path": str(dst), "overwrite": True})
    check("overwrite allowed", code == 200, str(forced))
    if code == 200:
        await wait_transfer(base, token, forced["id"])

    code, _ = call(base, token, "POST", f"/api/sftp/{sid}/ops",
                   {"op": "mkdir", "path": "/tmp/bitssh-spike-dir"})
    check("mkdir", code == 200)
    code, _ = call(base, token, "POST", f"/api/sftp/{sid}/ops",
                   {"op": "rename", "path": remote, "new_path": "/tmp/bitssh-spike-dir/a.bin"})
    check("rename", code == 200)
    code, _ = call(base, token, "POST", f"/api/sftp/{sid}/ops",
                   {"op": "remove", "path": "/tmp/bitssh-spike-dir/a.bin"})
    check("remove file", code == 200)
    code, _ = call(base, token, "POST", f"/api/sftp/{sid}/ops",
                   {"op": "remove", "path": "/tmp/bitssh-spike-dir", "is_dir": True})
    check("remove dir", code == 200)

    tree = cfg / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "a.txt").write_bytes(b"A")
    (tree / "sub" / "b.txt").write_bytes(b"BB")
    code, up3 = call(base, token, "POST", f"/api/sftp/{sid}/upload-local",
                     {"local_path": str(tree), "remote_dir": "/tmp/bitssh-tree"})
    check("start folder upload", code == 200, str(up3))
    item3 = await wait_transfer(base, token, up3["id"])
    check("folder upload done", item3["status"] == "done", str(item3))
    code, top = call(base, token, "GET", f"/api/sftp/{sid}/list?path=/tmp/bitssh-tree")
    names = {e["name"] for e in top.get("entries", [])}
    check("folder tree files", {"a.txt", "sub"} <= names, str(names))
    code, sub = call(base, token, "GET", f"/api/sftp/{sid}/list?path=/tmp/bitssh-tree/sub")
    check("nested file", any(e["name"] == "b.txt" for e in sub.get("entries", [])),
          str(sub.get("entries")))
    tree_local = cfg / "tree-dl"
    code, tdl = call(base, token, "POST", f"/api/sftp/{sid}/download-tree",
                     {"path": "/tmp/bitssh-tree", "local_dir": str(tree_local)})
    check("start folder download", code == 200, str(tdl))
    if code == 200:
        item = await wait_transfer(base, token, tdl["id"])
        check("folder download done", item["status"] == "done", str(item))
        check("folder structure downloaded",
              (tree_local / "a.txt").is_file() and (tree_local / "sub" / "b.txt").is_file(),
              str(list(tree_local.rglob('*'))))

    code, _ = call(base, token, "POST", f"/api/sftp/{sid}/ops",
                   {"op": "remove", "path": "/tmp/bitssh-tree", "is_dir": True})
    check("recursive remove", code == 200)

    code, local = call(base, token, "GET", "/api/local/list?path=~")
    check("local list", code == 200 and "entries" in local, f"{code}")

    call(base, token, "DELETE", f"/api/sessions/{sid}")
    call(base, token, "POST", "/internal/shutdown")
    try:
        proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
