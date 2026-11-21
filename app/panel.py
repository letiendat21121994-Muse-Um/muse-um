"""muse-um Panel daemon.

Connects to the Relay over WebSocket, registers a file-access root with
``hello`` and executes the 14 invoke tools inside the sandbox
(``MUSE_UM_ROOT_DIR``, default ``<repo>/demo-root``).

Implements PROTOCOL.md sections 2, 5, 6, 7 and 8:
- ``hello`` {root_dir, name} -> {root_id}; root_id is kept in RAM only,
  never logged and never written to disk.
- ``invoke_request`` -> ``invoke_result`` {invoke_id, ok, result, error}.
  Errors also carry a ``code`` field (``FORBIDDEN`` / ``BAD_MESSAGE``)
  next to the human-readable ``error`` string.
- Chunked upload: ``chunked_upload_start`` / ``chunked_upload_chunk`` /
  ``chunked_upload_end`` (256 KiB chunks, SHA-256 verified).
- Auto-reconnect with back-off; a new hello mints a new root_id, which is
  logged only as "root re-registered (root_id changed)", never its value.

Usage:
    MUSE_UM_RELAY_URL=http://127.0.0.1:8000 \
    MUSE_UM_ROOT_DIR=/path/to/root \
        .venv/bin/python app/panel.py
    # or: .venv/bin/python -m app.panel   (from the repo root)

``MUSE_UM_RELAY_URL`` is REQUIRED; if unset the process exits with a
clear error instead of silently defaulting to localhost.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import websockets
from websockets.exceptions import ConnectionClosed

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT_DIR = REPO_ROOT / "demo-root"
MAX_FRAME = 1_048_576
CHUNK_SEARCH_LIMIT = 200          # max search matches returned per invoke
ORGANISE_FILE_LIMIT = 2000        # max files walked by organise_preview
RUN_COMMAND_SHORT_WAIT = 5.0      # seconds before a command is "running"
RECONNECT_BACKOFF_MAX = 30.0

log = logging.getLogger("muse-um.panel")

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ToolError(Exception):
    """An invoke failure. ``code`` mirrors the PROTOCOL.md error codes."""

    def __init__(self, message: str, code: str = "BAD_MESSAGE") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class SandboxError(ToolError):
    def __init__(self) -> None:
        super().__init__("path escapes sandbox", code="FORBIDDEN")


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


class Sandbox:
    """All tool paths must stay inside ``root_dir`` (PROTOCOL.md section 8)."""

    def __init__(self, root_dir: Path) -> None:
        self.root = root_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, raw_path) -> Path:
        """Resolve ``raw_path`` (relative, inside the sandbox) to a Path.

        Rejects absolute paths (POSIX and Windows drive forms) and any
        path containing a ``..`` component. After resolving symlinks the
        target must still be inside ``root_dir``.
        """
        if raw_path is None:
            raise ToolError("missing required argument: 'path'")
        if not isinstance(raw_path, str):
            raise ToolError("'path' must be a string")
        if "\x00" in raw_path:
            raise SandboxError()
        # Treat both separators as separators when looking for escapes,
        # so a Windows-style " ..\\..\\x" cannot slip through on Linux.
        unified = raw_path.replace("\\", "/")
        if unified.startswith("/") or re.match(r"^[A-Za-z]:", unified):
            raise SandboxError()
        parts = [seg for seg in unified.split("/") if seg not in ("", ".")]
        if ".." in parts:
            raise SandboxError()
        target = self.root.joinpath(*parts) if parts else self.root
        resolved = target.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise SandboxError()
        return resolved

    def rel(self, path: Path) -> str:
        """Sandbox-relative display form of a resolved path ('.' for root)."""
        try:
            return str(path.resolve().relative_to(self.root))
        except ValueError:
            return "."


# ---------------------------------------------------------------------------
# Tool implementations (all sync except run_command)
# ---------------------------------------------------------------------------

TOOLS = (
    "list_dir", "read_file", "file_info", "search", "write_file",
    "edit_file", "create_dir", "copy_file", "move_file", "rename",
    "delete_file", "run_command", "cancel_command", "organise_preview",
)


def _arg(args: dict, *names, default=None, required: bool = False):
    for name in names:
        if name in args and args[name] is not None:
            return args[name]
    if required:
        raise ToolError(
            "missing required argument: '%s'" % (names[0],))
    return default


def tool_list_dir(sb: Sandbox, args: dict) -> dict:
    target = sb.resolve(_arg(args, "path", default="."))
    if not target.is_dir():
        raise ToolError("not a directory: '%s'" % sb.rel(target))
    entries = []
    for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        info = {"name": entry.name,
                "type": "dir" if entry.is_dir() else "file"}
        try:
            st = entry.stat()
            info["mtime"] = st.st_mtime
            if entry.is_file():
                info["size"] = st.st_size
        except OSError:
            pass
        entries.append(info)
    return {"path": sb.rel(target), "entries": entries}


def tool_read_file(sb: Sandbox, args: dict) -> dict:
    target = sb.resolve(_arg(args, "path", required=True))
    if not target.is_file():
        raise ToolError("file not found: '%s'" % sb.rel(target))
    offset = int(_arg(args, "offset", default=0) or 0)
    length = _arg(args, "length", default=None)
    if offset < 0:
        raise ToolError("'offset' must be >= 0")
    if length is not None:
        length = int(length)
        if length < 0:
            raise ToolError("'length' must be >= 0")
    encoding = _arg(args, "encoding", default="text")
    if encoding not in ("text", "base64"):
        raise ToolError("'encoding' must be 'text' or 'base64'")
    data = target.read_bytes()
    total = len(data)
    chunk = data[offset:offset + length if length is not None else None]
    if encoding == "base64":
        content = base64.b64encode(chunk).decode("ascii")
    else:
        try:
            content = chunk.decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError(
                "file is not valid UTF-8; use encoding 'base64'")
    return {"path": sb.rel(target), "offset": offset,
            "length": len(chunk), "total_size": total,
            "encoding": encoding, "content": content}


def tool_file_info(sb: Sandbox, args: dict) -> dict:
    target = sb.resolve(_arg(args, "path", required=True))
    if not target.exists():
        raise ToolError("path not found: '%s'" % sb.rel(target))
    st = target.stat()
    return {"path": sb.rel(target),
            "type": "dir" if target.is_dir() else "file",
            "size": st.st_size if target.is_file() else None,
            "mtime": st.st_mtime}


def tool_search(sb: Sandbox, args: dict) -> dict:
    query = _arg(args, "query", "pattern", "needle", "text", required=True)
    if not isinstance(query, str) or query == "":
        raise ToolError("'query' must be a non-empty string")
    case_sensitive = bool(_arg(args, "case_sensitive", default=True))
    recursive = bool(_arg(args, "recursive", default=False))
    target = sb.resolve(_arg(args, "path", default="."))
    files: list[Path] = []
    if target.is_file():
        files = [target]
    elif target.is_dir():
        if recursive:
            files = [p for p in target.rglob("*") if p.is_file()]
        else:
            files = [p for p in target.iterdir() if p.is_file()]
    else:
        raise ToolError("path not found: '%s'" % sb.rel(target))
    needle = query if case_sensitive else query.lower()
    matches: list[dict] = []
    truncated = False
    for f in sorted(files):
        # A symlinked file inside the root may point outside: skip it.
        try:
            resolved = f.resolve()
        except OSError:
            continue
        if resolved != sb.root and sb.root not in resolved.parents:
            continue
        try:
            data = f.read_bytes()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue  # binary file
        text = data.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            hay = line if case_sensitive else line.lower()
            if needle in hay:
                matches.append({"path": sb.rel(f), "line": lineno,
                                "text": line})
                if len(matches) >= CHUNK_SEARCH_LIMIT:
                    truncated = True
                    break
        if truncated:
            break
    return {"query": query, "matches": matches, "truncated": truncated}


def _write_file_bytes(sb: Sandbox, args: dict, data: bytes) -> dict:
    mode = _arg(args, "mode", required=True)
    if mode not in ("create", "overwrite", "append"):
        raise ToolError(
            "write_file requires 'mode': 'create' | 'overwrite' | 'append'")
    target = sb.resolve(_arg(args, "path", required=True))
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "create" and target.exists():
        raise ToolError("file already exists: '%s'" % sb.rel(target))
    if mode == "append":
        with target.open("ab") as fh:
            fh.write(data)
    else:
        with target.open("wb") as fh:
            fh.write(data)
    return {"path": sb.rel(target), "size": target.stat().st_size,
            "mode": mode}


def tool_write_file(sb: Sandbox, args: dict) -> dict:
    if "content_base64" in args and args["content_base64"] is not None:
        try:
            data = base64.b64decode(args["content_base64"], validate=True)
        except Exception:
            raise ToolError("'content_base64' is not valid base64")
    elif "content" in args and args["content"] is not None:
        content = args["content"]
        if not isinstance(content, str):
            raise ToolError("'content' must be a string")
        data = content.encode("utf-8")
    else:
        raise ToolError("write_file requires 'content' or 'content_base64'")
    return _write_file_bytes(sb, args, data)


def tool_edit_file(sb: Sandbox, args: dict) -> dict:
    old = _arg(args, "old", "old_string", "old_text", required=True)
    new = _arg(args, "new", "new_string", "new_text", required=True)
    if not isinstance(old, str) or not isinstance(new, str):
        raise ToolError("'old' and 'new' must be strings")
    target = sb.resolve(_arg(args, "path", required=True))
    if not target.is_file():
        raise ToolError("file not found: '%s'" % sb.rel(target))
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ToolError("file is not valid UTF-8")
    count = text.count(old)
    if count == 0:
        raise ToolError("old string not found in '%s'" % sb.rel(target))
    if count > 1:
        raise ToolError(
            "old string occurs %d times in '%s'; edit_file requires "
            "exactly one occurrence" % (count, sb.rel(target)))
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return {"path": sb.rel(target), "replaced": 1}


def tool_create_dir(sb: Sandbox, args: dict) -> dict:
    target = sb.resolve(_arg(args, "path", required=True))
    target.mkdir(parents=True, exist_ok=True)
    return {"path": sb.rel(target), "created": True}


def _two_paths(sb: Sandbox, args: dict) -> tuple[Path, Path]:
    src = sb.resolve(_arg(args, "src", "source", "path", required=True))
    dest = sb.resolve(_arg(args, "dest", "destination", "to", required=True))
    return src, dest


def tool_copy_file(sb: Sandbox, args: dict) -> dict:
    src, dest = _two_paths(sb, args)
    if not src.is_file():
        raise ToolError("source file not found: '%s'" % sb.rel(src))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return {"src": sb.rel(src), "dest": sb.rel(dest)}


def tool_move_file(sb: Sandbox, args: dict) -> dict:
    src, dest = _two_paths(sb, args)
    if not src.exists():
        raise ToolError("source not found: '%s'" % sb.rel(src))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return {"src": sb.rel(src), "dest": sb.rel(dest)}


def tool_rename(sb: Sandbox, args: dict) -> dict:
    src = sb.resolve(_arg(args, "src", "source", "path", required=True))
    new_name = _arg(args, "new_name", "newName", "name")
    if new_name is not None:
        if not isinstance(new_name, str) or new_name == "":
            raise ToolError("'new_name' must be a non-empty string")
        dest = sb.resolve(str(Path(sb.rel(src)).parent / new_name)
                          if sb.rel(src) != "." else new_name)
        # The check above resolves against the sandbox; a ".." inside
        # new_name is rejected by Sandbox.resolve itself.
    else:
        dest = sb.resolve(_arg(args, "dest", "destination", "to",
                               required=True))
    if not src.exists():
        raise ToolError("path not found: '%s'" % sb.rel(src))
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
    return {"src": sb.rel(src), "dest": sb.rel(dest)}


def tool_delete_file(sb: Sandbox, args: dict) -> dict:
    target = sb.resolve(_arg(args, "path", required=True))
    if not target.exists() and not target.is_symlink():
        raise ToolError("path not found: '%s'" % sb.rel(target))
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        try:
            target.rmdir()  # only empty directories
        except OSError:
            raise ToolError(
                "directory is not empty: '%s'" % sb.rel(target))
    return {"path": sb.rel(target), "deleted": True}


def tool_organise_preview(sb: Sandbox, args: dict) -> dict:
    target = sb.resolve(_arg(args, "path", default="."))
    if not target.is_dir():
        raise ToolError("not a directory: '%s'" % sb.rel(target))
    groups: dict[str, list[str]] = {}
    total = 0
    truncated = False
    for f in sorted(target.rglob("*")):
        if not f.is_file():
            continue
        try:
            resolved = f.resolve()
        except OSError:
            continue
        if resolved != sb.root and sb.root not in resolved.parents:
            continue
        total += 1
        if total > ORGANISE_FILE_LIMIT:
            truncated = True
            break
        ext = f.suffix.lower() or "no_extension"
        groups.setdefault(ext, []).append(sb.rel(f))
    # Read-only: this tool never creates, moves or deletes anything.
    return {"path": sb.rel(target), "total_files": total,
            "groups": groups, "truncated": truncated}


# ---------------------------------------------------------------------------
# run_command / cancel_command (async; tracked processes)
# ---------------------------------------------------------------------------


class CommandRunner:
    """Tracks running commands so cancel_command can kill them."""

    def __init__(self) -> None:
        self.running: dict[str, asyncio.subprocess.Process] = {}
        self.finished: dict[str, dict] = {}

    async def run(self, args: dict) -> dict:
        if "command" not in args:
            # Poll a previous run: run_command {id} with no "command"
            # returns the stored finished result, or "running" while
            # the command is still going. (PROTOCOL.md §6.)
            cmd_id = _arg(args, "id", required=True)
            if cmd_id in self.finished:
                return dict(self.finished[cmd_id])
            if cmd_id in self.running:
                return {"id": cmd_id, "status": "running"}
            raise ToolError("command not found: '%s'" % cmd_id)
        command = _arg(args, "command", required=True)
        if not isinstance(command, str) or command == "":
            raise ToolError("'command' must be a non-empty string")
        cmd_id = _arg(args, "id") or ("cmd_" + secrets.token_hex(8))
        if not isinstance(cmd_id, str):
            raise ToolError("'id' must be a string")
        if cmd_id in self.running:
            raise ToolError("command id already running: '%s'" % cmd_id)
        if cmd_id in self.finished and "command" in args:
            # A fresh run reuses the id: drop the stale finished result.
            del self.finished[cmd_id]
        if sys.platform.startswith("win"):
            argv = ["powershell", "-NoProfile", "-Command", command]
        else:
            argv = ["sh", "-c", command]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True)
        self.running[cmd_id] = proc
        try:
            await asyncio.wait_for(proc.wait(), timeout=RUN_COMMAND_SHORT_WAIT)
        except asyncio.TimeoutError:
            # Long command: return immediately; it keeps running.
            asyncio.create_task(self._reap(cmd_id, proc))
            return {"id": cmd_id, "status": "running"}
        result = await self._collect(cmd_id, proc)
        return result

    async def _reap(self, cmd_id: str, proc) -> None:
        await proc.wait()
        await self._collect(cmd_id, proc)

    async def _collect(self, cmd_id: str, proc) -> dict:
        stdout, stderr = await proc.communicate()
        self.running.pop(cmd_id, None)
        result = {"id": cmd_id, "status": "finished",
                  "stdout": stdout.decode("utf-8", errors="replace"),
                  "stderr": stderr.decode("utf-8", errors="replace"),
                  "returncode": proc.returncode}
        # A cancel that landed first wins; do not overwrite "cancelled".
        if self.finished.get(cmd_id, {}).get("status") != "cancelled":
            self.finished[cmd_id] = result
        return result

    async def cancel(self, args: dict) -> dict:
        cmd_id = _arg(args, "id", required=True)
        proc = self.running.get(cmd_id)
        if proc is None:
            if cmd_id in self.finished:
                return {"id": cmd_id, "status": "finished",
                        "detail": "command already finished"}
            raise ToolError("command not found: '%s'" % cmd_id)
        try:
            import os as _os
            if not sys.platform.startswith("win"):
                try:
                    _os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        self.running.pop(cmd_id, None)
        self.finished[cmd_id] = {"id": cmd_id, "status": "cancelled",
                                 "stdout": "", "stderr": "",
                                 "returncode": proc.returncode}
        return {"id": cmd_id, "status": "cancelled"}


SYNC_TOOLS = {
    "list_dir": tool_list_dir,
    "read_file": tool_read_file,
    "file_info": tool_file_info,
    "search": tool_search,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "create_dir": tool_create_dir,
    "copy_file": tool_copy_file,
    "move_file": tool_move_file,
    "rename": tool_rename,
    "delete_file": tool_delete_file,
    "organise_preview": tool_organise_preview,
}


# ---------------------------------------------------------------------------
# Panel session: hello, invoke dispatch, chunked uploads
# ---------------------------------------------------------------------------


class PanelSession:
    def __init__(self, ws, sandbox: Sandbox, name: str) -> None:
        self.ws = ws
        self.sb = sandbox
        self.name = name
        self.root_id: str | None = None  # RAM only, never logged/written
        self.runner = CommandRunner()
        self.uploads: dict[str, dict] = {}
        self._send_lock = asyncio.Lock()

    async def hello(self) -> str:
        hello_id = "hello_" + secrets.token_hex(8)
        await self.ws.send(json.dumps({
            "type": "hello",
            "id": hello_id,
            "root_dir": str(self.sb.root),
            "name": self.name,
        }))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=15.0)
            msg = json.loads(raw)
            if msg.get("type") == "hello_result":
                root_id = msg.get("root_id")
                if not root_id:
                    raise RuntimeError("hello_result without root_id")
                self.root_id = root_id
                return root_id
            if msg.get("type") == "error":
                raise RuntimeError("hello failed: %s" % msg.get("message"))
            # Ignore any other push while waiting for hello_result.

    async def send(self, payload: dict) -> None:
        async with self._send_lock:
            await self.ws.send(json.dumps(payload))

    async def send_invoke_result(self, invoke_id: str, ok: bool,
                                 result=None, error: str | None = None,
                                 code: str | None = None) -> None:
        payload = {"type": "invoke_result", "id": invoke_id,
                   "invoke_id": invoke_id, "ok": ok, "result": result,
                   "error": error}
        if code:
            payload["code"] = code
        await self.send(payload)

    async def execute_tool(self, tool: str, args: dict) -> dict:
        if tool == "run_command":
            return await self.runner.run(args)
        if tool == "cancel_command":
            return await self.runner.cancel(args)
        fn = SYNC_TOOLS.get(tool)
        if fn is None:
            raise ToolError("unknown tool: '%s'" % tool)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, fn, self.sb, args)

    async def handle_invoke(self, msg: dict) -> None:
        invoke_id = msg.get("invoke_id")
        tool = msg.get("tool")
        args = msg.get("args") or {}
        if not invoke_id or not tool:
            log.warning("invoke_request without invoke_id/tool; ignored")
            return
        if not isinstance(args, dict):
            args = {}
        try:
            result = await self.execute_tool(tool, args)
        except ToolError as exc:
            log.info("invoke %s -> error %s: %s", tool, exc.code, exc.message)
            await self.send_invoke_result(invoke_id, False, None,
                                          exc.message, exc.code)
        except Exception as exc:  # never crash the loop on a bad tool call
            log.exception("invoke %s failed unexpectedly", tool)
            await self.send_invoke_result(invoke_id, False, None, str(exc))
        else:
            log.info("invoke %s -> ok", tool)
            await self.send_invoke_result(invoke_id, True, result, None)

    # -- chunked upload (PROTOCOL.md section 7) ---------------------------

    async def handle_chunked_start(self, msg: dict) -> None:
        upload_id = msg.get("upload_id")
        if not upload_id:
            log.warning("chunked_upload_start without upload_id; ignored")
            return
        args = msg.get("args") or {}
        try:
            if msg.get("tool", "write_file") != "write_file":
                raise ToolError("chunked upload only supports write_file")
            mode = args.get("mode")
            if mode not in ("create", "overwrite", "append"):
                raise ToolError(
                    "write_file requires 'mode': 'create' | 'overwrite' | "
                    "'append'")
            target = self.sb.resolve(args.get("path"))
            total_chunks = int(msg.get("total_chunks", 0))
            total_size = int(msg.get("total_size", 0))
            if total_chunks <= 0 or total_size < 0:
                raise ToolError("invalid total_chunks/total_size")
            # Validate the destination now (incl. create-mode existence).
            if mode == "create" and target.exists():
                raise ToolError(
                    "file already exists: '%s'" % self.sb.rel(target))
        except ToolError as exc:
            log.info("chunked upload %s -> error %s: %s",
                     upload_id, exc.code, exc.message)
            await self.send_invoke_result(upload_id, False, None,
                                          exc.message, exc.code)
            return
        self.uploads[upload_id] = {
            "args": args, "total_chunks": total_chunks,
            "total_size": total_size, "sha256": msg.get("sha256"),
            "chunks": {},
        }
        log.info("chunked upload %s started (%d chunks)",
                 upload_id, total_chunks)

    async def handle_chunked_chunk(self, msg: dict) -> None:
        upload = self.uploads.get(msg.get("upload_id"))
        if upload is None:
            log.warning("chunk for unknown upload_id; ignored")
            return
        try:
            index = int(msg.get("index"))
            data = base64.b64decode(msg.get("data_base64", ""),
                                    validate=True)
        except Exception:
            log.warning("bad chunk for upload %s; ignored",
                        msg.get("upload_id"))
            return
        upload["chunks"][index] = data

    async def handle_chunked_end(self, msg: dict) -> None:
        upload_id = msg.get("upload_id")
        upload = self.uploads.pop(upload_id, None)
        if upload is None:
            await self.send_invoke_result(
                upload_id, False, None,
                "unknown upload_id: '%s'" % upload_id)
            return
        try:
            chunks = upload["chunks"]
            if len(chunks) != upload["total_chunks"]:
                raise ToolError(
                    "missing chunks: got %d of %d"
                    % (len(chunks), upload["total_chunks"]))
            data = b"".join(chunks[i]
                            for i in range(upload["total_chunks"]))
            if len(data) != upload["total_size"]:
                raise ToolError(
                    "size mismatch: got %d bytes, expected %d"
                    % (len(data), upload["total_size"]))
            digest = hashlib.sha256(data).hexdigest()
            if upload["sha256"] and digest != upload["sha256"]:
                raise ToolError("sha256 mismatch")
            result = await asyncio.get_running_loop().run_in_executor(
                None, _write_file_bytes, self.sb, upload["args"], data)
        except ToolError as exc:
            log.info("chunked upload %s -> error %s: %s",
                     upload_id, exc.code, exc.message)
            await self.send_invoke_result(upload_id, False, None,
                                          exc.message, exc.code)
        except Exception as exc:
            log.exception("chunked upload %s failed", upload_id)
            await self.send_invoke_result(upload_id, False, None, str(exc))
        else:
            log.info("chunked upload %s -> ok (%d bytes)",
                     upload_id, len(data))
            await self.send_invoke_result(upload_id, True, result, None)

    async def run(self) -> None:
        async for raw in self.ws:
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                log.warning("non-JSON message from Relay; ignored")
                continue
            mtype = msg.get("type")
            if mtype == "invoke_request":
                asyncio.create_task(self.handle_invoke(msg))
            elif mtype == "chunked_upload_start":
                asyncio.create_task(self.handle_chunked_start(msg))
            elif mtype == "chunked_upload_chunk":
                asyncio.create_task(self.handle_chunked_chunk(msg))
            elif mtype == "chunked_upload_end":
                asyncio.create_task(self.handle_chunked_end(msg))
            elif mtype == "error":
                log.warning("Relay error: %s", msg.get("message"))
            else:
                log.debug("ignoring Relay message type %r", mtype)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def http_to_ws(url: str) -> str:
    if url.startswith("http://"):
        return "ws://" + url[len("http://"):]
    if url.startswith("https://"):
        return "wss://" + url[len("https://"):]
    if url.startswith("ws://") or url.startswith("wss://"):
        return url
    raise ValueError(
        "MUSE_UM_RELAY_URL must start with http://, https://, ws:// "
        "or wss:// (got %r)" % url)


async def main_async() -> int:
    relay_url = os.environ.get("MUSE_UM_RELAY_URL")
    if not relay_url:
        print("error: MUSE_UM_RELAY_URL is not set\n"
              "Set it to the Relay HTTP(S) URL, e.g.\n"
              "  MUSE_UM_RELAY_URL=http://127.0.0.1:8000",
              file=sys.stderr)
        return 2
    try:
        ws_url = http_to_ws(relay_url)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    root_dir_env = os.environ.get("MUSE_UM_ROOT_DIR")
    root_dir = Path(root_dir_env) if root_dir_env else DEFAULT_ROOT_DIR
    sandbox = Sandbox(root_dir)
    name = os.environ.get("MUSE_UM_PANEL_NAME", "Panel")
    log.info("Panel starting: root_dir=%s relay=%s",
             sandbox.root, ws_url)

    backoff = 1.0
    ever_connected = False
    while True:
        try:
            async with websockets.connect(ws_url,
                                          max_size=MAX_FRAME) as ws:
                session = PanelSession(ws, sandbox, name)
                await session.hello()
                if ever_connected:
                    log.warning(
                        "Panel re-connected; root re-registered "
                        "(root_id changed)")
                else:
                    log.info("Panel connected; root registered")
                ever_connected = True
                backoff = 1.0
                await session.run()
                log.warning("Relay connection closed")
        except (ConnectionClosed, OSError, asyncio.TimeoutError,
                RuntimeError) as exc:
            log.warning("Relay connection lost (%s); retrying in %.0fs",
                        exc, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        log.info("Panel stopped by user")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
