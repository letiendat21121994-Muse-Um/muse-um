"""Background daemon (PROTOCOL.md §§10-11).

- Holds one WebSocket connection to the Relay.
- On start, auto-logs in with the saved encrypted token
  (``via_account_login: true``) and re-joins the saved room.
- Appends every incoming ``room_message`` NOT authored by self to
  ``room-queue.jsonl``.
- Serves the CLI over the Unix socket ``daemon.sock``:
  JSON ``{command, ...}`` -> ``{ok, ...}`` / ``{ok:false, error}``.
- Writes ``daemon.pid``, ``daemon.log`` (via the spawn parent) and
  ``daemon-status.json``.
- Reconnects with backoff when the Relay drops; never calls ``close``
  on the live session (PROTOCOL.md §15).
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .client import MissingRelayUrlError, RelayClient, RelayError, require_relay_url
from .state import (
    DAEMON_LOG_FILE,
    DAEMON_PID_FILE,
    DAEMON_SOCK_FILE,
    DAEMON_STATUS_FILE,
    ROOM_QUEUE_FILE,
    append_queue,
    get_state_dir,
    load_root_id,
    load_session,
    read_queue,
    save_session,
    state_path,
)
from .token_crypto import load_token

RECONNECT_DELAY_S = 5.0
STATUS_WRITE_INTERVAL_S = 10.0


def _repo_root() -> Path:
    # tools/muse_client/daemon.py -> repo root is parents[2].
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------
# Daemon process
# ---------------------------------------------------------------------

class Daemon:
    def __init__(self, relay_url: str, state_dir: Path | None = None):
        self.relay_url = relay_url
        self.state_dir = state_dir or get_state_dir()
        self.client: RelayClient | None = None
        self.account_id: str | None = None
        self.account_name: str | None = None
        self.session_id: str | None = None
        self.room_id: str | None = None
        self.members: list[dict] = []
        self.connected = False
        self.room_joined = False
        self._stop = asyncio.Event()
        self._sock_server: asyncio.AbstractServer | None = None
        self._status_task: asyncio.Task | None = None

    # -- status --------------------------------------------------------
    def status_dict(self) -> dict:
        return {
            "daemon": "running",
            "connected": self.connected,
            "room_joined": self.room_joined,
            "session": self.session_id is not None,
            "account_id_present": self.account_id is not None,
            "room_id": self.room_id,
            "room_members": self.members,
            "pending_room_messages": len(read_queue(self.state_dir)),
            "root_id_present": load_root_id(self.state_dir) is not None,
        }

    def write_status(self) -> None:
        path = state_path(DAEMON_STATUS_FILE, self.state_dir)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({**self.status_dict(), "updated_at": time.time()}, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    # -- room messages ---------------------------------------------------
    def _on_room_message(self, push: dict) -> None:
        message = push.get("message") or {}
        if not isinstance(message, dict):
            return
        if self.account_id is not None and message.get(
            "author_account_id"
        ) == self.account_id:
            return  # never store our own messages (§10)
        append_queue(message, self.state_dir)
        self.write_status()

    # -- connection lifecycle --------------------------------------------
    async def _login_and_join(self) -> None:
        token = load_token(self.state_dir)
        if token is None:
            raise RelayError(
                "AUTH_REQUIRED",
                "no saved account token; run 'pair' or 'login' first",
            )
        assert self.client is not None
        result = await self.client.login(token, via_account_login=True)
        self.session_id = result.get("session_id")
        self.account_id = result.get("account_id")
        self.account_name = result.get("account_name")
        saved = load_session(self.state_dir) or {}
        self.room_id = saved.get("room_id")
        if not self.room_id:
            # Recovery (PROTOCOL.md §13) moves session.json aside, so
            # the room is no longer there. The last known room is kept
            # in daemon-status.json (rewritten on every join/message)
            # — rejoin it if it still exists on the Relay. A room that
            # died with a Relay restart fails the join and the daemon
            # simply stays logged in without a room.
            try:
                last = json.loads(
                    state_path(DAEMON_STATUS_FILE, self.state_dir)
                    .read_text(encoding="utf-8")
                )
                self.room_id = last.get("room_id") or None
            except (OSError, ValueError):
                self.room_id = None
        if self.room_id:
            try:
                joined = await self.client.room_join(self.room_id)
                self.room_joined = True
                self.members = joined.get("members", []) or []
            except RelayError:
                # Saved room may be gone after a Relay restart; stay
                # logged in without a room.
                self.room_id = None
                self.room_joined = False
                self.members = []
        save_session(
            {
                "session_id": self.session_id,
                "account_id": self.account_id,
                "room_id": self.room_id,
                "relay_url": self.relay_url,
            },
            self.state_dir,
        )
        self.connected = True
        self.write_status()

    async def _serve_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.client = RelayClient(self.relay_url, self.state_dir)
                self.client.on_room_message = self._on_room_message
                await self.client.connect()
                await self._login_and_join()
                # Block until the connection drops or we are stopping.
                while not self._stop.is_set():
                    await asyncio.sleep(1)
                    if self.client.ws is None or self.client.ws.close_code is not None:
                        break
            except (RelayError, OSError, asyncio.CancelledError) as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                self.connected = False
                self.room_joined = False
                self.write_status()
                print(f"[daemon] connection failed: {exc}", flush=True)
            finally:
                if self.client is not None:
                    await self.client.close()
                    self.client = None
                self.connected = False
                self.write_status()
            if self._stop.is_set():
                break
            await asyncio.wait_for(self._stop.wait(), timeout=RECONNECT_DELAY_S)

    # -- Unix socket API ---------------------------------------------------
    async def _handle_socket(self, reader, writer) -> None:
        try:
            raw = await reader.read()
            if not raw:
                return
            try:
                req = json.loads(raw.decode("utf-8"))
            except ValueError:
                resp = {"ok": False, "error": "invalid JSON request"}
            else:
                resp = await self._dispatch(dict(req))
            writer.write(json.dumps(resp).encode("utf-8") + b"\n")
            await writer.drain()
        except Exception as exc:  # never kill the socket server on a bad req
            try:
                writer.write(
                    json.dumps({"ok": False, "error": str(exc)}).encode("utf-8")
                    + b"\n"
                )
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()

    async def _dispatch(self, req: dict) -> dict:
        command = req.get("command")
        if command == "status":
            return {"ok": True, **self.status_dict()}
        if command == "ping":
            return {"ok": True, "pong": True}
        if command == "stop":
            asyncio.create_task(self._shutdown())
            return {"ok": True, "stopping": True}
        if self.client is None or not self.connected:
            return {"ok": False, "error": "daemon is not connected to the Relay"}
        try:
            if command == "post":
                if not self.room_id:
                    return {"ok": False, "error": "no room joined"}
                res = await self.client.room_post(self.room_id, req["text"])
                return {"ok": True, "message_id": res.get("message_id")}
            if command == "reply":
                if not self.room_id:
                    return {"ok": False, "error": "no room joined"}
                res = await self.client.room_reply(
                    self.room_id, req["id"], req["text"]
                )
                return {"ok": True, "message_id": res.get("message_id")}
            if command == "create":
                res = await self.client.room_create(req.get("name"))
                room_id = res.get("room_id")
                if room_id:
                    # The Relay already lists the creator as a member;
                    # join locally too so room post works right after
                    # create without an extra explicit join.
                    try:
                        joined = await self.client.room_join(room_id)
                        self.room_id = joined.get("room_id", room_id)
                        self.room_joined = True
                        self.members = joined.get("members", []) or []
                        saved = load_session(self.state_dir) or {}
                        saved.update(
                            {
                                "session_id": self.session_id,
                                "account_id": self.account_id,
                                "room_id": self.room_id,
                                "relay_url": self.relay_url,
                            }
                        )
                        save_session(saved, self.state_dir)
                        self.write_status()
                    except Exception:
                        pass
                return {"ok": True, "room_id": room_id}
            if command == "join":
                res = await self.client.room_join(req["room_id"])
                self.room_id = res.get("room_id", req["room_id"])
                self.room_joined = True
                self.members = res.get("members", []) or []
                saved = load_session(self.state_dir) or {}
                saved.update(
                    {
                        "session_id": self.session_id,
                        "account_id": self.account_id,
                        "room_id": self.room_id,
                        "relay_url": self.relay_url,
                    }
                )
                save_session(saved, self.state_dir)
                self.write_status()
                return {"ok": True, "room_id": self.room_id, "members": self.members}
            if command == "invite":
                res = await self.client.room_invite(
                    req["room_id"], req["account_id"]
                )
                return {"ok": True, **res}
            if command == "invoke":
                res = await self.client.invoke(
                    req["tool"], req.get("args") or {}, root_id=req.get("root_id")
                )
                return {"ok": True, "result": res.get("result", res)}
            return {"ok": False, "error": f"unknown command: {command}"}
        except RelayError as exc:
            # Keep code and message separate; the CLI adds "CODE: " once.
            return {"ok": False, "error": exc.message, "code": exc.code}
        except KeyError as exc:
            return {"ok": False, "error": f"missing field: {exc}"}

    async def _shutdown(self) -> None:
        self._stop.set()
        if self._sock_server is not None:
            self._sock_server.close()

    async def run(self) -> None:
        pid_path = state_path(DAEMON_PID_FILE, self.state_dir)
        pid_path.write_text(str(os.getpid()), encoding="utf-8")
        sock_path = state_path(DAEMON_SOCK_FILE, self.state_dir)
        try:
            sock_path.unlink()
        except FileNotFoundError:
            pass
        self._sock_server = await asyncio.start_unix_server(
            self._handle_socket, path=str(sock_path)
        )
        # Seed the last known room BEFORE the first status write: the
        # write below would otherwise clobber daemon-status.json with
        # room_id=None and destroy the memory _login_and_join falls
        # back to after a recovery moved session.json aside (§13).
        try:
            _last = json.loads(
                state_path(DAEMON_STATUS_FILE, self.state_dir)
                .read_text(encoding="utf-8")
            )
            if _last.get("room_id"):
                self.room_id = _last["room_id"]
        except (OSError, ValueError):
            pass
        self.write_status()
        self._status_task = asyncio.create_task(self._status_writer())
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        try:
            await self._serve_forever()
        finally:
            if self._status_task is not None:
                self._status_task.cancel()
            if self._sock_server is not None:
                self._sock_server.close()
                await self._sock_server.wait_closed()
            try:
                sock_path.unlink()
            except FileNotFoundError:
                pass
            try:
                pid_path.unlink()
            except FileNotFoundError:
                pass

    async def _status_writer(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(STATUS_WRITE_INTERVAL_S)
            try:
                self.write_status()
            except OSError:
                pass


def run_daemon(relay_url: str, state_dir: Path | None = None) -> None:
    asyncio.run(Daemon(relay_url, state_dir).run())


# ---------------------------------------------------------------------
# CLI-side daemon control (spawn / stop / status)
# ---------------------------------------------------------------------

def daemon_pid(state_dir: Path | None = None) -> int | None:
    path = state_path(DAEMON_PID_FILE, state_dir)
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


async def _socket_request(req: dict, state_dir: Path, timeout: float = 15.0) -> dict:
    sock_path = state_path(DAEMON_SOCK_FILE, state_dir)
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(sock_path)), timeout=timeout
    )
    try:
        writer.write(json.dumps(req).encode("utf-8") + b"\n")
        await writer.drain()
        writer.write_eof()
        raw = await asyncio.wait_for(reader.read(), timeout=timeout)
    finally:
        writer.close()
    return json.loads(raw.decode("utf-8"))


def daemon_call(req: dict, state_dir: Path | None = None, timeout: float = 15.0) -> dict | None:
    """Send one JSON request to the daemon socket; None if unreachable."""
    state_dir = state_dir or get_state_dir()
    try:
        return asyncio.run(_socket_request(req, state_dir, timeout=timeout))
    except (OSError, asyncio.TimeoutError, ValueError, ConnectionError):
        return None


def daemon_status(state_dir: Path | None = None) -> dict:
    """Status via the live socket, falling back to daemon-status.json."""
    state_dir = state_dir or get_state_dir()
    live = daemon_call({"command": "status"}, state_dir)
    if live is not None and live.get("ok"):
        live["daemon"] = "running"
        return live
    status: dict = {"daemon": "stopped", "connected": False, "room_joined": False}
    path = state_path(DAEMON_STATUS_FILE, state_dir)
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            status.update(
                {
                    "session": saved.get("session", False),
                    "account_id_present": saved.get("account_id_present", False),
                    "room_id": saved.get("room_id"),
                    "room_members": saved.get("room_members", []),
                    "pending_room_messages": saved.get("pending_room_messages", 0),
                    "root_id_present": saved.get("root_id_present", False),
                }
            )
        except (OSError, ValueError):
            pass
    else:
        status.update(
            {
                "session": False,
                "account_id_present": False,
                "room_id": None,
                "room_members": [],
                "pending_room_messages": len(read_queue(state_dir)),
                "root_id_present": load_root_id(state_dir) is not None,
            }
        )
    if pid_alive(daemon_pid(state_dir)):
        status["daemon"] = "running"
    return status


def daemon_start(state_dir: Path | None = None) -> dict:
    """Spawn the background daemon (setsid, nohup-style, own log file)."""
    state_dir = state_dir or get_state_dir()
    relay_url = require_relay_url()
    if daemon_call({"command": "ping"}, state_dir, timeout=3.0) is not None:
        return {"ok": True, "already_running": True}
    log_path = state_path(DAEMON_LOG_FILE, state_dir)
    log_fh = open(log_path, "ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tools.muse_client", "daemon", "_run"],
            cwd=str(_repo_root()),
            env={**os.environ, "MUSE_UM_RELAY_URL": relay_url},
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_fh.close()
    # Wait briefly for the pid file / socket to appear.
    for _ in range(50):
        if daemon_call({"command": "ping"}, state_dir, timeout=1.0) is not None:
            return {"ok": True, "pid": proc.pid, "already_running": False}
        time.sleep(0.1)
    if proc.poll() is not None:
        return {"ok": False, "error": f"daemon exited immediately (see {log_path})"}
    return {"ok": True, "pid": proc.pid, "already_running": False}


def daemon_stop(state_dir: Path | None = None) -> dict:
    state_dir = state_dir or get_state_dir()
    resp = daemon_call({"command": "stop"}, state_dir, timeout=5.0)
    if resp is not None and resp.get("ok"):
        pid = daemon_pid(state_dir)
        for _ in range(50):
            if not pid_alive(pid):
                return {"ok": True, "via": "socket"}
            time.sleep(0.1)
        return {"ok": True, "via": "socket"}
    pid = daemon_pid(state_dir)
    if pid is not None and pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        for _ in range(50):
            if not pid_alive(pid):
                return {"ok": True, "via": "signal"}
            time.sleep(0.1)
        return {"ok": False, "error": "daemon did not stop"}
    return {"ok": False, "error": "daemon is not running"}
