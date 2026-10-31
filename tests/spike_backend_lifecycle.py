"""M0 spike: start the backend the way the shell does, then exercise it."""

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
PY = ROOT / ".venv" / "Scripts" / "python.exe" if os.name == "nt" else ROOT / ".venv" / "bin" / "python"


def request(url: str, token: str | None = None, method: str = "GET") -> tuple[int, bytes]:
    headers = {"X-Bitssh-Token": token} if token else {}
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    hs = Path(tempfile.gettempdir()) / f"bitssh-hs-test-{os.getpid()}.json"
    hs.unlink(missing_ok=True)

    proc = subprocess.Popen(
        [
            str(PY), "-m", "backend",
            "--server", "--handshake", str(hs), "--parent-pid", str(os.getpid()),
        ],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + 15
    data = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            print(f"FAIL backend exited early: {proc.returncode}")
            return 1
        if hs.exists():
            try:
                data = json.loads(hs.read_text("utf-8"))
                break
            except (json.JSONDecodeError, OSError):
                pass
        time.sleep(0.05)
    if not data:
        proc.kill()
        print("FAIL no handshake")
        return 1

    base = f"http://127.0.0.1:{data['port']}"
    print(f"handshake ok: port={data['port']} pid={data['pid']} version={data['version']}")
    print(f"handshake perms: {oct(hs.stat().st_mode & 0o777)}")

    code, _ = request(base + "/api/ping")
    print(f"no token -> {code} (expect 401)")

    code, body = request(base + "/api/ping", data["token"])
    print(f"with token -> {code} {body.decode()} (expect 200)")

    code, body = request(base + "/", None)
    print(f"index -> {code} bytes={len(body)}")

    code, _ = request(base + "/internal/shutdown", data["token"], "POST")
    print(f"shutdown -> {code} (expect 200)")

    try:
        rc = proc.wait(timeout=6)
    except subprocess.TimeoutExpired:
        proc.kill()
        print("FAIL backend did not exit after shutdown")
        return 1
    hs.unlink(missing_ok=True)
    print(f"backend exited rc={rc}")
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
