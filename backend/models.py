from __future__ import annotations

import uuid
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

WORKSPACE_FORMAT = "bitssh-profiles"
WORKSPACE_VERSION = 1


def new_id() -> str:
    return uuid.uuid4().hex


class AuthMethod(str, Enum):
    password = "password"
    key = "key"
    agent = "agent"
    kbdint = "kbdint"


class AuthSpec(BaseModel):
    method: AuthMethod = AuthMethod.password
    credential_id: str | None = None  # password lives in the vault, never in the JSON
    key_path: str | None = None
    key_credential_id: str | None = None  # passphrase, if any


class JumpHop(BaseModel):
    host: str
    port: int = 22
    username: str = ""
    auth: AuthSpec = Field(default_factory=AuthSpec)


class ForwardRule(BaseModel):
    id: str = Field(default_factory=new_id)
    type: Literal["local", "remote", "socks"] = "local"
    bind_host: str = "127.0.0.1"
    bind_port: int = 0
    dest_host: str = ""
    dest_port: int = 0
    auto_start: bool = False


class SerialConfig(BaseModel):
    port: str = ""
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1
    encoding: Literal["utf-8", "gbk", "latin-1"] = "utf-8"


class TerminalPrefs(BaseModel):
    term: str = "xterm-256color"
    scrollback: int = 5000


class Profile(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    protocol: Literal["ssh", "serial"] = "ssh"
    folder_id: str | None = None
    host: str = ""
    port: int = 22
    username: str = ""
    auth: AuthSpec = Field(default_factory=AuthSpec)
    jump: list[JumpHop] = Field(default_factory=list)
    forwards: list[ForwardRule] = Field(default_factory=list)
    serial: SerialConfig = Field(default_factory=SerialConfig)
    terminal: TerminalPrefs = Field(default_factory=TerminalPrefs)
    tags: list[str] = Field(default_factory=list)
    note: str = ""


class Folder(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    parent_id: str | None = None


class KnownHostRecord(BaseModel):
    host: str
    port: int = 22
    key_type: str
    key_b64: str
    added_at: str


class Workspace(BaseModel):
    format: Literal["bitssh-profiles"] = WORKSPACE_FORMAT
    version: int = WORKSPACE_VERSION
    app: str = "bitssh"
    exported_at: str | None = None
    folders: list[Folder] = Field(default_factory=list)
    profiles: list[Profile] = Field(default_factory=list)
    known_hosts: list[KnownHostRecord] = Field(default_factory=list)

    def find_profile(self, profile_id: str) -> Profile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)
