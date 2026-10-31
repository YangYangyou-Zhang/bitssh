"""M0 spike: connect to a legacy SSH server using SHA-1 era algorithms.

Run:  python -m tests.spike_old_ssh --host 127.0.0.1 --port 2222 --user root --password xxx
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import asyncssh

KEX = [
    "diffie-hellman-group14-sha1",
    "diffie-hellman-group1-sha1",
    "diffie-hellman-group14-sha256",
    "curve25519-sha256",
]

HOSTKEY = [
    "ssh-rsa",
    "rsa-sha2-256",
    "rsa-sha2-512",
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
]


async def run(args: argparse.Namespace) -> int:
    kwargs = {
        "port": args.port,
        "username": args.user,
        "known_hosts": None,
        "kex_algs": KEX,
        "server_host_key_algs": HOSTKEY,
        "login_timeout": 10,
    }
    if args.key:
        kwargs["client_keys"] = [args.key]
    if args.password:
        kwargs["password"] = args.password
    kwargs["preferred_auth"] = "publickey,password,keyboard-interactive"

    conn = await asyncssh.connect(args.host, **kwargs)

    kex = getattr(conn, "_kex_algs", None) or conn.get_extra_info("kex_algs")
    host_key = getattr(conn, "_server_host_key", None) or conn.get_extra_info("server_host_key")
    print(f"kex_algs      : {kex}")
    if host_key is not None:
        print(f"server_host_key: {host_key.get_algorithm()} {host_key.get_fingerprint()}")
    print(f"auth_method   : {conn.get_extra_info('auth_method')}")

    proc = await conn.create_process(
        "sh", term_type="xterm-256color", term_size=(80, 24), encoding=None
    )
    proc.stdin.write(b"uname -a; echo CJK-\xe4\xb8\xad\xe6\x96\x87; exit\n")
    await proc.stdin.drain()
    out = b""
    while True:
        chunk = await proc.stdout.read(4096)
        if not chunk:
            break
        out += chunk
    print("--- remote output ---")
    print(out.decode("utf-8", "replace"))
    print("---------------------")

    conn.close()
    await conn.wait_closed()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default=None)
    parser.add_argument("--key", default=None, help="path to a private key file")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
