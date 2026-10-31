from __future__ import annotations

import asyncssh


class BitsshClient(asyncssh.SSHClient):
    """Wires asyncssh callbacks into the owning session."""

    def __init__(self, session) -> None:  # noqa: ANN001
        super().__init__()
        self._session = session

    def connection_lost(self, exc) -> None:
        self._session.on_connection_lost(exc)

    def auth_banner_received(self, msg, lang) -> None:  # noqa: ANN001
        self._session.emit({"type": "banner", "text": msg})

    async def kbdint_auth_requested(self) -> str:
        return ""

    async def kbdint_challenge_received(self, name, instructions, lang, prompts):  # noqa: ANN001
        return await self._session.ask_kbdint(name, instructions, prompts)
