from __future__ import annotations

import asyncio
import json
import logging

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import credentials as credentials_api
from .api import downloads as downloads_api
from .api import edits as edits_api
from .api import forwards as forwards_api
from .api import known_hosts as known_hosts_api
from .api import local_fs as local_fs_api
from .api import profiles as profiles_api
from .api import serial as serial_api
from .api import sessions as sessions_api
from .api import sftp as sftp_api
from .api import system as system_api
from .api import theme as theme_api
from .api import transfers as transfers_api
from .api import workspace as workspace_api
from .api import ws_terminal as ws_terminal_api
from .config import web_root
from .security import local_origin_ok, token_matches
from .services.edits import EditSyncManager
from .services.settings import SettingsStore
from .services.vault import Vault
from .services.transfers import TransferManager
from .services.workspace import WorkspaceStore
from .ssh.forwarding import ForwardManager
from .ssh.session import SessionManager

log = logging.getLogger("bitssh.app")

PLACEHOLDER = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>bitssh</title>
<style>
  body{background:#1e1e1e;color:#d4d4d4;font:14px/1.6 Consolas,"Noto Sans Mono CJK SC",monospace;
       display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
  .box{border:1px solid #3c3c3c;border-radius:8px;padding:24px 32px;min-width:420px}
  h1{font-size:16px;margin:0 0 12px;color:#4ec9b0}
  #st{color:#e2c08d}
  .ok{color:#4ec9b0}.bad{color:#f48771}
</style></head><body>
<div class="box">
  <h1>bitssh backend</h1>
  <div>API: <span id="st">checking…</span></div>
  <div>version: __VERSION__</div>
</div>
<script>
const token = location.hash.startsWith('#token=') ? location.hash.slice(7) : '';
const st = document.getElementById('st');
if (!token) { st.textContent = 'no token in URL fragment'; st.className = 'bad'; }
else {
  fetch('/api/ping', {headers: {'X-Bitssh-Token': token}})
    .then(r => r.ok ? r.json() : Promise.reject(r.status))
    .then(j => { st.textContent = 'ok (' + j.backend + ')'; st.className = 'ok'; })
    .catch(e => { st.textContent = 'failed: ' + e; st.className = 'bad'; });
}
</script></body></html>"""


class ShutdownFlag:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def request(self) -> None:
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


class GuardMiddleware:
    """Reject non-loopback Host, cross-origin requests and missing tokens.

    Implemented as raw ASGI so it also covers WebSocket scopes (HTTP middleware
    never sees ``scope['type'] == 'websocket'``).
    """

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if not local_origin_ok(headers.get("origin"), headers.get("host")):
            await self._reject(scope, send, 403, "BAD_ORIGIN")
            return

        path = scope.get("path", "")
        # Ticket downloads are fetched by the browser itself (drag-out), which
        # cannot attach headers; the single-use ticket is the credential.
        if path.startswith("/api/download/"):
            await self.app(scope, receive, send)
            return
        if path.startswith("/api") or path.startswith("/internal"):
            supplied = headers.get("x-bitssh-token")
            if not token_matches(self.token, supplied):
                await self._reject(scope, send, 401, "UNAUTHORIZED")
                return

        await self.app(scope, receive, send)

    async def _reject(self, scope, send, status: int, code: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        body = json.dumps({"error": code}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app(token: str, shutdown: ShutdownFlag) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    root = web_root()

    if root is None:

        @app.get("/", response_class=HTMLResponse)
        async def index() -> str:
            return PLACEHOLDER.replace("__VERSION__", __version__)

    @app.get("/api/ping")
    async def ping() -> JSONResponse:
        return JSONResponse({"ok": True, "backend": __version__})

    @app.post("/internal/shutdown")
    async def shutdown_endpoint() -> JSONResponse:
        log.info("shutdown requested")
        shutdown.request()
        return JSONResponse({"ok": True})

    settings = SettingsStore()
    store = WorkspaceStore(settings)
    store.load_initial()
    vault = Vault()
    if not vault.unlock_keyring():
        log.warning("vault is locked: credentials unavailable until unlocked")
    app.state.settings = settings
    app.state.store = store
    app.state.vault = vault
    app.state.token = token
    app.state.sessions = SessionManager(store, vault)
    app.state.transfers = TransferManager()
    app.state.forwards = ForwardManager()
    app.state.edits = EditSyncManager()
    app.state.download_tickets = {}

    app.include_router(workspace_api.router)
    app.include_router(profiles_api.router)
    app.include_router(known_hosts_api.router)
    app.include_router(credentials_api.router)
    app.include_router(sessions_api.router)
    app.include_router(sftp_api.router)
    app.include_router(transfers_api.router)
    app.include_router(forwards_api.router)
    app.include_router(edits_api.router)
    app.include_router(downloads_api.router)
    app.include_router(system_api.router)
    app.include_router(theme_api.router)
    app.include_router(local_fs_api.router)
    app.include_router(serial_api.router)
    app.include_router(ws_terminal_api.router)

    app.add_middleware(GuardMiddleware, token=token)
    if root is not None:
        app.mount("/", StaticFiles(directory=str(root), html=True), name="web")
    return app
