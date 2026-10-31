from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn

from . import __version__
from .app import ShutdownFlag, create_app
from .config import log_dir
from .logsetup import setup_logging
from .security import new_token

log = logging.getLogger("bitssh")


def _install_parent_death_signal() -> None:
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, 15)  # PR_SET_PDEATHSIG, SIGTERM
    except Exception:
        log.debug("PR_SET_PDEATHSIG unavailable", exc_info=True)


def _watch_parent(parent_pid: int, loop: asyncio.AbstractEventLoop, flag: ShutdownFlag) -> None:
    def notify(reason: str) -> None:
        log.info("parent gone (%s), shutting down", reason)
        try:
            loop.call_soon_threadsafe(flag.request)
        except RuntimeError:
            pass

    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE = 0x00100000
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(SYNCHRONIZE, False, parent_pid)
        if not handle:
            notify("OpenProcess failed")
            return
        k32.WaitForSingleObject(handle, 0xFFFFFFFF)
        notify("wait returned")
        return

    while True:
        time.sleep(2.0)
        if os.getppid() != parent_pid:
            notify("ppid changed")
            return


def _replace_with_retry(src: str, dst: Path) -> None:
    delay = 0.05
    for attempt in range(5):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == 4:
                raise
            time.sleep(delay)
            delay *= 2


def _write_handshake(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".hs-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        _replace_with_retry(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


async def _serve(args: argparse.Namespace) -> int:
    setup_logging(log_dir())
    _install_parent_death_signal()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.set_inheritable(False)
    sock.bind(("127.0.0.1", args.port))
    sock.listen(128)
    port = sock.getsockname()[1]

    token = args.token or new_token()
    flag = ShutdownFlag()
    app = create_app(token, flag)
    config = uvicorn.Config(
        app,
        log_config=None,
        access_log=False,
        lifespan="off",
        ws_max_size=16 * 1024 * 1024,
    )
    server = uvicorn.Server(config)
    server.config.setup_event_loop = lambda: None  # we already are in a loop

    if args.handshake:
        _write_handshake(
            Path(args.handshake),
            {"port": port, "token": token, "pid": os.getpid(), "version": __version__},
        )
        log.info("handshake written to %s (port %d)", args.handshake, port)
    else:
        print(json.dumps({"port": port, "token": token}), flush=True)

    loop = asyncio.get_running_loop()
    if args.parent_pid:
        threading.Thread(
            target=_watch_parent, args=(args.parent_pid, loop, flag), daemon=True
        ).start()

    asyncio.create_task(app.state.store.autosave_loop())
    serve_task = asyncio.create_task(server.serve(sockets=[sock]))
    wait_task = asyncio.create_task(flag.wait())
    done, pending = await asyncio.wait(
        {serve_task, wait_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    if wait_task in done and not serve_task.done():
        app.state.forwards.stop_all()
        await app.state.edits.stop_all()
        await app.state.sessions.close_all()
        server.should_exit = True
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bitssh-backend")
    parser.add_argument("--server", action="store_true", help="run headless backend")
    parser.add_argument("--handshake", help="write {port, token} to this file")
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", default="")
    args = parser.parse_args(argv)

    if not args.server:
        parser.error("only --server mode is implemented")

    try:
        return asyncio.run(_serve(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
