"""muse-um Relay — HTTP + WebSocket server on one port (PROTOCOL.md §§0-9, 15).

Run from the repo root::

    python -m relay.main

Configuration comes from the environment (see ``relay/config.py``):
``RELAY_HOST`` (default ``127.0.0.1``), ``RELAY_PORT`` (default ``8000``),
``RELAY_ALLOW_PUBLIC_BIND`` (must be ``1``/``true`` to bind ``0.0.0.0``).

All state is in-memory only: a restart wipes accounts, pair codes, rooms,
history and sessions (PROTOCOL.md §3).

Security: never log a login token, root_id or session_id value — only
masked account/room prefixes and counts (PROTOCOL.md §15).

NOTE — POST bodies vs ``websockets.serve`` (PROTOCOL.md §0 vs §9):
§0 mandates ``websockets.serve(..., process_request=...)``, but
websockets 17.4's HTTP handshake parser rejects *any* request whose
``Content-Length`` is not ``0`` ("unsupported request body"), so a plain
``process_request`` implementation can never receive a JSON POST body —
``POST /api/accounts`` etc. (§9) would fail before ``process_request``
runs. This server therefore passes ``create_connection`` with
:class:`RelayServerConnection`, which intercepts the pre-handshake bytes,
stashes a plain-HTTP request body, and re-presents the request to the
handshake parser with ``Content-Length: 0``. WebSocket upgrades pass
through untouched. The server is still ``websockets.serve`` +
``process_request`` on a single port; only the body is recovered.
Chunked ``Transfer-Encoding`` is not supported (websockets rejects it).
"""

from __future__ import annotations

import asyncio
import hmac
import http
import json
import logging
import os
import secrets
import sys
import time
import urllib.parse
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response
from websockets.protocol import State

try:  # `python -m relay.main` from the repo root
    from relay.config import load_config
except ImportError:  # `python relay/main.py`
    from config import load_config

log = logging.getLogger("relay")

MAX_WS_SIZE = 1_048_576
PAIR_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
PAIR_CODE_LENGTH = 8
PAIR_TTL_SECONDS = 300
INVOKE_TIMEOUT_SECONDS = 120

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "app" / "static"

PLACEHOLDER_INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>muse-um Relay</title></head>
<body>
<h1>muse-um Relay</h1>
<p>The Relay is running. The Panel web UI has not been installed yet
(<code>app/static/index.html</code> is missing).</p>
<ul>
<li><a href="/health">/health</a></li>
<li><a href="/api/status">/api/status</a></li>
<li><a href="/api/accounts">/api/accounts</a></li>
<li><a href="/api/rooms">/api/rooms</a></li>
</ul>
</body></html>
"""

_STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


# ---------------------------------------------------------------------------
# IDs (PROTOCOL.md §1 — all generated live with `secrets`)
# ---------------------------------------------------------------------------

def _new_account_id() -> str:
    return "acc_" + secrets.token_hex(8)


def _new_room_id() -> str:
    return "room_conn_" + secrets.token_hex(8)


def _new_session_id() -> str:
    return "ses_" + secrets.token_hex(8)


def _new_root_id() -> str:
    return "root_" + secrets.token_hex(4)


def _new_message_id() -> str:
    return "msg_" + secrets.token_hex(8)


def _new_pair_code() -> str:
    return "".join(secrets.choice(PAIR_CODE_ALPHABET) for _ in range(PAIR_CODE_LENGTH))


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _short(value: str, keep: int = 12) -> str:
    """Masked id for logs, e.g. ``acc_d1b23e…`` (never a full secret)."""
    if not isinstance(value, str):
        return "?"
    return value[:keep] + "…" if len(value) > keep else value


# ---------------------------------------------------------------------------
# In-memory state (PROTOCOL.md §3)
# ---------------------------------------------------------------------------

class RelayState:
    def __init__(self) -> None:
        self.accounts: dict[str, dict] = {}       # id -> {"name", "token"}
        self.pair_codes: dict[str, dict] = {}     # code -> {account_id, expires_at, used}
        self.rooms: dict[str, dict] = {}          # id -> {"name", "members": set, "messages": [...]}
        self.sessions: dict[str, dict] = {}       # session_id -> {"account_id"}
        self.panel_ws = None                      # at most one Panel connection
        self.panel_root_id: str | None = None
        self.panel_root_dir: str | None = None
        self.panel_name: str | None = None
        # invoke_id -> {"ws", "request_id"} for invokes awaiting a Panel reply
        self.pending_invokes: dict[str, dict] = {}
        self.connections: set = set()             # all live WS connections
        self.conn_meta: dict = {}                 # ws -> per-connection state

    @property
    def panel_online(self) -> bool:
        return self.panel_ws is not None and self.panel_root_id is not None


def _members_list(state: RelayState, room: dict) -> list[dict]:
    """Room members as [{account_id, name}] (PROTOCOL.md §4)."""
    out = []
    for account_id in sorted(room["members"]):
        account = state.accounts.get(account_id)
        out.append({"account_id": account_id, "name": account["name"] if account else ""})
    return out


# ---------------------------------------------------------------------------
# HTTP body recovery (see module docstring: websockets rejects POST bodies)
# ---------------------------------------------------------------------------

class RelayServerConnection(ServerConnection):
    """ServerConnection that recovers plain-HTTP POST bodies.

    Before the WebSocket handshake, incoming bytes are buffered until the
    header block is complete. For a plain HTTP request with a body, the
    body is stashed in ``self.http_body`` and only the headers — with
    ``Content-Length`` rewritten to ``0`` — are fed to websockets, whose
    handshake parser otherwise rejects the request outright. WebSocket
    upgrade requests are passed through byte-for-byte.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.http_body: bytes = b""
        self._http_buf = bytearray()
        self._http_preamble_done = False

    def data_received(self, data: bytes) -> None:  # noqa: D102
        if self._http_preamble_done or self.protocol.state is not State.CONNECTING:
            self._http_preamble_done = True
            super().data_received(data)
            return
        self._http_buf += data
        head_end = self._http_buf.find(b"\r\n\r\n")
        if head_end == -1:
            return  # headers not complete yet; wait for more bytes
        head = bytes(self._http_buf[:head_end])
        lines = head.split(b"\r\n")
        is_upgrade = any(
            line.lower().startswith(b"upgrade:") and b"websocket" in line.lower()
            for line in lines[1:]
        )
        content_length = 0
        has_transfer_encoding = False
        for line in lines[1:]:
            lowered = line.lower()
            if lowered.startswith(b"content-length:"):
                try:
                    content_length = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    content_length = 0
            elif lowered.startswith(b"transfer-encoding:"):
                has_transfer_encoding = True
        if is_upgrade or has_transfer_encoding or content_length == 0:
            # Nothing to recover: hand the bytes over unchanged.
            self._http_preamble_done = True
            buffered = bytes(self._http_buf)
            self._http_buf = bytearray()
            super().data_received(buffered)
            return
        total = head_end + 4 + content_length
        if len(self._http_buf) < total:
            return  # body not complete yet; wait for more bytes
        self.http_body = bytes(self._http_buf[head_end + 4:total])
        new_lines = [
            line if not line.lower().startswith(b"content-length:")
            else b"Content-Length: 0"
            for line in lines
        ]
        self._http_preamble_done = True
        self._http_buf = bytearray()
        super().data_received(b"\r\n".join(new_lines) + b"\r\n\r\n")

    def eof_received(self) -> None:  # noqa: D102
        if self._http_buf:
            # Flush whatever was buffered and let websockets decide.
            buffered = bytes(self._http_buf)
            self._http_buf = bytearray()
            self._http_preamble_done = True
            super().data_received(buffered)
        super().eof_received()


# ---------------------------------------------------------------------------
# HTTP API (PROTOCOL.md §9)
# ---------------------------------------------------------------------------

def _http_response(status: int, body: bytes, content_type: str) -> Response:
    headers = Headers([
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
    ])
    return Response(status, http.HTTPStatus(status).phrase, headers, body)


def _json_response(status: int, payload) -> Response:
    return _http_response(
        status,
        json.dumps(payload).encode("utf-8"),
        "application/json; charset=utf-8",
    )


def _parse_json_body(connection, request: Request):
    """Parse the recovered POST body (b"" when there is none)."""
    raw = getattr(connection, "http_body", b"") or b""
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, "invalid JSON body"
    if not isinstance(parsed, dict):
        return None, "JSON body must be an object"
    return parsed, None


async def process_request(state: RelayState, connection, request: Request):
    """``websockets.serve`` process_request hook: HTTP on the same port."""
    upgrade = request.headers.get("Upgrade", "")
    if upgrade and upgrade.lower() == "websocket":
        return None  # WebSocket handshake — let websockets handle it.
    return _handle_http(state, connection, request)


def _handle_http(state: RelayState, connection, request: Request) -> Response:
    split = urllib.parse.urlsplit(request.path)
    path = urllib.parse.unquote(split.path)
    if len(path) > 1:
        path = path.rstrip("/")
    query = urllib.parse.parse_qs(split.query)
    method = request.method.upper()

    body, body_error = _parse_json_body(connection, request)
    if body is None:
        return _json_response(400, {"error": body_error})

    def param(name: str):
        if name in body:
            return body[name]
        values = query.get(name)
        return values[0] if values else None

    # -- GET /health ------------------------------------------------------
    if path == "/health" and method == "GET":
        return _json_response(200, {
            "status": "ok",
            "panel_online": state.panel_online,
            "accounts": len(state.accounts),
            "rooms": len(state.rooms),
        })

    # -- GET / ------------------------------------------------------------
    if path == "/" and method == "GET":
        index = STATIC_DIR / "index.html"
        if index.is_file():
            return _http_response(200, index.read_bytes(), "text/html; charset=utf-8")
        return _http_response(200, PLACEHOLDER_INDEX.encode("utf-8"), "text/html; charset=utf-8")

    # -- GET /static/<file> ------------------------------------------------
    if path.startswith("/static/") and method == "GET":
        rel = urllib.parse.unquote(path[len("/static/"):])
        try:
            target = (STATIC_DIR / rel).resolve()
        except (ValueError, OSError):
            return _json_response(404, {"error": "not found"})
        if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file():
            return _json_response(404, {"error": "not found"})
        content_type = _STATIC_CONTENT_TYPES.get(
            target.suffix.lower(), "application/octet-stream")
        return _http_response(200, target.read_bytes(), content_type)

    # -- GET /api/accounts (never tokens) ----------------------------------
    if path == "/api/accounts" and method == "GET":
        return _json_response(200, [
            {"account_id": account_id, "name": account["name"]}
            for account_id, account in state.accounts.items()
        ])

    # -- POST /api/accounts (token shown ONCE) ------------------------------
    if path == "/api/accounts" and method == "POST":
        name = param("name")
        if not isinstance(name, str) or not name.strip():
            return _json_response(400, {"error": "name is required"})
        account_id = _new_account_id()
        token = _new_token()
        state.accounts[account_id] = {"name": name.strip(), "token": token}
        log.info("account created %s name=%r", _short(account_id), name.strip())
        return _json_response(200, {
            "account_id": account_id, "name": name.strip(), "token": token})

    # -- POST /api/pair ------------------------------------------------------
    if path == "/api/pair" and method == "POST":
        account_id = param("account_id")
        if not isinstance(account_id, str) or account_id not in state.accounts:
            return _json_response(404, {"error": "account not found"})
        code = _new_pair_code()
        while code in state.pair_codes:  # vanishingly unlikely; stay unique
            code = _new_pair_code()
        state.pair_codes[code] = {
            "account_id": account_id,
            "expires_at": time.time() + PAIR_TTL_SECONDS,
            "used": False,
        }
        log.info("pair code created for %s", _short(account_id))
        return _json_response(200, {"code": code, "expires_in": PAIR_TTL_SECONDS})

    # -- GET /api/rooms ------------------------------------------------------
    if path == "/api/rooms" and method == "GET":
        return _json_response(200, [
            {
                "room_id": room_id,
                "name": room["name"],
                # members as [{account_id, name}] — same shape as
                # room_join / room_members (§4), never plain strings.
                "members": _members_list(state, room),
                "message_count": len(room["messages"]),
            }
            for room_id, room in state.rooms.items()
        ])

    # -- POST /api/rooms ------------------------------------------------------
    if path == "/api/rooms" and method == "POST":
        name = param("name")
        if name is not None and not isinstance(name, str):
            return _json_response(400, {"error": "name must be a string"})
        member_ids = param("member_ids")
        if member_ids is None:
            member_ids = []
        if not isinstance(member_ids, list) or not all(
                isinstance(m, str) for m in member_ids):
            return _json_response(400, {"error": "member_ids must be a list of account ids"})
        for member_id in member_ids:
            if member_id not in state.accounts:
                return _json_response(404, {"error": f"account not found: {_short(member_id)}"})
        room_id = _new_room_id()
        state.rooms[room_id] = {
            "name": name.strip() if isinstance(name, str) and name.strip() else "Room",
            "members": set(member_ids),
            "messages": [],
        }
        log.info("room created %s", _short(room_id))
        return _json_response(200, {"room_id": room_id})

    # -- POST /api/rooms/<id>/invite -----------------------------------------
    if path.startswith("/api/rooms/") and path.endswith("/invite") and method == "POST":
        room_id = urllib.parse.unquote(path[len("/api/rooms/"):-len("/invite")])
        room = state.rooms.get(room_id)
        if room is None:
            return _json_response(404, {"error": "room not found"})
        account_id = param("account_id")
        if not isinstance(account_id, str) or account_id not in state.accounts:
            return _json_response(404, {"error": "account not found"})
        room["members"].add(account_id)
        return _json_response(200, {"ok": True})

    # -- GET /api/status (never root_id or any token) -------------------------
    if path == "/api/status" and method == "GET":
        return _json_response(200, {
            "panel_online": state.panel_online,
            "root_active": state.panel_root_id is not None,
            "accounts": len(state.accounts),
            "rooms": len(state.rooms),
        })

    # -- Unknown route / wrong method -----------------------------------------
    known_paths = {"/", "/health", "/api/accounts", "/api/pair",
                   "/api/rooms", "/api/status"}
    if path in known_paths or path.startswith("/static/") or (
            path.startswith("/api/rooms/") and path.endswith("/invite")):
        return _json_response(405, {"error": f"method {method} not allowed for {path}"})
    return _json_response(404, {"error": "not found"})


# ---------------------------------------------------------------------------
# WebSocket (PROTOCOL.md §§2, 4, 5, 6, 7)
# ---------------------------------------------------------------------------

async def _send(ws, payload) -> None:
    try:
        await ws.send(payload if isinstance(payload, str) else json.dumps(payload))
    except ConnectionClosed:
        pass


async def _send_error(ws, req_id, code: str, message: str) -> None:
    await _send(ws, {"type": "error", "id": req_id, "code": code, "message": message})


async def _send_result(ws, req_id, result_type: str, **fields) -> None:
    payload = {"type": result_type, "id": req_id}
    payload.update(fields)
    await _send(ws, payload)


async def _broadcast(state: RelayState, room_id: str, message: dict) -> None:
    """Push room_message to every session joined to the room."""
    text = json.dumps({"type": "room_message", "room_id": room_id, "message": message})
    targets = [
        ws for ws in list(state.connections)
        if room_id in state.conn_meta.get(ws, {}).get("joined", set())
    ]
    if targets:
        await asyncio.gather(*[_send(ws, text) for ws in targets],
                             return_exceptions=True)


async def _fail_pending_invokes(state: RelayState, code: str, message: str) -> None:
    pending = state.pending_invokes
    state.pending_invokes = {}
    for entry in pending.values():
        await _send_error(entry["ws"], entry["request_id"], code, message)


async def _clear_panel(state: RelayState) -> None:
    """Drop the Panel registration; pending invokes fail APP_OFFLINE."""
    state.panel_ws = None
    state.panel_root_id = None
    state.panel_root_dir = None
    state.panel_name = None
    await _fail_pending_invokes(state, "APP_OFFLINE", "No Panel/app connection is online")


async def _invoke_timeout(state: RelayState, invoke_id: str) -> None:
    await asyncio.sleep(INVOKE_TIMEOUT_SECONDS)
    entry = state.pending_invokes.pop(invoke_id, None)
    if entry is not None:
        await _send_error(entry["ws"], entry["request_id"],
                          "BAD_MESSAGE", "invoke timed out")


def _require_session(ws, meta: dict, req_id):
    if meta["account_id"] is None:
        return False
    return True


async def _dispatch(ws, state: RelayState, meta: dict, data: dict) -> None:
    msg_type = data["type"]
    req_id = data.get("id")

    # -- Task API: never available; groups lock work via room messages (§4) --
    if msg_type.startswith("task_"):
        await _send_error(ws, req_id, "TASK_NOT_FOUND", "task API is not supported")
        return

    # -- pair: one-time code, 5 min expiry; token returned ONLY here (§4) --
    if msg_type == "pair":
        code = data.get("code")
        entry = state.pair_codes.get(code) if isinstance(code, str) else None
        if entry is None or entry["used"] or entry["expires_at"] < time.time():
            await _send_error(ws, req_id, "BAD_MESSAGE", "invalid or expired pair code")
            return
        entry["used"] = True
        account = state.accounts.get(entry["account_id"])
        if account is None:
            await _send_error(ws, req_id, "BAD_MESSAGE", "account not found")
            return
        await _send_result(ws, req_id, "pair_result",
                           account_id=entry["account_id"],
                           account_name=account["name"],
                           token=account["token"])
        return

    # -- login: requires Panel online, else APP_OFFLINE (§§4, 5) ------------
    if msg_type == "login":
        if not state.panel_online:
            await _send_error(ws, req_id, "APP_OFFLINE",
                              "No Panel/app connection is online")
            return
        token = data.get("token")
        account_id = None
        account = None
        if isinstance(token, str):
            for aid, acc in state.accounts.items():
                if acc["token"] is not None and hmac.compare_digest(acc["token"], token):
                    account_id, account = aid, acc
                    break
        if account is None:
            await _send_error(ws, req_id, "AUTH_REQUIRED", "unknown token")
            return
        session_id = _new_session_id()
        state.sessions[session_id] = {"account_id": account_id}
        meta["session_id"] = session_id
        meta["account_id"] = account_id
        log.info("login %s", _short(account_id))
        await _send_result(ws, req_id, "login_result",
                           session_id=session_id,
                           account_id=account_id,
                           account_name=account["name"],
                           via_account_login=bool(data.get("via_account_login", False)))
        return

    # -- hello (Panel → Relay, §5): mint a fresh root_id --------------------
    if msg_type == "hello":
        if state.panel_ws is not None and state.panel_ws is not ws:
            old_ws = state.panel_ws
            old_meta = state.conn_meta.get(old_ws)
            if old_meta is not None:
                old_meta["is_panel"] = False
            await _clear_panel(state)  # fails invokes pending on the old Panel
            try:
                await old_ws.close()
            except Exception:
                pass
        root_id = _new_root_id()
        state.panel_ws = ws
        state.panel_root_id = root_id
        root_dir = data.get("root_dir")
        state.panel_root_dir = root_dir if isinstance(root_dir, str) else None
        name = data.get("name")
        state.panel_name = name if isinstance(name, str) else None
        meta["is_panel"] = True
        log.info("panel hello name=%r root minted", state.panel_name)
        await _send_result(ws, req_id, "hello_result", root_id=root_id)
        return

    # -- invoke_result (Panel → Relay, §5): forward to the waiting client ---
    if msg_type == "invoke_result":
        if not meta["is_panel"]:
            await _send_error(ws, req_id, "BAD_MESSAGE",
                              "invoke_result is only accepted from the Panel")
            return
        invoke_id = data.get("invoke_id")
        entry = state.pending_invokes.pop(invoke_id, None) \
            if isinstance(invoke_id, str) else None
        if entry is None:
            log.info("invoke_result for unknown invoke_id; dropped")
            return
        # Forward the outcome, preserving the Panel's `code` (e.g.
        # FORBIDDEN / BAD_MESSAGE) and attaching the original request id
        # so the client can match the reply. Schema:
        # {invoke_id, ok, result, error, code?} + id of the invoke request.
        payload = {
            "type": "invoke_result",
            "id": entry["request_id"],
            "invoke_id": invoke_id,
            "ok": bool(data.get("ok", False)),
            "result": data.get("result"),
            "error": data.get("error"),
        }
        if data.get("code") is not None:
            payload["code"] = data.get("code")
        await _send(entry["ws"], payload)
        return

    # -- ping (§4): result type is "pong", not "ping_result" -----------------
    if msg_type == "ping":
        payload = {"type": "pong"}
        if req_id is not None:
            payload["id"] = req_id
        await _send(ws, payload)
        return

    # -- close (§4): destroys the session; token removed from Relay RAM -----
    if msg_type == "close":
        session_id = meta["session_id"]
        account_id = meta["account_id"]
        if session_id is not None:
            state.sessions.pop(session_id, None)
        if account_id is not None and account_id in state.accounts:
            state.accounts[account_id]["token"] = None
        meta["session_id"] = None
        meta["account_id"] = None
        meta["joined"] = set()
        await _send_result(ws, req_id, "close_result", ok=True)
        return

    # -- Everything below needs a logged-in session --------------------------
    if not _require_session(ws, meta, req_id):
        await _send_error(ws, req_id, "AUTH_REQUIRED", "login required")
        return
    account_id = meta["account_id"]

    # -- room_create ----------------------------------------------------------
    if msg_type == "room_create":
        name = data.get("name")
        if name is not None and not isinstance(name, str):
            await _send_error(ws, req_id, "BAD_MESSAGE", "name must be a string")
            return
        room_id = _new_room_id()
        state.rooms[room_id] = {
            "name": name.strip() if isinstance(name, str) and name.strip() else "Room",
            "members": {account_id},
            "messages": [],
        }
        meta["joined"].add(room_id)
        log.info("room created %s by %s", _short(room_id), _short(account_id))
        await _send_result(ws, req_id, "room_create_result", room_id=room_id)
        return

    # -- room_invite ----------------------------------------------------------
    if msg_type == "room_invite":
        room = state.rooms.get(data.get("room_id")) \
            if isinstance(data.get("room_id"), str) else None
        if room is None:
            await _send_error(ws, req_id, "NOT_FOUND", "room not found")
            return
        target = data.get("account_id")
        if not isinstance(target, str) or target not in state.accounts:
            await _send_error(ws, req_id, "NOT_FOUND", "account not found")
            return
        room["members"].add(target)
        await _send_result(ws, req_id, "room_invite_result", ok=True)
        return

    # -- room_join -------------------------------------------------------------
    if msg_type == "room_join":
        room = state.rooms.get(data.get("room_id")) \
            if isinstance(data.get("room_id"), str) else None
        if room is None:
            await _send_error(ws, req_id, "NOT_FOUND", "room not found")
            return
        room["members"].add(account_id)
        meta["joined"].add(data["room_id"])
        await _send_result(ws, req_id, "room_join_result",
                           room_id=data["room_id"],
                           members=_members_list(state, room))
        return

    # -- room_post --------------------------------------------------------------
    if msg_type == "room_post":
        room_id = data.get("room_id")
        room = state.rooms.get(room_id) if isinstance(room_id, str) else None
        if room is None:
            await _send_error(ws, req_id, "NOT_FOUND", "room not found")
            return
        if room_id not in meta["joined"]:
            await _send_error(ws, req_id, "BAD_MESSAGE",
                              "session has not joined this room")
            return
        text = data.get("text")
        if not isinstance(text, str) or not text:
            await _send_error(ws, req_id, "BAD_MESSAGE", "text is required")
            return
        account = state.accounts[account_id]
        message = {
            "id": _new_message_id(),
            "room_id": room_id,
            "author_account_id": account_id,
            "author_name": account["name"],
            "text": text,
            "reply_to": None,
            "ts": time.time(),
        }
        room["messages"].append(message)
        await _send_result(ws, req_id, "room_post_result", message_id=message["id"])
        await _broadcast(state, room_id, message)
        return

    # -- room_reply: reply_to MUST exist in the room history (§4) --------------
    if msg_type == "room_reply":
        room_id = data.get("room_id")
        room = state.rooms.get(room_id) if isinstance(room_id, str) else None
        if room is None:
            await _send_error(ws, req_id, "NOT_FOUND", "room not found")
            return
        if room_id not in meta["joined"]:
            await _send_error(ws, req_id, "BAD_MESSAGE",
                              "session has not joined this room")
            return
        reply_to = data.get("reply_to")
        if not any(m["id"] == reply_to for m in room["messages"]):
            await _send_error(ws, req_id, "BAD_MESSAGE",
                              "reply_to message not found in room history")
            return
        text = data.get("text")
        if not isinstance(text, str) or not text:
            await _send_error(ws, req_id, "BAD_MESSAGE", "text is required")
            return
        account = state.accounts[account_id]
        message = {
            "id": _new_message_id(),
            "room_id": room_id,
            "author_account_id": account_id,
            "author_name": account["name"],
            "text": text,
            "reply_to": reply_to,
            "ts": time.time(),
        }
        room["messages"].append(message)
        await _send_result(ws, req_id, "room_reply_result", message_id=message["id"])
        await _broadcast(state, room_id, message)
        return

    # -- room_list: OLDEST `limit` messages, NOT the newest (§4) ----------------
    if msg_type == "room_list":
        room_id = data.get("room_id")
        room = state.rooms.get(room_id) if isinstance(room_id, str) else None
        if room is None:
            await _send_error(ws, req_id, "NOT_FOUND", "room not found")
            return
        limit = data.get("limit")
        if limit is None:
            messages = list(room["messages"])
        else:
            if isinstance(limit, bool):
                await _send_error(ws, req_id, "BAD_MESSAGE", "limit must be an integer")
                return
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                await _send_error(ws, req_id, "BAD_MESSAGE", "limit must be an integer")
                return
            messages = list(room["messages"][:max(0, limit)])
        await _send_result(ws, req_id, "room_list_result", messages=messages)
        return

    # -- room_members ------------------------------------------------------------
    if msg_type == "room_members":
        room_id = data.get("room_id")
        room = state.rooms.get(room_id) if isinstance(room_id, str) else None
        if room is None:
            await _send_error(ws, req_id, "NOT_FOUND", "room not found")
            return
        await _send_result(ws, req_id, "room_members_result",
                           members=_members_list(state, room))
        return

    # -- invoke (§6): root check, then forward to the Panel -----------------------
    if msg_type == "invoke":
        if not state.panel_online:
            await _send_error(ws, req_id, "APP_OFFLINE",
                              "No Panel/app connection is online")
            return
        root_id = data.get("root_id")
        if not isinstance(root_id, str) or not root_id:
            await _send_error(ws, req_id, "BAD_MESSAGE", "root_id is required")
            return
        current = state.panel_root_id
        if root_id != current:
            await _send_error(
                ws, req_id, "BAD_MESSAGE",
                f"root_id '{root_id}' does not match the open root '{current}'")
            return
        tool = data.get("tool")
        args = data.get("args", {})
        if not isinstance(tool, str) or not tool:
            await _send_error(ws, req_id, "BAD_MESSAGE", "tool is required")
            return
        if not isinstance(args, dict):
            await _send_error(ws, req_id, "BAD_MESSAGE", "args must be an object")
            return
        # NOTE: argument validation (e.g. write_file mode) is done by the
        # Panel, so every tool failure comes back in one shape:
        # invoke_result {ok: false, error, code}. The Relay does not
        # pre-validate tool args here.
        invoke_id = "inv_" + secrets.token_hex(8)
        state.pending_invokes[invoke_id] = {"ws": ws, "request_id": req_id}
        try:
            await state.panel_ws.send(json.dumps({
                "type": "invoke_request",
                "invoke_id": invoke_id,
                "tool": tool,
                "args": args,
                "root_id": root_id,
                "account_id": account_id,
            }))
        except ConnectionClosed:
            state.pending_invokes.pop(invoke_id, None)
            await _send_error(ws, req_id, "APP_OFFLINE",
                              "No Panel/app connection is online")
            return
        asyncio.create_task(_invoke_timeout(state, invoke_id))
        return  # outcome arrives later as invoke_result

    # -- chunked file transfer (§7): forward to the Panel unchanged ---------------
    if msg_type in ("chunked_upload_start", "chunked_upload_chunk",
                   "chunked_upload_end"):
        if not state.panel_online:
            await _send_error(ws, req_id, "APP_OFFLINE",
                              "No Panel/app connection is online")
            return
        if msg_type == "chunked_upload_start":
            upload_id = data.get("upload_id")
            if isinstance(upload_id, str):
                state.pending_invokes[upload_id] = {"ws": ws, "request_id": req_id}
        try:
            await state.panel_ws.send(json.dumps(data))
        except ConnectionClosed:
            await _send_error(ws, req_id, "APP_OFFLINE",
                              "No Panel/app connection is online")
        return

    await _send_error(ws, req_id, "BAD_MESSAGE", f"unknown message type: {msg_type}")


async def _ws_handler(ws, state: RelayState) -> None:
    meta = {"session_id": None, "account_id": None,
            "joined": set(), "is_panel": False}
    state.connections.add(ws)
    state.conn_meta[ws] = meta
    try:
        async for raw in ws:
            if isinstance(raw, bytes):
                await _send_error(ws, None, "BAD_MESSAGE",
                                  "binary frames are not supported")
                continue
            try:
                data = json.loads(raw)
            except ValueError:
                await _send_error(ws, None, "BAD_MESSAGE", "invalid JSON")
                continue
            if not isinstance(data, dict) or not isinstance(data.get("type"), str):
                req_id = data.get("id") if isinstance(data, dict) else None
                await _send_error(ws, req_id, "BAD_MESSAGE",
                                  "message must be a JSON object with a string type")
                continue
            await _dispatch(ws, state, meta, data)
    except ConnectionClosed:
        pass
    finally:
        state.connections.discard(ws)
        state.conn_meta.pop(ws, None)
        if meta["is_panel"] and state.panel_ws is ws:
            log.info("panel disconnected")
            await _clear_panel(state)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

async def main() -> None:
    # Check layer 1 — relay/config.py refuses a public bind without the flag.
    config = load_config()
    # Check layer 2 — independent re-check here in main.py (PROTOCOL.md §0):
    # refuse to start on 0.0.0.0 unless RELAY_ALLOW_PUBLIC_BIND is set.
    if os.environ.get("RELAY_HOST", "127.0.0.1").strip() == "0.0.0.0" and \
            os.environ.get("RELAY_ALLOW_PUBLIC_BIND", "").strip().lower() not in ("1", "true"):
        raise RuntimeError(
            "Refusing to bind RELAY_HOST=0.0.0.0 without "
            "RELAY_ALLOW_PUBLIC_BIND=1 (checked in relay/main.py).")
    state = RelayState()

    async def handler(ws):
        await _ws_handler(ws, state)

    async def _process_request(connection, request):
        return await process_request(state, connection, request)

    server = await serve(
        handler,
        config.host,
        config.port,
        max_size=MAX_WS_SIZE,
        process_request=_process_request,
        create_connection=RelayServerConnection,
    )
    log.info("Relay listening on %s:%d (panel_online=%s)",
             config.host, config.port, state.panel_online)
    await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(main())
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except OSError as exc:
        print(f"ERROR: cannot start Relay: {exc}", file=sys.stderr)
        sys.exit(2)
