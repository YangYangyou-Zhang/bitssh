from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..atomic import write_text_atomic
from ..config import config_dir

log = logging.getLogger("bitssh.vault")

KEYRING_SERVICE = "bitssh"
KEYRING_USER = "vault-master"


class VaultLocked(Exception):
    pass


class Vault:
    """AES-256-GCM secrets file; the 32-byte master key lives in the OS keyring.

    Keyring entries are capped at 2560 bytes on Windows Credential Manager, so
    only this short key goes there -- private keys and passwords stay in the
    encrypted file.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "vault.bin")
        self._key: bytes | None = None
        self._entries: dict[str, dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        try:
            obj = json.loads(self.path.read_text(encoding="utf-8"))
            self._entries = dict(obj.get("entries", {}))
        except FileNotFoundError:
            self._entries = {}
        except Exception as exc:
            log.error("vault unreadable (starting empty): %s", exc)
            self._entries = {}

    def unlock_keyring(self) -> bool:
        try:
            import keyring

            stored = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            if stored:
                self._key = base64.urlsafe_b64decode(stored.encode("ascii"))
                return True
            key = secrets.token_bytes(32)
            keyring.set_password(
                KEYRING_SERVICE, KEYRING_USER, base64.urlsafe_b64encode(key).decode("ascii")
            )
            self._key = key
            return True
        except Exception as exc:
            log.warning("keyring unavailable: %s", exc)
            return False

    def set_key(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("master key must be 32 bytes")
        self._key = key

    @property
    def unlocked(self) -> bool:
        return self._key is not None

    def _require(self) -> bytes:
        if not self._key:
            raise VaultLocked("VAULT_LOCKED")
        return self._key

    def get(self, credential_id: str) -> bytes | None:
        entry = self._entries.get(credential_id)
        if not entry:
            return None
        aead = AESGCM(self._require())
        nonce = base64.b64decode(entry["n"])
        blob = base64.b64decode(entry["c"])
        return aead.decrypt(nonce, blob, credential_id.encode("utf-8"))

    def set(self, credential_id: str, secret: bytes) -> None:
        aead = AESGCM(self._require())
        nonce = os.urandom(12)
        blob = aead.encrypt(nonce, secret, credential_id.encode("utf-8"))
        self._entries[credential_id] = {
            "n": base64.b64encode(nonce).decode("ascii"),
            "c": base64.b64encode(blob).decode("ascii"),
        }
        self._save()

    def delete(self, credential_id: str) -> None:
        if self._entries.pop(credential_id, None) is not None:
            self._save()

    def _save(self) -> None:
        payload = json.dumps({"version": 1, "entries": self._entries}, indent=2)
        write_text_atomic(self.path, payload + "\n")
