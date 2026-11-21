"""WebSocket client for the muse-um Relay (PROTOCOL.md §§0-7).

- Frame limit 1 MB (``max_size=1_048_576``).
- ``MUSE_UM_RELAY_URL`` is REQUIRED; never silently defaulted.
  The http(s) URL is converted to ws(s) by replacing the scheme.
- Proxy: ``websockets.connect`` honours the environment proxy by
  default (guide §5.3 — Relay is reached via the tunnel proxy);
  loopback hosts are bypassed via NO_PROXY. No proxy code is needed
  here; set ``MUSE_UM_PROXY`` only to override explicitly.
- Root-id mismatch (``BAD_MESSAGE`` with the exact message from §6)
  is parsed with a regex and ``invoke`` retries exactly once.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from pathlib import Path

import websockets
from websockets.asyncio.client import ClientConnection

from .state import get_state_dir, load_root_id, save_root_id

MAX_FRAME_SIZE = 1_048_576

RELAY_URL_ENV = "MUSE_UM_RELAY_URL"
PROXY_ENV = "MUSE_UM_PROXY"

# PROTOCOL.md §6 exact message:
#   root_id '<given>' does not match the open root '<current>'
ROOT_MISMATCH_RE = re.compile(
    r"root_id '([^']+)' does not match the open root '([^']+)'"
)


class RelayError(Exception):
    """An ``error`` message from the Relay."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class MissingRelayUrlError(Exception):
    """MUSE_UM_RELAY_URL is not set."""


def require_relay_url() -> str:
    url = os.environ.get(RELAY_URL_ENV, "").strip()
    if not url:
        raise MissingRelayUrlError(
            "MUSE_UM_RELAY_URL is not set. "
            "Set it to the Relay URL, e.g. "
            "MUSE_UM_RELAY_URL=http://<tailscale-ip>:8000"
        )
    return url


def relay_url_to_ws(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.startswith("https://"):
        return "wss://" + url[len("https://"):]
    if url.startswith("http://"):
        return "ws://" + url[len("http://"):]
    if url.startswith(("ws://", "wss://")):
        return url
    raise ValueError(f"unsupported relay URL scheme: {url!r}")


def parse_root_mismatch(message: str) -> tuple[str, str] | None:
    """Return (given_root, current_root) from a §6 BAD_MESSAGE, else None."""
    match = ROOT_MISMATCH_RE.search(message or "")
    if not match:
        return None
    return match.group(1), match.group(2)


class RelayClient:
    """One WebSocket connection to the Relay.

    Incoming messages are pumped by a background reader task into
    pending request futures (matched by ``id``); ``room_message``
    pushes go to ``on_room_message``.
    """

    def __init__(self, relay_url: str, state_dir: Path | None = None):
        self.relay_url = relay_url
        self.ws_url = relay_url_to_ws(relay_url)
        self.state_dir = state_dir or get_state_dir()
        self.ws: ClientConnection | None = None
        self.on_room_message = None  # callable(dict) or None
        self._pending: dict[str, asyncio.Future] = {}
        self._invoke_waiters: list[asyncio.Future] = []
        self._reader_task: asyncio.Task | None = None
        self._closed = False

    async def connect(self) -> None:
        kwargs: dict = {"max_size": MAX_FRAME_SIZE}
        proxy_override = os.environ.get(PROXY_ENV, "").strip()
        if proxy_override:
            kwargs["proxy"] = proxy_override
        try:
            self.ws = await websockets.connect(self.ws_url, **kwargs)
        except Exception as exc:
            raise RelayError(
                "APP_OFFLINE", f"Could not reach the Relay at {self.ws_url}: {exc}"
            ) from exc
        self._closed = False
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def close(self) -> None:
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        for fut in list(self._pending.values()) + self._invoke_waiters:
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        self._invoke_waiters.clear()

    async def __aenter__(self) -> "RelayClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    # -- incoming pump -------------------------------------------------
    async def _reader_loop(self) -> None:
        assert self.ws is not None
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            # Connection dropped: fail pending requests so callers notice.
            for fut in list(self._pending.values()) + self._invoke_waiters:
                if not fut.done():
                    fut.set_exception(
                        RelayError("APP_OFFLINE", "Relay connection closed")
                    )
            self._pending.clear()
            self._invoke_waiters.clear()

    def _dispatch(self, msg: dict) -> None:
        msg_id = msg.get("id")
        if msg.get("type") == "room_message":
            if self.on_room_message is not None:
                try:
                    self.on_room_message(msg)
                except Exception:
                    pass
            return
        if isinstance(msg_id, str) and msg_id in self._pending:
            fut = self._pending.pop(msg_id)
            if fut in self._invoke_waiters:
                self._invoke_waiters.remove(fut)
            if not fut.done():
                fut.set_result(msg)
            return
        if msg.get("type") == "invoke_result" and self._invoke_waiters:
            # Panel replies may not echo the request id; hand the result
            # to the oldest waiting invoke.
            fut = self._invoke_waiters.pop(0)
            if not fut.done():
                fut.set_result(msg)
            return
        # Anything else (unsolicited push) is ignored.

    # -- request/response ----------------------------------------------
    async def request(
        self, msg_type: str, timeout: float = 30.0, **fields
    ) -> dict:
        """Send one message and wait for its ``<type>_result`` / error.

        Returns the result dict. Raises RelayError on an error message
        or on timeout / dropped connection.
        """
        if self.ws is None:
            raise RelayError("APP_OFFLINE", "not connected to the Relay")
        req_id = "req_" + secrets.token_hex(8)
        payload = {"type": msg_type, "id": req_id, **fields}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        if msg_type == "invoke":
            self._invoke_waiters.append(fut)
        await self.ws.send(json.dumps(payload))
        try:
            msg = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            if fut in self._invoke_waiters:
                self._invoke_waiters.remove(fut)
            raise RelayError("APP_OFFLINE", f"timed out waiting for {msg_type}") from exc
        if fut in self._invoke_waiters:
            self._invoke_waiters.remove(fut)
        if msg.get("type") == "error":
            raise RelayError(
                str(msg.get("code", "BAD_MESSAGE")), str(msg.get("message", ""))
            )
        return msg

    async def send_message(self, msg_type: str, **fields) -> str:
        """Send a message without waiting for a reply. Returns its id."""
        if self.ws is None:
            raise RelayError("APP_OFFLINE", "not connected to the Relay")
        req_id = "req_" + secrets.token_hex(8)
        await self.ws.send(json.dumps({"type": msg_type, "id": req_id, **fields}))
        return req_id

    async def wait_invoke_result(self, timeout: float = 120.0) -> dict:
        """Wait for the next ``invoke_result`` (chunked-upload replies)."""
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._invoke_waiters.append(fut)
        try:
            msg = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            if fut in self._invoke_waiters:
                self._invoke_waiters.remove(fut)
            raise RelayError(
                "APP_OFFLINE", "timed out waiting for invoke_result"
            ) from exc
        if msg.get("type") == "error":
            raise RelayError(
                str(msg.get("code", "BAD_MESSAGE")), str(msg.get("message", ""))
            )
        return msg

    # -- Relay API (PROTOCOL.md §4) ------------------------------------
    async def pair(self, code: str) -> dict:
        return await self.request("pair", code=code)

    async def login(self, token: str, via_account_login: bool) -> dict:
        # The token value is sent to the Relay only; never logged.
        return await self.request(
            "login", token=token, via_account_login=bool(via_account_login)
        )

    async def room_create(self, name: str | None = None) -> dict:
        fields = {} if name is None else {"name": name}
        return await self.request("room_create", **fields)

    async def room_invite(self, room_id: str, account_id: str) -> dict:
        return await self.request(
            "room_invite", room_id=room_id, account_id=account_id
        )

    async def room_join(self, room_id: str) -> dict:
        return await self.request("room_join", room_id=room_id)

    async def room_post(self, room_id: str, text: str) -> dict:
        return await self.request("room_post", room_id=room_id, text=text)

    async def room_reply(self, room_id: str, reply_to: str, text: str) -> dict:
        return await self.request(
            "room_reply", room_id=room_id, reply_to=reply_to, text=text
        )

    async def room_list(self, room_id: str, limit: int = 50) -> dict:
        return await self.request("room_list", room_id=room_id, limit=limit)

    async def room_members(self, room_id: str) -> dict:
        return await self.request("room_members", room_id=room_id)

    async def ping(self) -> dict:
        return await self.request("ping", timeout=10.0)

    async def invoke(
        self,
        tool: str,
        args: dict,
        root_id: str | None = None,
        timeout: float = 120.0,
    ) -> dict:
        """Invoke a Panel tool, retrying exactly once on root mismatch.

        ``root_id`` defaults to the stored root-id file. On a §6
        BAD_MESSAGE the new root is parsed from the message, saved to
        the root-id file, and the invoke is retried once with it.
        When no root is stored yet, probe with a dummy id: the Relay
        answers BAD_MESSAGE naming the open root, and the retry below
        picks it up (the documented first-invoke bootstrap).
        """
        root = root_id or load_root_id(self.state_dir)
        if not root:
            root = "root_00000000"
        try:
            return await self.request(
                "invoke", root_id=root, tool=tool, args=args, timeout=timeout
            )
        except RelayError as exc:
            if exc.code != "BAD_MESSAGE":
                raise
            parsed = parse_root_mismatch(exc.message)
            if parsed is None:
                raise
            _given, current = parsed
            if current == root:
                raise
            # Retry exactly once with the new root, and persist it.
            save_root_id(current, self.state_dir)
            return await self.request(
                "invoke", root_id=current, tool=tool, args=args, timeout=timeout
            )


def run_sync(coro):
    """Run an async coroutine from sync CLI code."""
    return asyncio.run(coro)
