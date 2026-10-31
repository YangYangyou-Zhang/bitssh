import asyncio
import pathlib
import sys

import asyncssh

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from backend.ssh import algs

FILES = sorted(
    p for p in (root / ".test" / "me").rglob("*") if p.is_file()
)[:3]


async def connect():
    return await asyncssh.connect(
        "127.0.0.1", port=2222, username="root", known_hosts=None,
        client_keys=[str(root / "tests" / "legacy_rsa")],
        kex_algs=algs.KEX_ALGS, server_host_key_algs=algs.SERVER_HOST_KEY_ALGS,
    )


async def attempt(label, indexes, block, requests):
    conn = await connect()
    sftp = await conn.start_sftp_client()
    try:
        await sftp.mkdir(f"/tmp/me-var-{label}")
    except Exception:
        pass
    print(f"[{label}] block={block} requests={requests}", flush=True)
    try:
        for index in indexes:
            path = FILES[index]
            remote = f"/tmp/me-var-{label}/{path.name}"
            try:
                await sftp.put(str(path), remote, block_size=block, max_requests=requests)
                print(f"  ok {index} {path.name} {path.stat().st_size}", flush=True)
            except Exception as exc:
                print(f"  FAIL {index}: {type(exc).__name__}: {exc}", flush=True)
                break
    finally:
        conn.close()


async def main() -> None:
    await attempt("solo2", [1], 262144, 16)
    await attempt("pair", [0, 1], 262144, 16)
    await attempt("small", [0, 1, 2], 65536, 8)
    await attempt("default", [0, 1, 2], 32768, 4)


asyncio.run(main())
