from __future__ import annotations

import asyncio
import logging
import uuid

import asyncssh

from ..models import Profile, new_id
from ..services.vault import Vault, VaultLocked
from ..services.workspace import WorkspaceStore
from . import algs
from .client import BitsshClient
from .knownhosts import find_record, key_line, record_for_key, render_known_hosts
from .sftp_service import drop_client
from .probe import probe_host_key

log = logging.getLogger("bitssh.session")

HIGH_WATER = 256 * 1024
LOW_WATER = 64 * 1024
COLS, ROWS = 80, 24


class SessionError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SshSession:
    def __init__(self, profile: Profile, store: WorkspaceStore, vault: Vault) -> None:
        self.id = uuid.uuid4().hex
        self.profile = profile
        self.store = store
        self.vault = vault

        self.state = "idle"
        self.events: asyncio.Queue = asyncio.Queue()
        self.pending_hostkey: dict | None = None
        self.pending_kbdint: dict | None = None
        self.error: str | None = None
        self.exit_status: int | None = None

        self.conn: asyncssh.SSHClientConnection | None = None
        self.proc: asyncssh.SSHClientProcess | None = None
        self._tunnels: list[asyncssh.SSHClientConnection] = []

        self._hostkey_future: asyncio.Future | None = None
        self._kbdint_future: asyncio.Future | None = None
        self._password: str | None = None
        self._passphrase: str | None = None
        self._closed = False
        self._task: asyncio.Task | None = None

        # Recent PTY output, replayed to a client that attaches later (e.g.
        # after popping the terminal out into its own window).
        self.recent = bytearray()
        self._pending_bytes = 0
        self._ack_event = asyncio.Event()
        self._ack_event.set()
        self._paused = False
        self._resume_event = asyncio.Event()
        self._resume_event.set()

    # ---------- plumbing ----------

    def emit(self, item) -> None:
        self.events.put_nowait(item)

    def _set_state(self, state: str) -> None:
        self.state = state
        self.emit({"type": "state", "state": state})

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            await self._connect()
        except SessionError as exc:
            self._fail(exc.code)
        except VaultLocked:
            self._fail("VAULT_LOCKED")
        except asyncssh.HostKeyNotVerifiable:
            self._fail("HOSTKEY_MISMATCH")
        except asyncssh.PermissionDenied:
            self._fail("AUTH_FAILED")
        except asyncssh.KeyExchangeFailed:
            self._fail("ALGO_MISMATCH")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("session %s failed", self.id)
            self._fail("CONNECT_FAILED")

    def _fail(self, code: str) -> None:
        self.error = code
        self._set_state("closed")
        self.emit({"type": "error", "code": code})

    # ---------- credentials ----------

    async def _load_credentials(self) -> None:
        auth = self.profile.auth
        if auth.method in ("password", "kbdint"):
            if not auth.credential_id:
                auth.credential_id = new_id()
                self.store.touch()
            secret = self.vault.get(auth.credential_id)
            if secret:
                self._password = secret.decode("utf-8")
        elif auth.method == "key" and auth.key_credential_id:
            secret = self.vault.get(auth.key_credential_id)
            if secret:
                self._passphrase = secret.decode("utf-8")

    def set_password(self, password: str) -> None:
        auth = self.profile.auth
        if not auth.credential_id:
            auth.credential_id = new_id()
        self.vault.set(auth.credential_id, password.encode("utf-8"))
        self._password = password
        self.store.touch()

    # ---------- connect ----------

    async def _connect(self) -> None:
        profile = self.profile
        if profile.protocol != "ssh":
            raise SessionError("UNSUPPORTED_PROTOCOL")
        if not profile.host:
            raise SessionError("INVALID_PROFILE")

        self._set_state("connecting")
        await self._load_credentials()
        if profile.auth.method in ("password", "kbdint") and not self._password:
            raise SessionError("CREDENTIAL_REQUIRED")

        extras: list[str] = []
        tunnel: asyncssh.SSHClientConnection | None = None
        hops = [(h.host, h.port, h.username, h.auth) for h in profile.jump]
        hops.append((profile.host, profile.port, profile.username, profile.auth))

        for index, (host, port, username, auth) in enumerate(hops):
            is_target = index == len(hops) - 1
            extra = await self._verify_host_key(host, port)
            if extra:
                extras.append(extra)
            text = render_known_hosts(self.store.data.known_hosts) + "".join(extras)
            password = self._password if is_target else self._secret_for(auth.credential_id)
            passphrase = self._passphrase if is_target else self._secret_for(auth.key_credential_id)
            kwargs = self._kwargs_for(auth, username, password, passphrase, text)
            if tunnel is not None:
                kwargs["tunnel"] = tunnel
            self._set_state("auth")
            conn = await asyncssh.connect(host, port=port, **kwargs)
            if is_target:
                self.conn = conn
            else:
                tunnel = conn
                self._tunnels.append(conn)

        term = profile.terminal.term or "xterm-256color"
        self.proc = await self.conn.create_process(
            term_type=term,
            term_size=(COLS, ROWS),
            encoding=None,
            send_eof=False,
            stderr=asyncssh.STDOUT,
        )
        self._set_state("ready")
        self.emit({"type": "ready", "cols": COLS, "rows": ROWS, "term": term})
        asyncio.create_task(self._reader())
        asyncio.create_task(self._waiter())

    def _secret_for(self, credential_id: str | None) -> str | None:
        if not credential_id:
            return None
        secret = self.vault.get(credential_id)
        return secret.decode("utf-8") if secret else None

    async def _verify_host_key(self, host: str, port: int) -> str:
        record = find_record(self.store.data.known_hosts, host, port)
        if record is not None:
            return ""

        key = await probe_host_key(host, port)
        self.pending_hostkey = {
            "host": host,
            "port": port,
            "key_type": key.get_algorithm(),
            "fingerprint": key.get_fingerprint("sha256"),
        }
        self._hostkey_future = asyncio.get_running_loop().create_future()
        self._set_state("hostkey_check")
        self.emit({"type": "hostkey", **self.pending_hostkey})
        decision = await self._hostkey_future
        self.pending_hostkey = None
        self._hostkey_future = None

        if decision == "reject":
            raise SessionError("HOSTKEY_REJECTED")
        if decision == "always":
            self.store.data.known_hosts.append(record_for_key(host, port, key))
            self.store.touch()
            return ""
        return key_line(host, port, key) + "\n"

    def _kwargs_for(
        self,
        auth,  # noqa: ANN001 - AuthSpec
        username: str,
        password: str | None,
        passphrase: str | None,
        known_hosts_text: str,
    ) -> dict:
        if not known_hosts_text.strip():
            raise SessionError("HOSTKEY_UNAVAILABLE")
        kwargs: dict = {
            "known_hosts": known_hosts_text.encode("utf-8"),
            "client_factory": lambda: BitsshClient(self),
            "kex_algs": algs.KEX_ALGS,
            "server_host_key_algs": algs.SERVER_HOST_KEY_ALGS,
            "encryption_algs": algs.CIPHERS,
            "mac_algs": algs.MACS,
            "keepalive_interval": 30,
            "keepalive_count_max": 3,
            "login_timeout": 15,
        }
        if username:
            kwargs["username"] = username
        if auth.method in ("password", "kbdint"):
            kwargs["password"] = password
        elif auth.method == "key":
            kwargs["client_keys"] = [auth.key_path] if auth.key_path else []
            if passphrase:
                kwargs["passphrase"] = passphrase
        elif auth.method == "agent":
            pass  # asyncssh uses the local agent by default
        return kwargs

    # ---------- prompts ----------

    def resolve_hostkey(self, decision: str) -> bool:
        if self._hostkey_future is None or self._hostkey_future.done():
            return False
        self._hostkey_future.set_result(decision)
        return True

    async def ask_kbdint(self, name: str, instructions: str, prompts) -> list[str] | None:
        if self._password is not None and len(prompts) == 1 and not prompts[0][1]:
            return [self._password]
        self.pending_kbdint = {
            "name": name,
            "instruction": instructions,
            "prompts": [[text, bool(echo)] for text, echo in prompts],
        }
        self._kbdint_future = asyncio.get_running_loop().create_future()
        self.emit({"type": "kbdint", **self.pending_kbdint})
        try:
            return await self._kbdint_future
        finally:
            self.pending_kbdint = None
            self._kbdint_future = None

    def resolve_kbdint(self, responses: list[str]) -> bool:
        if self._kbdint_future is None or self._kbdint_future.done():
            return False
        self._kbdint_future.set_result(responses)
        return True

    # ---------- io ----------

    async def _reader(self) -> None:
        assert self.proc is not None
        try:
            while True:
                if self._paused:
                    self._resume_event.clear()
                    await self._resume_event.wait()
                while self._pending_bytes > HIGH_WATER and not self._closed:
                    self._ack_event.clear()
                    await self._ack_event.wait()
                data = await self.proc.stdout.read(65536)
                if not data:
                    break
                self._pending_bytes += len(data)
                self.recent.extend(data)
                if len(self.recent) > 262144:
                    del self.recent[:-262144]
                self.emit(data)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reader failed")
        finally:
            self._set_state("closed")

    async def _waiter(self) -> None:
        assert self.proc is not None
        try:
            await self.proc.wait()
            self.exit_status = self.proc.exit_status
            self.emit({"type": "exit", "code": self.exit_status})
        except Exception:
            pass

    def ack(self, size: int) -> None:
        self._pending_bytes = max(0, self._pending_bytes - max(0, size))
        if self._pending_bytes <= LOW_WATER:
            self._ack_event.set()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if not paused:
            self._resume_event.set()

    def write(self, data: bytes) -> None:
        if self.proc is not None and not self._closed:
            self.proc.stdin.write(data)

    def resize(self, cols: int, rows: int) -> None:
        if self.proc is not None and cols > 0 and rows > 0:
            try:
                self.proc.channel.change_terminal_size(cols, rows)
            except Exception:
                log.debug("resize failed", exc_info=True)

    def on_connection_lost(self, exc) -> None:
        if not self._closed:
            self._set_state("closed")
            if exc is not None:
                self.emit({"type": "error", "code": "CONNECTION_LOST"})

    async def close(self) -> None:
        self._closed = True
        if self._task and not self._task.done():
            self._task.cancel()
        if self.proc is not None:
            try:
                self.proc.close()
            except Exception:
                pass
        if self.conn is not None:
            drop_client(self.conn)
            self.conn.close()
            try:
                await asyncio.wait_for(self.conn.wait_closed(), 3)
            except Exception:
                pass
        for tunnel in self._tunnels:
            try:
                tunnel.close()
                await asyncio.wait_for(tunnel.wait_closed(), 3)
            except Exception:
                pass
        self._tunnels.clear()


class SessionManager:
    def __init__(self, store: WorkspaceStore, vault: Vault) -> None:
        self.store = store
        self.vault = vault
        self.sessions: dict[str, SshSession] = {}

    def create(self, profile: Profile, password: str | None = None) -> SshSession:
        session = SshSession(profile, self.store, self.vault)
        if password:
            session.set_password(password)
        self.sessions[session.id] = session
        session.start()
        return session

    def register(self, session) -> None:  # noqa: ANN001 - serial sessions share the interface
        self.sessions[session.id] = session

    def get(self, session_id: str):  # noqa: ANN201
        return self.sessions.get(session_id)

    async def close(self, session_id: str) -> bool:
        session = self.sessions.pop(session_id, None)
        if session is None:
            return False
        await session.close()
        return True

    async def close_all(self) -> None:
        for session_id in list(self.sessions):
            await self.close(session_id)
