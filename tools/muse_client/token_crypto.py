"""Fernet encryption for the stored login token (PROTOCOL.md §10, §15).

- Key lives in ``token-key`` (0600).
- Encrypted token lives in ``account.token`` (0600).
- The token value is NEVER printed or logged anywhere in this package.
"""
from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .state import (
    ACCOUNT_TOKEN_FILE,
    TOKEN_KEY_FILE,
    state_path,
    write_private,
)


def get_or_create_key(state_dir: Path | None = None) -> bytes:
    """Return the Fernet key, creating and storing it (0600) if absent."""
    key_path = state_path(TOKEN_KEY_FILE, state_dir)
    if key_path.exists():
        key = key_path.read_bytes().strip()
        if key:
            return key
    key = Fernet.generate_key()
    write_private(key_path, key + b"\n")
    return key


def encrypt_token(token: str, state_dir: Path | None = None) -> bytes:
    fernet = Fernet(get_or_create_key(state_dir))
    return fernet.encrypt(token.encode("utf-8"))


def decrypt_token(data: bytes | str, state_dir: Path | None = None) -> str:
    """Decrypt an encrypted token. Raises ValueError on bad data/key."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    fernet = Fernet(get_or_create_key(state_dir))
    try:
        return fernet.decrypt(data.strip()).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("stored token cannot be decrypted (bad key/data)") from exc


def save_token(token: str, state_dir: Path | None = None) -> Path:
    path = state_path(ACCOUNT_TOKEN_FILE, state_dir)
    write_private(path, encrypt_token(token, state_dir) + b"\n")
    return path


def load_token(state_dir: Path | None = None) -> str | None:
    """Return the decrypted saved token, or None if none is stored."""
    path = state_path(ACCOUNT_TOKEN_FILE, state_dir)
    if not path.exists():
        return None
    try:
        return decrypt_token(path.read_bytes(), state_dir)
    except (OSError, ValueError):
        return None


def has_saved_token(state_dir: Path | None = None) -> bool:
    return state_path(ACCOUNT_TOKEN_FILE, state_dir).exists()
