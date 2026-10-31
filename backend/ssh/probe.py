from __future__ import annotations

import asyncio
import logging

import asyncssh

from . import algs

log = logging.getLogger("bitssh.probe")

# asyncssh treats an empty known_hosts string as "not specified" and falls
# back to the user's ~/.ssh/known_hosts. If the host happens to be listed
# there, validate_host_public_key is skipped entirely and the probe captures
# no key. A syntactically valid entry for a name that never matches keeps
# validation enabled while guaranteeing the callback fires, independent of
# whatever is in the system file.
_DUMMY_LINE = (
    b"bitssh-probe.invalid "
    + asyncssh.generate_private_key("ssh-ed25519").export_public_key().strip()
    + b"\n"
)


class _ProbeClient(asyncssh.SSHClient):
    """Captures the host key during KEX without ever authenticating."""

    key: asyncssh.SSHKey | None = None

    def validate_host_public_key(self, host, addr, port, key):  # noqa: ANN001
        self.key = key
        return True


async def probe_host_key(host: str, port: int, timeout: float = 12.0) -> asyncssh.SSHKey:
    client = _ProbeClient()
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                host,
                port=port,
                # Must stay non-empty and non-None: None disables validation
                # (no callback), and "" silently reads the system known_hosts.
                known_hosts=_DUMMY_LINE,
                client_factory=lambda: client,
                username="bitssh-probe",
                client_keys=[],
                agent_path=None,
                kex_algs=algs.KEX_ALGS,
                server_host_key_algs=algs.SERVER_HOST_KEY_ALGS,
                encryption_algs=algs.CIPHERS,
                mac_algs=algs.MACS,
                login_timeout=8,
            ),
            timeout,
        )
        conn.close()
    except Exception as exc:
        # Auth is expected to fail; the host key is captured during KEX.
        log.debug("probe finished with %s", type(exc).__name__)
    if client.key is None:
        raise RuntimeError("HOSTKEY_UNAVAILABLE")
    return client.key
