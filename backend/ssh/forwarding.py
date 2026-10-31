from __future__ import annotations

import logging
import uuid

log = logging.getLogger("bitssh.forwarding")


class ForwardError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ForwardManager:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def list(self) -> list[dict]:
        return [{k: v for k, v in item.items() if k != "listener"} for item in self.items.values()]

    async def start(
        self,
        session,
        kind: str,
        bind_host: str,
        bind_port: int,
        dest_host: str,
        dest_port: int,
    ) -> dict:
        conn = getattr(session, "conn", None)
        if conn is None:
            raise ForwardError("SESSION_NOT_READY")
        bind_host = bind_host or "127.0.0.1"
        try:
            if kind == "local":
                listener = await conn.forward_local_port(
                    bind_host, bind_port, dest_host, dest_port
                )
            elif kind == "remote":
                listener = await conn.forward_remote_port(
                    bind_host, bind_port, dest_host, dest_port
                )
            elif kind == "socks":
                # 0 means "let the OS pick", which is what the UI sends by default.
                listener = await conn.forward_socks(bind_host, bind_port)
            else:
                raise ForwardError("UNKNOWN_FORWARD_TYPE")
        except ForwardError:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if "address already in use" in message or "bind" in message:
                raise ForwardError("FORWARD_PORT_IN_USE") from exc
            if "administratively prohibited" in message or "forbidden" in message:
                raise ForwardError("REMOTE_FORWARD_DENIED") from exc
            log.warning("forward failed: %s", exc)
            raise ForwardError("FORWARD_FAILED") from exc

        try:
            actual_port = listener.get_port()
        except Exception:
            actual_port = bind_port

        item = {
            "id": uuid.uuid4().hex,
            "session_id": session.id,
            "type": kind,
            "bind_host": bind_host,
            "bind_port": actual_port,
            "dest_host": dest_host,
            "dest_port": dest_port,
            "listener": listener,
        }
        self.items[item["id"]] = item
        return {k: v for k, v in item.items() if k != "listener"}

    def stop(self, forward_id: str) -> bool:
        item = self.items.pop(forward_id, None)
        if item is None:
            return False
        listener = item.get("listener")
        if listener is not None:
            try:
                listener.close()
            except Exception:
                log.debug("listener close failed", exc_info=True)
        return True

    def stop_session(self, session_id: str) -> None:
        for forward_id, item in list(self.items.items()):
            if item["session_id"] == session_id:
                self.stop(forward_id)

    def stop_all(self) -> None:
        for forward_id in list(self.items):
            self.stop(forward_id)
