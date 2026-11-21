"""Relay configuration.

Reads the Relay bind settings from the environment (PROTOCOL.md §0):

- ``RELAY_HOST``            — bind address, default ``127.0.0.1``.
- ``RELAY_PORT``            — port, default ``8000``.
- ``RELAY_ALLOW_PUBLIC_BIND`` — must be ``1``/``true`` to allow binding
  ``0.0.0.0``; default ``False``.

This module is check layer 1 of 2: :func:`load_config` refuses a public
bind without the flag. ``relay/main.py`` repeats the same check
independently before starting the server (layer 2), so a public bind can
never start by accident even if this module's check is bypassed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

#: Values accepted as "true" for RELAY_ALLOW_PUBLIC_BIND (PROTOCOL.md §0).
_TRUE_VALUES = {"1", "true"}


@dataclass(frozen=True)
class RelayConfig:
    """Resolved Relay configuration."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    allow_public_bind: bool = False


def validate_public_bind(host: str, allow_public_bind: bool) -> None:
    """Refuse to bind ``0.0.0.0`` unless the public-bind flag is set.

    Raises:
        RuntimeError: if ``host`` is ``0.0.0.0`` and ``allow_public_bind``
            is false.
    """
    if host == "0.0.0.0" and not allow_public_bind:
        raise RuntimeError(
            "Refusing to bind RELAY_HOST=0.0.0.0 without "
            "RELAY_ALLOW_PUBLIC_BIND=1. Binding 0.0.0.0 exposes the Relay "
            "on every network interface of this machine; set "
            "RELAY_ALLOW_PUBLIC_BIND=1 explicitly if that is intended, "
            "or bind a specific address (e.g. 127.0.0.1 or a Tailscale IP)."
        )


def load_config(env: dict | None = None) -> RelayConfig:
    """Load and validate the Relay configuration from the environment.

    Args:
        env: mapping to read from (defaults to ``os.environ``); injectable
            for tests.

    Raises:
        ValueError: if ``RELAY_PORT`` is not a valid TCP port number.
        RuntimeError: if ``RELAY_HOST=0.0.0.0`` without
            ``RELAY_ALLOW_PUBLIC_BIND=1``/``true``.
    """
    if env is None:
        env = os.environ
    host = (env.get("RELAY_HOST") or DEFAULT_HOST).strip()
    port_raw = (env.get("RELAY_PORT") or str(DEFAULT_PORT)).strip()
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError(f"Invalid RELAY_PORT {port_raw!r}: not an integer") from exc
    if not 0 < port < 65536:
        raise ValueError(f"Invalid RELAY_PORT {port_raw!r}: out of range 1-65535")
    allow_public_bind = (env.get("RELAY_ALLOW_PUBLIC_BIND") or "").strip().lower() in _TRUE_VALUES
    validate_public_bind(host, allow_public_bind)
    return RelayConfig(host=host, port=port, allow_public_bind=allow_public_bind)


# Backwards-friendly alias.
get_config = load_config
