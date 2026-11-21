"""Local newline-delimited JSON transport: Unix sockets or Windows pipes."""
from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

from .state import DAEMON_SOCK_FILE

STREAM_LIMIT = 1_048_576


def pipe_name(state_dir: Path) -> str:
    digest = hashlib.sha256(str(state_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    return "\\\\.\\pipe\\muse-um-" + digest


def endpoint(state_dir: Path) -> str:
    return pipe_name(state_dir) if sys.platform == "win32" else str(state_dir / DAEMON_SOCK_FILE)


async def start_server(handler, state_dir: Path):
    loop = asyncio.get_running_loop()
    if sys.platform == "win32":
        def protocol_factory():
            reader = asyncio.StreamReader(limit=STREAM_LIMIT, loop=loop)
            return asyncio.StreamReaderProtocol(reader, handler, loop=loop)

        return await loop.start_serving_pipe(protocol_factory, endpoint(state_dir))
    path = state_dir / DAEMON_SOCK_FILE
    path.unlink(missing_ok=True)
    return await asyncio.start_unix_server(handler, path=str(path), limit=STREAM_LIMIT)


async def open_connection(state_dir: Path, timeout: float):
    async def connect():
        if sys.platform != "win32":
            return await asyncio.open_unix_connection(endpoint(state_dir), limit=STREAM_LIMIT)
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=STREAM_LIMIT, loop=loop)
        protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        transport, _ = await loop.create_pipe_connection(lambda: protocol, endpoint(state_dir))
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        return reader, writer

    return await asyncio.wait_for(connect(), timeout=timeout)


async def close_server(server, state_dir: Path) -> None:
    if sys.platform == "win32":
        for pipe_server in server:
            pipe_server.close()
    else:
        server.close()
        await server.wait_closed()
        (state_dir / DAEMON_SOCK_FILE).unlink(missing_ok=True)
