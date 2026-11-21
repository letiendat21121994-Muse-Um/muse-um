"""Local state-directory helpers (PROTOCOL.md §10).

State dir: ``~/.cache/muse-um/`` or ``$MUSE_UM_STATE_DIR``.
Sensitive files (token-key, account.token, root-id) are written 0600.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

STATE_DIR_ENV = "MUSE_UM_STATE_DIR"

ACCOUNT_TOKEN_FILE = "account.token"
TOKEN_KEY_FILE = "token-key"
SESSION_FILE = "session.json"
ROOM_QUEUE_FILE = "room-queue.jsonl"
DAEMON_SOCK_FILE = "daemon.sock"
DAEMON_PID_FILE = "daemon.pid"
DAEMON_LOG_FILE = "daemon.log"
DAEMON_STATUS_FILE = "daemon-status.json"
ROOT_ID_FILE = "root-id"


def get_state_dir() -> Path:
    raw = os.environ.get(STATE_DIR_ENV)
    path = Path(raw).expanduser() if raw else Path.home() / ".cache" / "muse-um"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(name: str, state_dir: Path | None = None) -> Path:
    return (state_dir or get_state_dir()) / name


def write_private(path: Path, data: bytes) -> None:
    """Write bytes with 0600 permissions (best effort on chmod)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with restrictive mode from the start where possible.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def write_text_private(path: Path, text: str) -> None:
    write_private(path, text.encode("utf-8"))


def load_session(state_dir: Path | None = None) -> dict | None:
    path = state_path(SESSION_FILE, state_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_session(session: dict, state_dir: Path | None = None) -> Path:
    path = state_path(SESSION_FILE, state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=2), encoding="utf-8")
    return path


def load_root_id(state_dir: Path | None = None) -> str | None:
    path = state_path(ROOT_ID_FILE, state_dir)
    if not path.exists():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def save_root_id(root_id: str, state_dir: Path | None = None) -> Path:
    path = state_path(ROOT_ID_FILE, state_dir)
    write_text_private(path, root_id.strip() + "\n")
    return path


def read_queue(state_dir: Path | None = None) -> list[dict]:
    """All messages in room-queue.jsonl, in file (chronological) order."""
    path = state_path(ROOM_QUEUE_FILE, state_dir)
    messages: list[dict] = []
    if not path.exists():
        return messages
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except ValueError:
            continue
    return messages


def append_queue(message: dict, state_dir: Path | None = None) -> None:
    path = state_path(ROOM_QUEUE_FILE, state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(message, ensure_ascii=False) + "\n")
