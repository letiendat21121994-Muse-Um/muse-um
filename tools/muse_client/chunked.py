"""Chunked file transfer (PROTOCOL.md §7).

- Upload: 256 KiB raw chunks (``CHUNK_SIZE``), base64 per chunk
  (~342 KiB, under the 1 MB frame limit), SHA-256 over the whole file.
- Read-back: repeated ``read_file`` byte-range calls, concatenated
  client-side, SHA-256 verified.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from pathlib import Path

from .client import RelayClient, RelayError

CHUNK_SIZE = 256 * 1024  # 256 KiB


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def split_bytes(data: bytes, chunk_size: int = CHUNK_SIZE) -> list[bytes]:
    """Split bytes into ``chunk_size`` pieces (last one may be shorter)."""
    return [
        data[i : i + chunk_size] for i in range(0, len(data), chunk_size)
    ] or [b""]


def iter_file_chunks(path: str | Path, chunk_size: int = CHUNK_SIZE):
    """Yield ``(index, bytes)`` chunks of a file."""
    with open(path, "rb") as fh:
        index = 0
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            yield index, block
            index += 1


def total_chunks_for(size: int, chunk_size: int = CHUNK_SIZE) -> int:
    if size == 0:
        return 0
    return (size + chunk_size - 1) // chunk_size


async def upload_chunked(
    client: RelayClient,
    local_path: str | Path,
    remote_path: str,
    mode: str,
    timeout: float = 180.0,
) -> dict:
    """Upload a file via chunked_upload_start/chunk/end (§7).

    Returns the Panel's ``invoke_result`` for the upload.
    """
    local_path = Path(local_path)
    total_size = local_path.stat().st_size
    total_chunks = total_chunks_for(total_size)
    sha256 = sha256_file(local_path)
    upload_id = "upl_" + secrets.token_hex(8)

    await client.send_message(
        "chunked_upload_start",
        upload_id=upload_id,
        tool="write_file",
        args={"path": remote_path, "mode": mode},
        total_chunks=total_chunks,
        total_size=total_size,
        sha256=sha256,
    )
    for index, block in iter_file_chunks(local_path):
        await client.send_message(
            "chunked_upload_chunk",
            upload_id=upload_id,
            index=index,
            data_base64=base64.b64encode(block).decode("ascii"),
        )
    await client.send_message("chunked_upload_end", upload_id=upload_id)
    result = await client.wait_invoke_result(timeout=timeout)
    if result.get("invoke_id") not in (None, upload_id) and result.get(
        "upload_id"
    ) not in (None, upload_id):
        # Result for a different upload; still return it, caller decides.
        pass
    return result


def _decode_read_result(result: dict) -> bytes:
    """Extract bytes from a read_file invoke result.

    Accepts ``content``/``data`` as str (text or base64) or bytes,
    honouring an ``encoding`` field of ``"base64"``.
    """
    payload = result.get("result", result)
    if isinstance(payload, dict):
        content = payload.get("content", payload.get("data", ""))
        encoding = payload.get("encoding", "text")
    else:
        content, encoding = payload, "text"
    if isinstance(content, bytes):
        return content
    if encoding == "base64":
        return base64.b64decode(content)
    return str(content).encode("utf-8")


async def read_file_full(
    client: RelayClient,
    path: str,
    expected_sha256: str | None = None,
    chunk_length: int = CHUNK_SIZE,
    root_id: str | None = None,
) -> bytes:
    """Read a whole file via byte-range ``read_file`` invokes.

    Concatenates the ranges and, if ``expected_sha256`` is given,
    verifies the SHA-256 of the result (raises ValueError on mismatch).
    """
    data = bytearray()
    offset = 0
    while True:
        result = await client.invoke(
            "read_file",
            {"path": path, "offset": offset, "length": chunk_length,
             "encoding": "base64"},
            root_id=root_id,
        )
        block = _decode_read_result(result)
        if not block:
            break
        data.extend(block)
        offset += len(block)
        if len(block) < chunk_length:
            break
    out = bytes(data)
    if expected_sha256 is not None:
        actual = sha256_bytes(out)
        if actual != expected_sha256:
            raise ValueError(
                f"SHA-256 mismatch for {path}: expected {expected_sha256}, "
                f"got {actual}"
            )
    return out
