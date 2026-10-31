from __future__ import annotations

import asyncio
import logging
import threading
import uuid

from ..models import Profile

log = logging.getLogger("bitssh.serial")


class SerialSession:
    """Duck-typed like SshSession so the terminal WebSocket can drive both."""

    def __init__(self, profile: Profile) -> None:
        self.id = uuid.uuid4().hex
        self.profile = profile
        self.state = "idle"
        self.events: asyncio.Queue = asyncio.Queue()
        self.pending_hostkey = None
        self.pending_kbdint = None
        self.error: str | None = None
        self.exit_status: int | None = None
        self.conn = None
        self.proc = None

        self.recent = bytearray()
        self._ser = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._paused = False
        self._reader: threading.Thread | None = None
        self._task: asyncio.Task | None = None

    # ---------- lifecycle ----------

    def emit(self, item) -> None:
        self.events.put_nowait(item)

    def _set_state(self, state: str) -> None:
        self.state = state
        self.emit({"type": "state", "state": state})

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        cfg = self.profile.serial
        if not cfg.port:
            self._fail("SERIAL_PORT_REQUIRED")
            return
        self._set_state("connecting")
        try:
            import serial

            self._ser = await asyncio.to_thread(
                serial.Serial,
                cfg.port,
                cfg.baudrate,
                bytesize=cfg.bytesize,
                parity=cfg.parity,
                stopbits=cfg.stopbits,
                timeout=0.05,
                write_timeout=2,
            )
        except ImportError:
            self._fail("SERIAL_UNAVAILABLE")
            return
        except PermissionError:
            self._fail("SERIAL_PERMISSION_DENIED")
            return
        except Exception as exc:
            message = str(exc).lower()
            if "busy" in message or "access is denied" in message or "permission denied" in message:
                self._fail("SERIAL_PORT_BUSY")
            else:
                log.warning("serial open failed: %s", exc)
                self._fail("CONNECT_FAILED")
            return

        self._loop = asyncio.get_running_loop()
        self._set_state("ready")
        self.emit({"type": "ready", "serial": True})
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        while not self._closed and self._ser is not None:
            try:
                data = self._ser.read(4096)
            except Exception:
                break
            if data and self._loop is not None:
                self.recent.extend(data)
                if len(self.recent) > 262144:
                    del self.recent[:-262144]
                try:
                    self._loop.call_soon_threadsafe(self.events.put_nowait, data)
                except RuntimeError:
                    break
        if not self._closed:
            self._set_state("closed")

    def _fail(self, code: str) -> None:
        self.error = code
        self._set_state("closed")
        self.emit({"type": "error", "code": code})

    async def close(self) -> None:
        self._closed = True
        if self._task and not self._task.done():
            self._task.cancel()
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                await asyncio.to_thread(ser.close)
            except Exception:
                pass
        self._set_state("closed")

    # ---------- terminal interface ----------

    def write(self, data: bytes) -> None:
        ser = self._ser
        if ser is None or self._closed:
            return
        try:
            ser.write(data)
        except Exception:
            log.debug("serial write failed", exc_info=True)

    def ack(self, size: int) -> None:
        return

    def resize(self, cols: int, rows: int) -> None:
        return

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    def on_connection_lost(self, exc) -> None:  # parity with SshSession
        return

    # ---------- serial controls ----------

    def set_line(self, dtr: bool | None = None, rts: bool | None = None) -> None:
        ser = self._ser
        if ser is None:
            return
        try:
            if dtr is not None:
                ser.dtr = dtr
            if rts is not None:
                ser.rts = rts
        except Exception:
            log.debug("serial line control failed", exc_info=True)

    def send_break(self, duration: float = 0.25) -> None:
        ser = self._ser
        if ser is None:
            return
        try:
            ser.send_break(duration)
        except Exception:
            log.debug("serial break failed", exc_info=True)
