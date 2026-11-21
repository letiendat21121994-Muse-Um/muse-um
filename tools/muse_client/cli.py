"""CLI for muse-um client (PROTOCOL.md §11).

Run as:  python -m tools.muse_client <command>

Security (§15): the login token comes ONLY from the env var
``MUSE_UM_ACCOUNT_TOKEN`` (there is no CLI flag for it), and token /
root_id / session_id values are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from .chunked import read_file_full, upload_chunked
from .client import (
    MissingRelayUrlError,
    RelayClient,
    RelayError,
    require_relay_url,
)
from .daemon import daemon_call, daemon_start, daemon_status, daemon_stop, run_daemon
from .state import (
    get_state_dir,
    load_root_id,
    load_session,
    read_queue,
    save_root_id,
    save_session,
)
from .token_crypto import has_saved_token, load_token, save_token

PAIR_CODE_RE = re.compile(r"^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$")


def _fail(message: str, code: int = 1) -> int:
    print(f"error: {message}", file=sys.stderr)
    return code


# ---------------------------------------------------------------------
# Direct-WS fallback (used when the daemon is down; prefer daemon)
# ---------------------------------------------------------------------

async def _direct_login_client(relay_url: str) -> RelayClient:
    token = load_token()
    if token is None:
        raise RelayError("AUTH_REQUIRED", "no saved account token; run 'pair' or 'login' first")
    client = RelayClient(relay_url)
    await client.connect()
    try:
        result = await client.login(token, via_account_login=True)
    except Exception:
        await client.close()
        raise
    session = load_session() or {}
    session.update(
        {
            "session_id": result.get("session_id"),
            "account_id": result.get("account_id"),
            "relay_url": relay_url,
        }
    )
    if session.get("room_id"):
        try:
            await client.room_join(session["room_id"])
        except RelayError:
            pass
    save_session(session)
    return client


async def _direct_room_op(command: str, **fields) -> dict:
    relay_url = require_relay_url()
    client = await _direct_login_client(relay_url)
    try:
        session = load_session() or {}
        room_id = session.get("room_id")
        if command == "post":
            if not room_id:
                raise RelayError("BAD_MESSAGE", "no room joined; run 'room join' first")
            return await client.room_post(room_id, fields["text"])
        if command == "reply":
            if not room_id:
                raise RelayError("BAD_MESSAGE", "no room joined; run 'room join' first")
            return await client.room_reply(room_id, fields["id"], fields["text"])
        if command == "create":
            return await client.room_create(fields.get("name"))
        if command == "join":
            res = await client.room_join(fields["room_id"])
            session["room_id"] = res.get("room_id", fields["room_id"])
            save_session(session)
            return res
        if command == "invite":
            return await client.room_invite(fields["room_id"], fields["account_id"])
        raise ValueError(f"unknown direct op: {command}")
    finally:
        await client.close()


def _room_via_daemon_or_direct(command: str, **fields) -> dict:
    """Prefer the daemon socket; fall back to a direct WS session."""
    resp = daemon_call({"command": command, **fields})
    if resp is not None:
        if resp.get("ok"):
            return resp
        # Daemon reachable but the op failed (e.g. BAD_MESSAGE on an old
        # reply id): report the daemon's error, do not retry elsewhere.
        raise RelayError(
            resp.get("code", "BAD_MESSAGE"), resp.get("error", "daemon request failed")
        )
    return asyncio.run(_direct_room_op(command, **fields))


# ---------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------

def cmd_pair(args: argparse.Namespace) -> int:
    try:
        relay_url = require_relay_url()
    except MissingRelayUrlError as exc:
        return _fail(str(exc), 2)
    code = args.code.strip().upper()
    if not PAIR_CODE_RE.match(code):
        return _fail("pair code must be 8 chars from A-Z (no 0/O/1/I) and 2-9", 2)

    async def _run():
        async with RelayClient(relay_url) as client:
            return await client.pair(code)

    try:
        result = asyncio.run(_run())
    except RelayError as exc:
        return _fail(f"{exc.code}: {exc.message}")
    token = result.get("token")
    if not token:
        return _fail("pair succeeded but no token was returned")
    save_token(token)
    print(f"paired account_id={result.get('account_id')} name={result.get('account_name')}")
    # Log in with the fresh token to obtain a session.
    async def _login():
        async with RelayClient(relay_url) as client:
            return await client.login(token, via_account_login=False)

    try:
        login_res = asyncio.run(_login())
    except RelayError as exc:
        print(
            f"warning: token saved, but login failed ({exc.code}: {exc.message}); "
            "start the daemon later to retry",
            file=sys.stderr,
        )
        return 0
    save_session(
        {
            "session_id": login_res.get("session_id"),
            "account_id": login_res.get("account_id"),
            "room_id": None,
            "relay_url": relay_url,
        }
    )
    print("login ok; session saved")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    try:
        relay_url = require_relay_url()
    except MissingRelayUrlError as exc:
        return _fail(str(exc), 2)
    token = os.environ.get("MUSE_UM_ACCOUNT_TOKEN", "")
    if not token:
        return _fail(
            "MUSE_UM_ACCOUNT_TOKEN is not set. The login token is passed "
            "only via that environment variable (never a CLI flag).",
            2,
        )

    async def _run():
        async with RelayClient(relay_url) as client:
            return await client.login(token, via_account_login=False)

    try:
        result = asyncio.run(_run())
    except RelayError as exc:
        return _fail(f"{exc.code}: {exc.message}")
    save_token(token)
    save_session(
        {
            "session_id": result.get("session_id"),
            "account_id": result.get("account_id"),
            "room_id": None,
            "relay_url": relay_url,
        }
    )
    print(f"login ok account_id={result.get('account_id')} name={result.get('account_name')}")
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    state_dir = get_state_dir()
    if args.action == "_run":
        # Internal: the background daemon process itself.
        try:
            relay_url = require_relay_url()
        except MissingRelayUrlError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        run_daemon(relay_url, state_dir)
        return 0
    if args.action == "start":
        try:
            result = daemon_start(state_dir)
        except MissingRelayUrlError as exc:
            return _fail(str(exc), 2)
        if not result.get("ok"):
            return _fail(result.get("error", "daemon failed to start"))
        if result.get("already_running"):
            print("daemon: already running")
        else:
            print(f"daemon: started (pid {result.get('pid')})")
        return 0
    if args.action == "stop":
        result = daemon_stop(state_dir)
        if not result.get("ok"):
            return _fail(result.get("error", "daemon stop failed"))
        print("daemon: stopped")
        return 0
    if args.action == "status":
        status = daemon_status(state_dir)
        print(f"daemon: {status.get('daemon')}")
        print(f"connected: {str(status.get('connected', False)).lower()}")
        print(f"room_joined: {str(status.get('room_joined', False)).lower()}")
        print(f"session: {'active' if status.get('session') else 'none'}")
        print(f"room_id: {status.get('room_id') or ''}")
        members = status.get("room_members") or []
        names = ", ".join(
            m.get("name") or m.get("account_id", "") for m in members
        )
        print(f"room_members: [{names}]")
        print(f"pending_room_messages: {status.get('pending_room_messages', 0)}")
        print(f"root_id: {'set' if status.get('root_id_present') else ''}")
        return 0
    return _fail(f"unknown daemon action: {args.action}", 2)


def cmd_room(args: argparse.Namespace) -> int:
    if args.action == "list":
        # Local read only; works even when the session is dead (§7.2).
        messages = read_queue()
        session = load_session() or {}
        room_id = session.get("room_id")
        if room_id:
            in_room = [m for m in messages if m.get("room_id") == room_id]
            if in_room:
                messages = in_room
        # N OLDEST messages (§4 room_list / §7.2).
        for msg in messages[: args.limit]:
            print(json.dumps(msg, ensure_ascii=False))
        return 0
    try:
        require_relay_url()
    except MissingRelayUrlError as exc:
        return _fail(str(exc), 2)
    try:
        if args.action == "post":
            res = _room_via_daemon_or_direct("post", text=args.text)
            print(f"post_id {res.get('message_id')}")
        elif args.action == "reply":
            res = _room_via_daemon_or_direct(
                "reply", id=args.id, text=args.text
            )
            print(f"post_id {res.get('message_id')}")
        elif args.action == "create":
            res = _room_via_daemon_or_direct("create", name=args.name)
            print(f"room_id {res.get('room_id')}")
        elif args.action == "join":
            res = _room_via_daemon_or_direct("join", room_id=args.room_id)
            print(f"joined room_id {res.get('room_id')}")
        elif args.action == "invite":
            _room_via_daemon_or_direct(
                "invite", room_id=args.room_id, account_id=args.account_id
            )
            print("invite ok")
        else:
            return _fail(f"unknown room action: {args.action}", 2)
    except RelayError as exc:
        return _fail(f"{exc.code}: {exc.message}")
    return 0


def cmd_invoke(args: argparse.Namespace) -> int:
    try:
        relay_url = require_relay_url()
    except MissingRelayUrlError as exc:
        return _fail(str(exc), 2)
    try:
        parsed_args = json.loads(args.args) if args.args else {}
    except ValueError as exc:
        return _fail(f"--args is not valid JSON: {exc}", 2)
    if not isinstance(parsed_args, dict):
        return _fail("--args must be a JSON object", 2)
    if args.chunked_file is not None:
        if args.tool != "write_file":
            return _fail("--chunked-file is only valid with --tool write_file", 2)
        if "path" not in parsed_args or "mode" not in parsed_args:
            return _fail(
                "--chunked-file requires --args to contain 'path' and 'mode'", 2
            )
        if not Path(args.chunked_file).is_file():
            return _fail(f"chunked file not found: {args.chunked_file}", 2)

    async def _run():
        async with RelayClient(relay_url) as client:
            token = load_token()
            if token is not None:
                try:
                    await client.login(token, via_account_login=True)
                    saved = load_session() or {}
                    if saved.get("room_id"):
                        try:
                            await client.room_join(saved["room_id"])
                        except RelayError:
                            pass
                except RelayError:
                    # Invoke can still proceed if a session is not
                    # required by this Relay; login failure is reported
                    # only if the invoke itself then fails.
                    pass
            if args.chunked_file is not None:
                return await upload_chunked(
                    client,
                    args.chunked_file,
                    parsed_args["path"],
                    parsed_args["mode"],
                )
            return await client.invoke(
                args.tool, parsed_args, root_id=args.root_id
            )

    try:
        result = asyncio.run(_run())
    except RelayError as exc:
        return _fail(f"{exc.code}: {exc.message}")
    except ValueError as exc:
        return _fail(str(exc))
    if isinstance(result, dict) and result.get("type") == "invoke_result" \
            and result.get("ok") is False:
        # A failed tool must not look successful: exit non-zero and
        # show the Panel's code + message (never any secret values).
        return _fail(
            f"{result.get('code') or 'BAD_MESSAGE'}: "
            f"{result.get('error') or 'invoke failed'}")
    print(json.dumps(result.get("result", result), ensure_ascii=False, indent=2))
    return 0


def cmd_read_file(args: argparse.Namespace) -> int:
    """Utility: read a remote file back in byte ranges, verify SHA-256."""
    try:
        relay_url = require_relay_url()
    except MissingRelayUrlError as exc:
        return _fail(str(exc), 2)

    async def _run():
        async with RelayClient(relay_url) as client:
            token = load_token()
            if token is not None:
                await client.login(token, via_account_login=True)
            return await read_file_full(
                client,
                args.path,
                expected_sha256=args.sha256,
                root_id=args.root_id,
            )

    try:
        data = asyncio.run(_run())
    except (RelayError, ValueError) as exc:
        return _fail(str(exc))
    if args.out:
        Path(args.out).write_bytes(data)
        print(f"wrote {len(data)} bytes to {args.out}")
    else:
        sys.stdout.buffer.write(data)
    return 0


# ---------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools.muse_client",
        description="muse-um client: pair/login, daemon, rooms, invoke.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pair = sub.add_parser("pair", help="pair with an 8-char code, save token")
    p_pair.add_argument("--code", required=True, help="8-char pair code")
    p_pair.set_defaults(func=cmd_pair)

    p_login = sub.add_parser(
        "login",
        help="log in with the token from MUSE_UM_ACCOUNT_TOKEN only",
    )
    p_login.set_defaults(func=cmd_login)

    p_daemon = sub.add_parser("daemon", help="daemon start|stop|status")
    p_daemon.add_argument(
        "action", choices=["start", "stop", "status", "_run"],
        help="daemon action (_run is internal)",
    )
    p_daemon.set_defaults(func=cmd_daemon)

    p_room = sub.add_parser("room", help="room post|reply|list|create|join|invite")
    room_sub = p_room.add_subparsers(dest="action", required=True)
    p_post = room_sub.add_parser("post", help="post a message to the joined room")
    p_post.add_argument("--text", required=True)
    p_reply = room_sub.add_parser("reply", help="reply to a message by id")
    p_reply.add_argument("--id", required=True, dest="id")
    p_reply.add_argument("--text", required=True)
    p_list = room_sub.add_parser(
        "list", help="list N OLDEST messages from the local queue"
    )
    p_list.add_argument("--limit", type=int, default=50)
    p_create = room_sub.add_parser("create", help="create a room")
    p_create.add_argument("--name", required=False, default=None)
    p_join = room_sub.add_parser("join", help="join a room")
    p_join.add_argument("--room-id", required=True)
    p_invite = room_sub.add_parser("invite", help="invite an account to a room")
    p_invite.add_argument("--room-id", required=True)
    p_invite.add_argument("--account-id", required=True)
    p_room.set_defaults(func=cmd_room)

    p_invoke = sub.add_parser("invoke", help="invoke a Panel tool on the host")
    p_invoke.add_argument("--tool", required=True)
    p_invoke.add_argument("--args", default="{}", help="JSON object of tool args")
    p_invoke.add_argument("--root-id", default=None,
                          help="root id (default: stored root-id file)")
    p_invoke.add_argument("--chunked-file", default=None,
                          help="local file to upload in 256 KiB chunks "
                               "(only with --tool write_file)")
    p_invoke.set_defaults(func=cmd_invoke)

    p_read = sub.add_parser(
        "read-file",
        help="utility: read a remote file back in chunks, verify SHA-256",
    )
    p_read.add_argument("--path", required=True)
    p_read.add_argument("--sha256", default=None)
    p_read.add_argument("--root-id", default=None)
    p_read.add_argument("--out", default=None)
    p_read.set_defaults(func=cmd_read_file)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except MissingRelayUrlError as exc:
        return _fail(str(exc), 2)
    except RelayError as exc:
        return _fail(f"{exc.code}: {exc.message}")
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
