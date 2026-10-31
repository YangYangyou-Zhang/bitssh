import asyncio
import posixpath
import pathlib
import sys

import asyncssh

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from backend.ssh import algs, sftp_service as s


async def main() -> None:
    conn = await asyncssh.connect(
        "127.0.0.1", port=2222, username="root", known_hosts=None,
        client_keys=[r"tests\legacy_rsa"],
        kex_algs=algs.KEX_ALGS, server_host_key_algs=algs.SERVER_HOST_KEY_ALGS,
    )
    sftp = await s.sftp_client(conn)
    root = "/tmp/rmtree-debug"

    try:
        await sftp.mkdir(root)
    except Exception:
        pass
    try:
        await sftp.mkdir(root + "/sub")
    except Exception:
        pass
    for path, data in ((root + "/a.txt", b"A"), (root + "/sub/b.txt", b"BB")):
        async with sftp.open(path, "wb") as handle:
            await handle.write(data)

    print("scandir root:", end=" ", flush=True)
    entries = []
    async for entry in sftp.scandir(root):
        entries.append((entry.filename, entry.attrs.type))
    print(entries, flush=True)

    print("service remove start", flush=True)
    await asyncio.wait_for(s.remove(conn, root, True), 10)
    print("service remove done", flush=True)
    conn.close()


asyncio.run(main())
