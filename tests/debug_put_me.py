import asyncio
import pathlib
import sys

import asyncssh

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from backend.ssh import algs


async def main() -> None:
    conn = await asyncssh.connect(
        "127.0.0.1", port=2222, username="root", known_hosts=None,
        client_keys=[str(root / "tests" / "legacy_rsa")],
        kex_algs=algs.KEX_ALGS, server_host_key_algs=algs.SERVER_HOST_KEY_ALGS,
    )
    sftp = await conn.start_sftp_client()
    try:
        await sftp.mkdir("/tmp/me-standalone")
    except Exception:
        pass
    local_root = root / ".test" / "me"
    files = sorted(p for p in local_root.rglob("*") if p.is_file())
    print("files:", len(files), flush=True)
    total = 0
    for index, path in enumerate(files):
        remote = "/tmp/me-standalone/" + path.relative_to(local_root).as_posix()
        try:
            await sftp.put(str(path), remote,
                           block_size=65536, max_requests=8)
            total += path.stat().st_size
            print(f"  ok {index}: {path.name} ({path.stat().st_size})", flush=True)
        except Exception as exc:
            print(f"  FAIL {index}: {path.name}: {type(exc).__name__}: {exc}", flush=True)
            break
    print("uploaded bytes:", total, flush=True)
    conn.close()


asyncio.run(main())
