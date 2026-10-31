from __future__ import annotations

import os
import posixpath
from pathlib import Path
from typing import Callable

import asyncssh

SFTP_TYPE_DIRECTORY = 2
SFTP_TYPE_SYMLINK = 3

# 256 KiB data + SFTP header exceeds the protocol's 256 KiB packet limit on
# OpenSSH 8.x; the server then aborts the subsystem (exit 11) mid-transfer.
SFTP_BLOCK_SIZE = 65536
SFTP_MAX_REQUESTS = 8

ProgressHandler = Callable[[bytes, bytes, int, int], None]

# One SFTP subsystem channel per SSH connection. Opening a new one per call
# exhausts sshd's MaxSessions (10 by default) and then fails with
# ChannelOpenError "open failed". Keyed by id() and holding the connection so
# the id cannot be recycled while cached.
_clients: dict[int, tuple[object, asyncssh.SFTPClient]] = {}


async def sftp_client(conn: asyncssh.SSHClientConnection) -> asyncssh.SFTPClient:
    key = id(conn)
    entry = _clients.get(key)
    if entry is not None and entry[0] is conn:
        return entry[1]
    client = await conn.start_sftp_client()
    _clients[key] = (conn, client)
    return client


def drop_client(conn) -> None:  # noqa: ANN001
    _clients.pop(id(conn), None)


def norm_remote(path: str) -> str:
    cleaned = posixpath.normpath(path or "/")
    return cleaned if cleaned.startswith("/") else "/" + cleaned


async def home_dir(conn: asyncssh.SSHClientConnection) -> str:
    """Remote home = the SFTP subsystem's initial working directory."""
    sftp = await sftp_client(conn)
    try:
        cwd = await sftp.getcwd()
        if cwd:
            return cwd
    except Exception:
        pass
    return await sftp.realpath(".")


async def list_dir(conn: asyncssh.SSHClientConnection, path: str) -> list[dict]:
    sftp = await sftp_client(conn)
    target = norm_remote(path)
    entries: list[dict] = []
    async for entry in sftp.scandir(target):
        if entry.filename in (".", ".."):
            continue
        attrs = entry.attrs
        entries.append(
            {
                "name": entry.filename,
                "dir": attrs.type == SFTP_TYPE_DIRECTORY,
                "link": attrs.type == SFTP_TYPE_SYMLINK,
                "size": int(attrs.size or 0),
                "mtime": int(attrs.mtime or 0),
                "mode": int(attrs.permissions or 0),
            }
        )
    entries.sort(key=lambda item: (not item["dir"], item["name"].lower()))
    return entries


async def stat_path(conn: asyncssh.SSHClientConnection, path: str) -> dict:
    sftp = await sftp_client(conn)
    attrs = await sftp.stat(norm_remote(path))
    return {
        "size": int(attrs.size or 0),
        "mtime": int(attrs.mtime or 0),
        "mode": int(attrs.permissions or 0),
        "dir": attrs.type == SFTP_TYPE_DIRECTORY,
    }


async def mkdir(conn, path: str) -> None:  # noqa: ANN001
    sftp = await sftp_client(conn)
    await sftp.mkdir(norm_remote(path))


async def remove(conn, path: str, is_dir: bool) -> None:  # noqa: ANN001
    sftp = await sftp_client(conn)
    target = norm_remote(path)
    if not is_dir:
        await sftp.remove(target)
        return

    async def purge(directory: str) -> None:
        children = [
            entry
            async for entry in sftp.scandir(directory)
            if entry.filename not in (".", "..")
        ]
        for child in children:
            child_path = posixpath.join(directory, child.filename)
            if child.attrs.type == SFTP_TYPE_DIRECTORY:
                await purge(child_path)
            else:
                await sftp.remove(child_path)
        await sftp.rmdir(directory)

    await purge(target)


async def rename(conn, old: str, new: str) -> None:  # noqa: ANN001
    sftp = await sftp_client(conn)
    await sftp.rename(norm_remote(old), norm_remote(new))


async def chmod(conn, path: str, mode: int) -> None:  # noqa: ANN001
    sftp = await sftp_client(conn)
    await sftp.chmod(norm_remote(path), mode)


async def download(
    conn,  # noqa: ANN001
    remote: str,
    local: str,
    progress: ProgressHandler | None = None,
) -> None:
    sftp = await sftp_client(conn)
    await sftp.get(
        norm_remote(remote),
        local,
        block_size=SFTP_BLOCK_SIZE,
        max_requests=SFTP_MAX_REQUESTS,
        progress_handler=progress,
    )


async def download_tree(
    conn,  # noqa: ANN001
    remote_dir: str,
    local_dir: Path,
    progress: ProgressHandler | None = None,
) -> None:
    """Recursively download a remote directory, preserving its structure."""
    sftp = await sftp_client(conn)
    base = norm_remote(remote_dir)
    files: list[tuple[str, Path, int]] = []

    async def walk(remote_path: str, local_path: Path) -> None:
        local_path.mkdir(parents=True, exist_ok=True)
        entries = [
            entry
            async for entry in sftp.scandir(remote_path)
            if entry.filename not in (".", "..")
        ]
        for entry in entries:
            child_remote = posixpath.join(remote_path, entry.filename)
            child_local = local_path / entry.filename
            if entry.attrs.type == SFTP_TYPE_DIRECTORY:
                await walk(child_remote, child_local)
            else:
                files.append((child_remote, child_local, int(entry.attrs.size or 0)))

    await walk(base, local_dir)
    total = sum(size for _r, _l, size in files)
    done = 0
    for remote_path, local_path, size in files:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        offset = done

        def report(_src, _dst, copied: int, _total: int, offset=offset) -> None:
            if progress:
                progress(b"", b"", offset + copied, total)

        await sftp.get(
            remote_path,
            str(local_path),
            block_size=SFTP_BLOCK_SIZE,
            max_requests=SFTP_MAX_REQUESTS,
            progress_handler=report,
        )
        done += size
    if progress:
        progress(b"", b"", total, total)


async def upload(
    conn,  # noqa: ANN001
    local: str,
    remote: str,
    progress: ProgressHandler | None = None,
) -> None:
    sftp = await sftp_client(conn)
    await sftp.put(
        local,
        norm_remote(remote),
        block_size=SFTP_BLOCK_SIZE,
        max_requests=SFTP_MAX_REQUESTS,
        progress_handler=progress,
    )


async def _mkdir_quiet(sftp, path: str) -> None:  # noqa: ANN001
    try:
        await sftp.mkdir(path)
    except Exception:
        pass  # already exists or no permission; put() will report the real error


async def upload_tree(
    conn,  # noqa: ANN001
    local: Path,
    remote_dir: str,
    progress: ProgressHandler | None = None,
) -> None:
    """Upload a file or a whole directory tree, preserving relative paths."""
    sftp = await sftp_client(conn)
    base = norm_remote(remote_dir)

    if local.is_file():
        total = local.stat().st_size
        await sftp.put(
            str(local),
            posixpath.join(base, local.name),
            block_size=SFTP_BLOCK_SIZE,
            max_requests=SFTP_MAX_REQUESTS,
            progress_handler=progress,
        )
        if progress:
            progress(b"", b"", total, total)
        return

    files: list[tuple[Path, str]] = []
    for root, _dirs, names in os.walk(local):
        root_path = Path(root)
        rel = root_path.relative_to(local).as_posix()
        rdir = base if rel == "." else posixpath.join(base, rel)
        await _mkdir_quiet(sftp, rdir)
        for name in names:
            files.append((root_path / name, posixpath.join(rdir, name)))

    total = sum(path.stat().st_size for path, _ in files if path.exists())
    done = 0
    for path, remote in files:
        size = path.stat().st_size
        offset = done

        def report(_src, _dst, copied: int, _total: int, offset=offset) -> None:
            if progress:
                progress(b"", b"", offset + copied, total)

        await sftp.put(
            str(path),
            remote,
            block_size=SFTP_BLOCK_SIZE,
            max_requests=SFTP_MAX_REQUESTS,
            progress_handler=report,
        )
        done += size
    if progress:
        progress(b"", b"", total, total)
