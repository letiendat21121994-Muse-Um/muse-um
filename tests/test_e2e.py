#!/usr/bin/env python3
"""End-to-end test for muse-um — runs the REAL Relay, Panel and clients.

    .venv/bin/python tests/test_e2e.py [--keep-logs]

What it does (see PROTOCOL.md for the contract each step checks):
  1. Starts the Relay on 127.0.0.1:8000, waits for GET /health -> 200.
  2. Starts the Panel (root_dir = a temp sandbox), waits for
     GET /api/status -> panel_online true.
  3. Creates 2 accounts via the Panel HTTP API, creates a pair code for each.
  4. pair/login for 2 demo clients (each with its own MUSE_UM_STATE_DIR),
     daemon start for both, waits for connected: true.
  5. room create + invite + join; post/reply back and forth.
  6. room list --limit N returns the OLDEST N messages (PROTOCOL.md §4).
  7. invoke with EXACT result shapes (app/panel.py):
     write_file -> {"path","size","mode"}; read_file ->
     {"path","offset","length","total_size","encoding","content"};
     file_info -> {"path","type","size","mtime"}; list_dir ->
     {"path","entries":[{"name","type","size"?,"mtime"}]}; search ->
     {"query","matches":[{"path","line","text"}],"truncated"};
     edit_file -> {"path","replaced":1}; create_dir -> {"path","created":true};
     copy/move/rename -> {"src","dest"}; delete_file -> {"path","deleted":true};
     organise_preview -> {"path","total_files","groups","truncated"};
     run_command (short commands only) -> {"id","status":"finished",
     "stdout","stderr","returncode"}.
  8. daemon status never leaks secret values (PROTOCOL.md §15 wins over
     §11): it prints `root_id: set` (or empty) and `session: active`/`none`;
     the test asserts no root_*/ses_* value appears in its output. The
     current root_id is obtained only by invoking with a stale root and
     parsing it from the BAD_MESSAGE (PROTOCOL.md §6); the CLI retries
     once automatically.
  9. With the daemon stopped, `room list` still works (local file only)
     and CLI `invoke` still works (it opens its own WS and logs in with
     the saved token).
 10. Chunked upload of a ~600 KB file + SHA-256 verification on read-back
     (read_file encoding="base64" -> result["content"]).
 11. Healthy/stale/invalid-session restart, backup collisions, durable rejoin
     after a status rewrite, and recover-daemon.sh hook re-enabling.
 12. Unreachable Relay: login retries keep the daemon alive, status exposes
     errors/attempts, restart timeout is JSON, and stop interrupts backoff.
 13. Unix IPC endpoint/framing, deterministic Windows pipe names, and
     Windows service module import without pywin32 on Linux.
 14. GET /health -> 200 (re-checked at the end).

Every step prints [PASS]/[FAIL]; the exit code is non-zero if any step
fails. All child processes are terminated and temp dirs removed on exit.

Confirmed from the real code (read before writing these asserts):
  * Panel: app/panel.py — entry `python app/panel.py` / `python -m app.panel`;
    tool result shapes as in step 7 above; write_file needs `content` (or
    `content_base64`) + `mode`; edit_file args `old`/`new` and requires
    exactly one occurrence; search args `query` (+ `path`,
    `case_sensitive`, `recursive`); sandbox escape -> FORBIDDEN
    "path escapes sandbox"; run_command over 5 s returns
    {"status":"running"} and there is NO poll yet — so this test only
    uses short commands (echo) and asserts stdout/returncode; the
    coordinator will add a poll convention later.
  * Client: tools/muse_client/cli.py — `invoke` prints the tool result
    dict as JSON; `daemon status` prints `connected:`/`room_joined:` plus
    the masked `root_id: set`/empty and `session: active`/`none` lines;
    `room list` reads the local queue file only; CLI `invoke` logs in
    over its own WebSocket, so it works even when the daemon is down.
  * `daemon status` is checked only for `connected: true` /
    `room_joined: true` — never for root_id/session_id values.

Still assumed (the test fails loudly instead of guessing silently):
  * Relay entry point: `python -m relay.main` (relay/main.py was not
    present yet when this test was written).
  * How the Relay forwards a Panel invoke failure (ok:false
    invoke_result): as {"type":"error","code":...} or as an
    invoke_result with ok:false. Error-case asserts below accept either
    shape; the root-mismatch case is asserted strictly per PROTOCOL.md §6
    (Relay MUST return an error with the exact message).

Secrets (login tokens) are kept in memory only and NEVER printed.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable

RELAY_URL = os.environ.get("MUSE_UM_TEST_RELAY_URL", "http://127.0.0.1:8000")
WS_URL = RELAY_URL.replace("http://", "ws://").replace("https://", "wss://")
MSG_RE = re.compile(r"msg_[0-9a-f]{16}")
ROOM_RE = re.compile(r"room_conn_[0-9a-f]{16}")
ROOT_RE = re.compile(r"root_[0-9a-f]{8}")
SES_RE = re.compile(r"ses_[0-9a-f]{16}")
BAD_ROOT_RE = re.compile(
    r"root_id '([^']+)' does not match the open root '([^']+)'"
)

KEEP_LOGS = "--keep-logs" in sys.argv

# ---------------------------------------------------------------- steps ---
results: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""),
          flush=True)
    return ok


def summary() -> int:
    failed = [r for r in results if not r[1]]
    print(f"\n==== E2E summary: {len(results) - len(failed)}/{len(results)} passed ====",
          flush=True)
    for name, ok, detail in results:
        if not ok:
            print(f"  FAILED: {name} — {detail}", flush=True)
    return 1 if failed else 0


def last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ""


def tail(text: str, n: int = 300) -> str:
    return text.strip()[-n:]


# ---------------------------------------------------------------- http ----
def http_json(method: str, path: str, body: dict | None = None,
              timeout: float = 10.0) -> tuple[int, dict]:
    url = RELAY_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, {"raw": raw}
    except Exception as e:  # connection refused etc.
        return 0, {"error": str(e)}


def wait_for(fn, timeout: float, interval: float = 0.5, desc: str = ""):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    return last


# ---------------------------------------------------------------- procs ---
procs: list[subprocess.Popen] = []


def start_proc(argv: list[str], env: dict, log_path: Path, cwd: Path = REPO_ROOT):
    log_f = open(log_path, "ab")
    p = subprocess.Popen(argv, cwd=str(cwd), env=env,
                         stdout=log_f, stderr=subprocess.STDOUT)
    procs.append(p)
    return p


def stop_all():
    for p in procs:
        if p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
    procs.clear()


# ---------------------------------------------------------------- client --
def client_env(state_dir: Path) -> dict:
    env = dict(os.environ)
    env["MUSE_UM_RELAY_URL"] = RELAY_URL
    env["MUSE_UM_STATE_DIR"] = str(state_dir)
    return env


def run_client(state_dir: Path, *args: str, timeout: float = 60.0,
               extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = client_env(state_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run([PY, "-m", "tools.muse_client", *args],
                          cwd=str(REPO_ROOT), env=env, timeout=timeout,
                          capture_output=True, text=True)


def invoke_cli_json(state_dir: Path, tool: str, args: dict,
                    timeout: float = 60.0,
                    extra_args: tuple[str, ...] = ()) -> tuple[int, dict | None, str]:
    """Run CLI invoke; return (rc, parsed_result_dict_or_None, raw_text).

    The CLI prints the tool result dict as JSON (cli.py cmd_invoke), so
    the result shape can be asserted exactly (see app/panel.py)."""
    r = run_client(state_dir, "invoke", "--tool", tool, "--args",
                   json.dumps(args), *extra_args, timeout=timeout)
    parsed = None
    try:
        parsed = json.loads(r.stdout)
    except ValueError:
        parsed = None
    return r.returncode, parsed, r.stdout + r.stderr


def error_reply_info(reply: dict) -> tuple[str, str] | None:
    """Normalize an invoke failure reply to (code, message), or None.

    The Relay may forward a Panel failure either as {"type":"error",
    "code","message"} or as an invoke_result with ok:false (the Panel
    puts code+error in that payload). Accept both shapes."""
    if reply.get("type") == "error":
        return reply.get("code"), reply.get("message", "")
    if reply.get("type") == "invoke_result" and reply.get("ok") is False:
        return reply.get("code") or "BAD_MESSAGE", \
            reply.get("error") or str(reply.get("result"))
    return None


def daemon_connected(state_dir: Path) -> bool:
    """True if `daemon status` (or daemon-status.json) says connected: true."""
    try:
        out = run_client(state_dir, "daemon", "status", timeout=15.0)
        text = out.stdout + out.stderr
        if re.search(r"connected:\s*true", text):
            return True
    except Exception:
        pass
    try:
        st = json.loads((state_dir / "daemon-status.json").read_text())
        return bool(st.get("connected"))
    except Exception:
        return False


def daemon_room_joined(state_dir: Path) -> bool:
    try:
        out = run_client(state_dir, "daemon", "status", timeout=15.0)
        return bool(re.search(r"room_joined:\s*true", out.stdout + out.stderr))
    except Exception:
        return False


def wait_daemon_connected(state_dir: Path, timeout: float = 60.0) -> bool:
    return bool(wait_for(lambda: daemon_connected(state_dir), timeout,
                         interval=2.0))


def wait_queue_contains(state_dir: Path, msg_id: str, timeout: float = 20.0) -> bool:
    q = state_dir / "room-queue.jsonl"
    def _check():
        try:
            return msg_id in q.read_text(errors="replace")
        except Exception:
            return False
    return bool(wait_for(_check, timeout, interval=0.5))


# ------------------------------------------------------------- raw WS -----
async def _ws_rpc(ws, msg: dict, timeout: float = 30.0) -> dict:
    await ws.send(json.dumps(msg))
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
        data = json.loads(raw)
        if data.get("id") == msg.get("id"):
            return data
        # server pushes (room_message) are ignored here
    raise TimeoutError(f"no reply to {msg.get('type')} within {timeout}s")


async def _ws_wait_invoke_reply(ws, timeout: float = 30.0) -> dict:
    """Wait for the next invoke_result/error after an invoke.

    The reply is matched by TYPE, not by request id: the Panel's
    invoke_result carries the Panel's invoke_id, and the Relay may not
    echo the client's request id (see client.py _on_message). The test
    only ever has one invoke in flight, so the next invoke_result/error
    is the answer to it. Room pushes are ignored."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
        data = json.loads(raw)
        if data.get("type") in ("invoke_result", "error"):
            return data
    raise TimeoutError("no invoke_result within 30s")


async def ws_login_and_invoke(token: str, root_id: str, tool: str,
                              args: dict) -> tuple[dict, dict]:
    """Log in over a raw WebSocket with `token`, invoke once, return
    (login_result, invoke_reply). The WS session is throwaway: the socket is
    simply closed afterwards (no `close` on the live daemon sessions)."""
    import websockets
    async with websockets.connect(WS_URL, max_size=1_048_576) as ws:
        login = await _ws_rpc(ws, {"type": "login", "id": "t-login",
                                   "token": token, "via_account_login": True})
        await ws.send(json.dumps({"type": "invoke", "id": "t-invoke",
                                  "root_id": root_id, "tool": tool,
                                  "args": args}))
        reply = await _ws_wait_invoke_reply(ws)
        return login, reply


# ---------------------------------------------------------------- main ----
def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="muse-um-e2e-"))
    root_dir = tmp / "demo-root"
    root_dir.mkdir()
    log_dir = tmp / "logs"
    log_dir.mkdir()
    state_a = tmp / "state-a"
    state_b = tmp / "state-b"
    state_a.mkdir()
    state_b.mkdir()
    print(f"temp dir: {tmp} (removed on exit unless --keep-logs)", flush=True)
    print(f"relay: {RELAY_URL}  root_dir: {root_dir}", flush=True)

    relay = panel = None
    try:
        # --- 1. Relay ------------------------------------------------------
        relay_argv = None
        if (REPO_ROOT / "relay" / "main.py").exists():
            relay_argv = [PY, "-m", "relay.main"]
        if relay_argv is None:
            step("relay start", False,
                 "relay/main.py not found — Relay not built yet")
            return summary()
        env = dict(os.environ, RELAY_HOST="127.0.0.1", PYTHONPATH=str(REPO_ROOT))
        relay = start_proc(relay_argv, env, log_dir / "relay.log")
        ok = bool(wait_for(
            lambda: http_json("GET", "/health")[0] == 200, 30, 0.5))
        if not step("relay start + GET /health -> 200", ok,
                    "relay did not answer /health within 30s; see logs/relay.log"
                    if not ok else ""):
            return summary()

        # --- 2. Panel ------------------------------------------------------
        panel_argv = None
        panel_override = os.environ.get("MUSE_UM_TEST_PANEL_CMD")
        if panel_override:
            panel_argv = panel_override.split()
        else:
            for cand in ([PY, "app/panel.py"], [PY, "-m", "app.panel"],
                         [PY, "app/main.py"], [PY, "-m", "app.main"]):
                target = cand[1] if cand[1] != "-m" else None
                mod = cand[2] if cand[1] == "-m" else None
                exists = False
                if target and (REPO_ROOT / target).exists():
                    exists = True
                if mod and (REPO_ROOT / Path(*mod.split("."))).with_suffix(".py").exists():
                    exists = True
                if exists:
                    panel_argv = cand
                    break
        if panel_argv is None:
            step("panel start", False,
                 "no Panel entry point found (looked for app/main.py, "
                 "app/panel.py, app.main, app.panel; override with "
                 "MUSE_UM_TEST_PANEL_CMD) — Panel not built yet")
            return summary()
        env = dict(os.environ, MUSE_UM_ROOT_DIR=str(root_dir),
                   MUSE_UM_RELAY_URL=RELAY_URL, PYTHONPATH=str(REPO_ROOT))
        panel = start_proc(panel_argv, env, log_dir / "panel.log")
        ok = bool(wait_for(
            lambda: http_json("GET", "/api/status")[1].get("panel_online") is True,
            30, 0.5))
        if not step("panel start + /api/status panel_online=true", ok,
                    "panel did not come online within 30s; see logs/panel.log"
                    if not ok else ""):
            return summary()

        # --- 3. Two accounts + pair codes via HTTP API --------------------
        accounts = {}
        for name in ("Demo A", "Demo B"):
            code_http, acc = http_json("POST", "/api/accounts", {"name": name})
            good = code_http == 200 and acc.get("account_id", "").startswith("acc_") \
                and acc.get("token")
            step(f"create account {name} via POST /api/accounts", good,
                 f"HTTP {code_http}" if not good else acc["account_id"])
            if not good:
                return summary()
            accounts[name] = acc
        acc_a, acc_b = accounts["Demo A"], accounts["Demo B"]

        codes = {}
        for name, acc in (("A", acc_a), ("B", acc_b)):
            code_http, pair = http_json("POST", "/api/pair",
                                        {"account_id": acc["account_id"]})
            good = code_http == 200 and re.fullmatch(r"[A-HJ-NP-Z2-9]{8}",
                                                      pair.get("code", "")) \
                and pair.get("expires_in") == 300
            step(f"pair code for account {name}", good,
                 f"HTTP {code_http}: {pair}" if not good else "code issued (8 chars, 300s)")
            if not good:
                return summary()
            codes[name] = pair["code"]

        # --- 4. pair + daemon start for both clients -----------------------
        for name, state in (("A", state_a), ("B", state_b)):
            r = run_client(state, "pair", "--code", codes[name])
            if not step(f"client {name}: pair --code", r.returncode == 0,
                        last_line(r.stdout + r.stderr) if r.returncode != 0 else ""):
                return summary()
            r = run_client(state, "daemon", "start")
            if not step(f"client {name}: daemon start", r.returncode == 0,
                        tail(r.stdout + r.stderr) if r.returncode != 0 else ""):
                return summary()
            ok = wait_daemon_connected(state, timeout=60)
            if not step(f"client {name}: daemon connected:true", ok,
                        "" if ok else "daemon did not connect within 60s"):
                return summary()

        # --- 4b. daemon status must not leak secret values (§15) ----------
        # It prints `root_id: set` (or empty) and `session: active`/`none`,
        # NEVER the root_id/session_id values. Only connected:/room_joined:
        # are asserted as values.
        r = run_client(state_a, "daemon", "status", timeout=15.0)
        status_text = r.stdout + r.stderr
        root_line = re.search(r"^root_id: *(set)?\s*$", status_text, re.M)
        leak = bool(ROOT_RE.search(status_text) or SES_RE.search(status_text))
        no_leak_ok = (not leak and "session: active" in status_text
                      and root_line is not None)
        step("daemon status leaks no root_id/session_id values",
             no_leak_ok, "" if no_leak_ok else tail(status_text))

        # --- 5. room create + invite + join --------------------------------
        r = run_client(state_a, "room", "create", "--name", "e2e-room")
        m = ROOM_RE.search(r.stdout + r.stderr)
        room_id = m.group(0) if m else ""
        if not step("room create (client A)", bool(room_id),
                    (r.stdout + r.stderr).strip()[-300:] if not room_id else room_id):
            return summary()
        # NOTE: create does NOT join at the daemon/CLI level (daemon.py:
        # the create handler returns the room_id without setting the
        # daemon's current room; the Relay does add the creator as a
        # member, but `room post` checks the daemon's room). The creator
        # must explicitly join before posting — same as every other member.
        r = run_client(state_a, "room", "join", "--room-id", room_id)
        if not step("room join (client A, the creator)", r.returncode == 0,
                    (r.stdout + r.stderr).strip()[-300:] if r.returncode != 0 else ""):
            return summary()
        r = run_client(state_a, "room", "invite", "--room-id", room_id,
                       "--account-id", acc_b["account_id"])
        if not step("room invite B", r.returncode == 0,
                    (r.stdout + r.stderr).strip()[-300:] if r.returncode != 0 else ""):
            return summary()
        r = run_client(state_b, "room", "join", "--room-id", room_id)
        if not step("room join (client B)", r.returncode == 0,
                    (r.stdout + r.stderr).strip()[-300:] if r.returncode != 0 else ""):
            return summary()
        ok = wait_for(lambda: daemon_room_joined(state_b), 30, 1.0)
        step("client B: room_joined:true", bool(ok),
             "" if ok else "B did not report room_joined within 30s")

        # --- 6. post / reply back and forth --------------------------------
        r = run_client(state_a, "room", "post", "--text", "hello from A")
        m = MSG_RE.search(r.stdout + r.stderr)
        post_a = m.group(0) if m else ""
        if not step("room post (A)", bool(post_a),
                    (r.stdout + r.stderr).strip()[-300:] if not post_a else post_a):
            return summary()
        ok = wait_queue_contains(state_b, post_a)
        step("B receives A's post in room-queue.jsonl", ok,
             "" if ok else f"{post_a} not in B queue within 20s")
        if not ok:
            return summary()
        r = run_client(state_b, "room", "list", "--limit", "10")
        step("B room list sees A's post", post_a in (r.stdout + r.stderr),
             "" if post_a in (r.stdout + r.stderr) else (r.stdout + r.stderr).strip()[-300:])
        r = run_client(state_b, "room", "reply", "--id", post_a,
                       "--text", "hi from B")
        m = MSG_RE.search(r.stdout + r.stderr)
        reply_b = m.group(0) if m else ""
        if not step("room reply (B -> A)", bool(reply_b),
                    (r.stdout + r.stderr).strip()[-300:] if not reply_b else reply_b):
            return summary()
        ok = wait_queue_contains(state_a, reply_b)
        step("A receives B's reply in room-queue.jsonl", ok,
             "" if ok else f"{reply_b} not in A queue within 20s")
        if not ok:
            return summary()
        r = run_client(state_a, "room", "reply", "--id", reply_b,
                       "--text", "ack from A")
        m = MSG_RE.search(r.stdout + r.stderr)
        reply_a2 = m.group(0) if m else ""
        step("room reply (A -> B)", bool(reply_a2),
             (r.stdout + r.stderr).strip()[-300:] if not reply_a2 else reply_a2)
        if not reply_a2:
            return summary()

        # --- 7. room list --limit N returns the OLDEST N -------------------
        extra = []
        for text in ("oldest-check-1", "oldest-check-2", "oldest-check-3"):
            r = run_client(state_a, "room", "post", "--text", text)
            m = MSG_RE.search(r.stdout + r.stderr)
            if m:
                extra.append(m.group(0))
        ok = len(extra) == 3 and wait_queue_contains(state_b, extra[-1])
        step("post 3 more messages (A)", ok, "" if ok else "posts failed")
        if not ok:
            return summary()
        # B's queue (foreign messages only) is, in time order:
        #   post_a, reply_a2, extra[0], extra[1], extra[2]
        r = run_client(state_b, "room", "list", "--limit", "2")
        found = MSG_RE.findall(r.stdout + r.stderr)
        step("room list --limit 2 returns OLDEST 2",
             found[:2] == [post_a, reply_a2],
             f"got {found[:2]}, expected [{post_a}, {reply_a2}]")

        # --- 8. invoke: exact result shapes (app/panel.py) ---------------
        # BOOTSTRAP: no root-id has ever been stored in this fresh state
        # dir, so a plain invoke fails before sending ("no root_id
        # available", client.py). The documented bootstrap is one invoke
        # with an explicit bogus --root-id: the Relay answers BAD_MESSAGE
        # naming the current root, the client parses it, saves it, and
        # retries once — which succeeds. From then on the stored root is
        # used. (Sandbox is still empty, so list_dir "." is [].)
        r = run_client(state_a, "invoke", "--tool", "list_dir", "--args",
                       json.dumps({"path": "."}),
                       "--root-id", "root_00000000")
        try:
            boot_res = json.loads(r.stdout)
        except ValueError:
            boot_res = None
        if not step("bootstrap root-id: invoke with bogus --root-id retries once",
                    r.returncode == 0 and boot_res == {"path": ".",
                                                       "entries": []},
                    tail(r.stdout + r.stderr)
                    if r.returncode != 0 else f"res={boot_res}"):
            return summary()
        # CLI invoke prints the tool result dict as JSON (cli.py), so each
        # shape below is asserted exactly. "hello e2e\n" is 10 bytes.
        rc, res, raw = invoke_cli_json(
            state_a, "write_file",
            {"path": "hello.txt", "mode": "create", "content": "hello e2e\n"})
        if not step("invoke write_file hello.txt",
                    rc == 0 and res == {"path": "hello.txt", "size": 10,
                                        "mode": "create"},
                    f"rc={rc} res={res}" if rc != 0 or res != {"path": "hello.txt", "size": 10, "mode": "create"} else ""):
            return summary()
        rc, res, raw = invoke_cli_json(state_a, "read_file",
                                       {"path": "hello.txt"})
        if not step("invoke read_file -> exact shape + content",
                    rc == 0 and isinstance(res, dict)
                    and res.get("content") == "hello e2e\n"
                    and res.get("total_size") == 10
                    and res.get("length") == 10
                    and res.get("offset") == 0
                    and res.get("encoding") == "text",
                    f"rc={rc} res={res}"):
            return summary()
        rc, res, raw = invoke_cli_json(state_a, "file_info",
                                       {"path": "hello.txt"})
        if not step("invoke file_info -> exact shape",
                    rc == 0 and isinstance(res, dict)
                    and res.get("path") == "hello.txt"
                    and res.get("type") == "file"
                    and res.get("size") == 10 and "mtime" in res,
                    f"rc={rc} res={res}"):
            return summary()
        rc, res, raw = invoke_cli_json(state_a, "list_dir", {"path": "."})
        step("invoke list_dir -> entries contain hello.txt",
             rc == 0 and isinstance(res, dict) and res.get("path") == "."
             and any(e.get("name") == "hello.txt" and e.get("type") == "file"
                     and e.get("size") == 10
                     for e in res.get("entries", [])),
             f"rc={rc} res={res}")
        rc, res, raw = invoke_cli_json(
            state_a, "edit_file",
            {"path": "hello.txt", "old": "hello", "new": "HELLO"})
        if not step("invoke edit_file -> replaced: 1",
                    rc == 0 and res == {"path": "hello.txt", "replaced": 1},
                    f"rc={rc} res={res}"):
            return summary()
        rc, res, raw = invoke_cli_json(state_a, "read_file",
                                       {"path": "hello.txt"})
        step("read_file after edit shows HELLO e2e",
             rc == 0 and isinstance(res, dict)
             and res.get("content") == "HELLO e2e\n",
             f"rc={rc} res={res}")
        rc, res, raw = invoke_cli_json(state_a, "search",
                                       {"query": "HELLO", "path": "."})
        step("invoke search finds HELLO in hello.txt:1",
             rc == 0 and isinstance(res, dict)
             and res.get("query") == "HELLO"
             and res.get("matches") == [{"path": "hello.txt", "line": 1,
                                         "text": "HELLO e2e"}]
             and res.get("truncated") is False,
             f"rc={rc} res={res}")
        rc, res, raw = invoke_cli_json(state_a, "create_dir",
                                       {"path": "sub"})
        step("invoke create_dir sub",
             rc == 0 and res == {"path": "sub", "created": True},
             f"rc={rc} res={res}")
        rc, res, raw = invoke_cli_json(
            state_a, "copy_file",
            {"src": "hello.txt", "dest": "sub/copy.txt"})
        step("invoke copy_file hello.txt -> sub/copy.txt",
             rc == 0 and res == {"src": "hello.txt", "dest": "sub/copy.txt"},
             f"rc={rc} res={res}")
        rc, res, raw = invoke_cli_json(
            state_a, "move_file",
            {"src": "sub/copy.txt", "dest": "sub/moved.txt"})
        step("invoke move_file sub/copy.txt -> sub/moved.txt",
             rc == 0 and res == {"src": "sub/copy.txt",
                                 "dest": "sub/moved.txt"},
             f"rc={rc} res={res}")
        rc, res, raw = invoke_cli_json(
            state_a, "rename",
            {"src": "sub/moved.txt", "new_name": "renamed.txt"})
        step("invoke rename sub/moved.txt -> sub/renamed.txt",
             rc == 0 and res == {"src": "sub/moved.txt",
                                 "dest": "sub/renamed.txt"},
             f"rc={rc} res={res}")
        rc, res, raw = invoke_cli_json(state_a, "organise_preview",
                                       {"path": "."})
        step("invoke organise_preview groups .txt files",
             rc == 0 and isinstance(res, dict)
             and "hello.txt" in res.get("groups", {}).get(".txt", [])
             and "sub/renamed.txt" in res.get("groups", {}).get(".txt", [])
             and res.get("total_files", 0) >= 2,
             f"rc={rc} res={res}")
        rc, res, raw = invoke_cli_json(
            state_a, "delete_file", {"path": "sub/renamed.txt"})
        step("invoke delete_file sub/renamed.txt",
             rc == 0 and res == {"path": "sub/renamed.txt", "deleted": True},
             f"rc={rc} res={res}")
        # run_command: short command finishes inline; a long command
        # returns {"status":"running"} and is polled with
        # run_command {"id": ...} (no "command") — PROTOCOL.md §6.
        rc, res, raw = invoke_cli_json(state_a, "run_command",
                                       {"command": "echo e2e-ok"})
        step("invoke run_command (short) -> finished, stdout + returncode 0",
             rc == 0 and isinstance(res, dict)
             and res.get("status") == "finished"
             and res.get("returncode") == 0
             and "e2e-ok" in res.get("stdout", ""),
             f"rc={rc} res={res}")
        rc, res, raw = invoke_cli_json(state_a, "run_command",
                                       {"command": "sleep 8 && echo poll-ok"})
        long_id = res.get("id") if isinstance(res, dict) else None
        step("invoke run_command (long) -> running with id",
             rc == 0 and isinstance(res, dict)
             and res.get("status") == "running" and bool(long_id),
             f"rc={rc} res={res}")
        rc, res, raw = invoke_cli_json(state_a, "run_command",
                                       {"id": long_id})
        step("run_command poll while running -> status running",
             rc == 0 and isinstance(res, dict)
             and res.get("status") == "running",
             f"rc={rc} res={res}")
        time.sleep(9)
        rc, res, raw = invoke_cli_json(state_a, "run_command",
                                       {"id": long_id})
        step("run_command poll after finish -> finished, stdout poll-ok",
             rc == 0 and isinstance(res, dict)
             and res.get("status") == "finished"
             and res.get("returncode") == 0
             and "poll-ok" in res.get("stdout", ""),
             f"rc={rc} res={res}")

        # --- 8b. CLI invoke with a stale root retries once (client.py) ----
        r = run_client(state_a, "invoke", "--tool", "file_info", "--args",
                       json.dumps({"path": "hello.txt"}),
                       "--root-id", "root_00000000")
        try:
            retry_res = json.loads(r.stdout)
        except ValueError:
            retry_res = None
        step("CLI invoke with stale --root-id retries once and succeeds",
             r.returncode == 0 and isinstance(retry_res, dict)
             and retry_res.get("size") == 10,
             tail(r.stdout + r.stderr)
             if r.returncode != 0 else f"res={retry_res}")

        # --- 9. invoke with a wrong root-id -> exact BAD_MESSAGE -----------
        login_res, reply = asyncio.run(
            ws_login_and_invoke(acc_a["token"], "root_00000000",
                                "file_info", {"path": "hello.txt"}))
        m = BAD_ROOT_RE.search(reply.get("message", "")) \
            if reply.get("type") == "error" else None
        step("invoke with wrong root-id -> BAD_MESSAGE with exact message",
             reply.get("type") == "error"
             and reply.get("code") == "BAD_MESSAGE" and bool(m)
             and m.group(1) == "root_00000000"
             and ROOT_RE.fullmatch(m.group(2)) is not None,
             f"got {reply}")
        if m:
            # The current root parsed from the error message works.
            _login2, reply2 = asyncio.run(
                ws_login_and_invoke(acc_a["token"], m.group(2),
                                    "file_info", {"path": "hello.txt"}))
            step("invoke with root-id parsed from BAD_MESSAGE succeeds",
                 reply2.get("type") == "invoke_result"
                 and reply2.get("ok") is True,
                 f"got {reply2}")
            current_root = m.group(2)
        else:
            current_root = ""
        # Sandbox escape -> FORBIDDEN "path escapes sandbox" (panel.py).
        _l, rep = asyncio.run(ws_login_and_invoke(
            acc_a["token"], current_root,
            "read_file", {"path": "../escape.txt"}))
        err = error_reply_info(rep)
        step("invoke read_file ../escape.txt -> FORBIDDEN 'path escapes sandbox'",
             err is not None and err[0] == "FORBIDDEN"
             and err[1] == "path escapes sandbox",
             f"got {rep}")
        # write_file without mode -> BAD_MESSAGE (PROTOCOL.md §6).
        _l, rep = asyncio.run(ws_login_and_invoke(
            acc_a["token"], current_root,
            "write_file", {"path": "x.txt", "content": "x"}))
        err = error_reply_info(rep)
        step("invoke write_file without mode -> BAD_MESSAGE",
             err is not None and err[0] == "BAD_MESSAGE"
             and "mode" in err[1],
             f"got {rep}")

        # --- 9b. room list + CLI invoke work with the daemon down --------
        # room list reads the local queue file only; CLI invoke opens its
        # own WS and logs in with the saved token (cli.py).
        r = run_client(state_b, "daemon", "stop")
        step("daemon stop (client B)", r.returncode == 0,
             tail(r.stdout + r.stderr) if r.returncode != 0 else "")
        r = run_client(state_b, "room", "list", "--limit", "10")
        step("room list works with daemon down (local file only)",
             r.returncode == 0 and post_a in r.stdout,
             tail(r.stdout + r.stderr)
             if not (r.returncode == 0 and post_a in r.stdout) else "")
        # B has never invoked before, so no root-id is stored in B's state
        # dir yet: the bootstrap (explicit bogus --root-id, Relay answers
        # BAD_MESSAGE with the current root, client retries once) is
        # needed here too — it also proves the retry path works with the
        # daemon down.
        rc, res, raw = invoke_cli_json(
            state_b, "file_info", {"path": "hello.txt"},
            extra_args=("--root-id", "root_00000000"))
        step("CLI invoke works with daemon down (own WS + saved token)",
             rc == 0 and isinstance(res, dict) and res.get("size") == 10,
             f"rc={rc} res={res} {tail(raw)}")
        r = run_client(state_b, "daemon", "start")
        step("daemon start again (client B)", r.returncode == 0,
             tail(r.stdout + r.stderr) if r.returncode != 0 else "")
        ok = wait_daemon_connected(state_b, timeout=60)
        step("client B: daemon connected:true again", ok,
             "" if ok else "B did not reconnect within 60s")

        # --- 10. chunked upload ~600 KB + SHA-256 ---------------------------
        rng = random.Random(42)
        payload = bytes(rng.randrange(256) for _ in range(600 * 1024))
        digest = hashlib.sha256(payload).hexdigest()
        local_big = tmp / "big.bin"
        local_big.write_bytes(payload)
        r = run_client(state_a, "invoke", "--tool", "write_file", "--args",
                       json.dumps({"path": "big.bin", "mode": "create"}),
                       "--chunked-file", str(local_big), timeout=120.0)
        try:
            up_res = json.loads(r.stdout)
        except ValueError:
            up_res = None
        if not step("chunked upload big.bin (~600 KB, 3 x 256 KiB chunks)",
                    r.returncode == 0 and isinstance(up_res, dict)
                    and up_res.get("path") == "big.bin"
                    and up_res.get("size") == 614400
                    and up_res.get("mode") == "create",
                    f"rc={r.returncode} res={up_res} {tail(r.stdout + r.stderr)}"):
            return summary()
        rc, res, raw = invoke_cli_json(state_a, "file_info",
                                       {"path": "big.bin"})
        step("file_info big.bin size is 614400",
             rc == 0 and isinstance(res, dict) and res.get("size") == 614400,
             f"rc={rc} res={res}")

        async def _read_back() -> bytes:
            import websockets
            chunks = []
            async with websockets.connect(WS_URL, max_size=1_048_576) as ws:
                await _ws_rpc(ws, {"type": "login", "id": "rb-login",
                                   "token": acc_a["token"],
                                   "via_account_login": True})
                offset = 0
                while offset < len(payload):
                    length = min(256 * 1024, len(payload) - offset)
                    await ws.send(json.dumps(
                        {"type": "invoke", "id": f"rb-{offset}",
                         "root_id": m.group(2) if m else "",
                         "tool": "read_file",
                         "args": {"path": "big.bin", "offset": offset,
                                  "length": length, "encoding": "base64"}}))
                    rep = await _ws_wait_invoke_reply(ws)
                    if rep.get("type") != "invoke_result" or not rep.get("ok"):
                        raise RuntimeError(f"read_file failed at {offset}: {rep}")
                    res = rep.get("result")
                    # Panel read_file base64 shape: {"content": "<base64>", ...}
                    b64 = res.get("content") if isinstance(res, dict) else None
                    if b64 is None and isinstance(res, str):
                        b64 = res
                    if b64 is None:
                        raise RuntimeError(f"no base64 payload in {rep}")
                    data = base64.b64decode(b64)
                    chunks.append(data)
                    offset += len(data)
                    if not data:
                        break
            return b"".join(chunks)

        try:
            back = asyncio.run(_read_back())
            step("chunked read-back SHA-256 matches upload",
                 hashlib.sha256(back).hexdigest() == digest
                 and len(back) == len(payload),
                 f"got {len(back)} bytes, sha256={hashlib.sha256(back).hexdigest()[:16]}...")
        except Exception as e:
            step("chunked read-back SHA-256 matches upload", False, str(e))

        # --- 11. recovery (PROTOCOL.md §13) --------------------------------
        session_path = state_b / "session.json"
        old_pid = (state_b / "daemon.pid").read_text()
        for invalid_timeout in ("0", "-1"):
            r = run_client(state_b, "daemon", "restart", "--timeout", invalid_timeout)
            failure = json.loads(r.stdout)
            status = json.loads(run_client(state_b, "daemon", "status", "--json").stdout)
            step(f"restart timeout {invalid_timeout} rejected without stopping healthy daemon",
                 r.returncode == 1 and not failure.get("ok") and bool(failure.get("error"))
                 and (state_b / "daemon.pid").read_text() == old_pid
                 and status.get("connected") and status.get("room_joined"), tail(r.stdout))
        no_relay_env = client_env(state_b)
        no_relay_env.pop("MUSE_UM_RELAY_URL", None)
        r = subprocess.run(
            [PY, "-m", "tools.muse_client", "daemon", "restart"],
            cwd=str(REPO_ROOT), env=no_relay_env,
            capture_output=True, text=True, timeout=15)
        failure = json.loads(r.stdout)
        status = json.loads(run_client(state_b, "daemon", "status", "--json").stdout)
        step("restart without relay URL returns error and leaves running daemon connected",
             r.returncode == 1 and not failure.get("ok") and bool(failure.get("error"))
             and (state_b / "daemon.pid").read_text() == old_pid
             and status.get("connected") and status.get("room_joined"), tail(r.stdout))
        before = session_path.read_bytes()
        previous_session = json.loads(before)
        previous_backups = set(state_b.glob("session.json.bak-*"))
        old_pid = (state_b / "daemon.pid").read_text()
        r = run_client(state_b, "daemon", "restart", "--timeout", "25")
        restart = json.loads(r.stdout)
        current_session = json.loads(session_path.read_bytes())
        step("healthy restart preserves room state without backup and returns ok JSON",
             r.returncode == 0 and restart.get("ok")
             and restart.get("session_backup") is None
             and set(state_b.glob("session.json.bak-*")) == previous_backups
             and all(current_session.get(key) == previous_session.get(key)
                     for key in ("room_id", "last_room_id")), tail(r.stdout + r.stderr))
        before = session_path.read_bytes()
        status = json.loads(run_client(state_b, "daemon", "status", "--json").stdout)
        step("restart replaces old pid and leaves daemon running",
             (state_b / "daemon.pid").read_text() != old_pid
             and status.get("daemon") == "running" and status.get("connected")
             and status.get("room_joined"))

        run_client(state_b, "daemon", "stop")
        (state_b / "daemon-status.json").write_text('{"room_id": null}')
        r = run_client(state_b, "daemon", "restart", "--timeout", "25")
        restart = json.loads(r.stdout)
        status = json.loads(run_client(state_b, "daemon", "status", "--json").stdout)
        backup = state_b / (restart.get("session_backup") or "missing-backup")
        step("stale restart backs up session, rejoins same room in <30s despite status rewrite",
             r.returncode == 0 and restart.get("ok") and restart.get("connected")
             and restart.get("room_joined") and restart.get("elapsed_s", 99) < 30
             and backup.is_file() and backup.read_bytes() == before
             and status.get("room_id") == room_id, tail(r.stdout + r.stderr))

        for invalid in ('not JSON', '[]'):
            run_client(state_b, "daemon", "stop")
            session_path.write_text(invalid)
            r = run_client(state_b, "daemon", "restart", "--timeout", "25")
            restart = json.loads(r.stdout)
            new_backup = state_b / (restart.get("session_backup") or "missing-backup")
            status = json.loads(run_client(state_b, "daemon", "status", "--json").stdout)
            step(f"invalid session {invalid!r}: backup preserved and same room recovered",
                 r.returncode == 0 and restart.get("ok") and new_backup != backup
                 and new_backup.read_text() == invalid and backup.read_bytes() == before
                 and status.get("room_id") == room_id, tail(r.stdout + r.stderr))

        # Không cần pair; hook luôn được bật lại sau khôi phục.
        rec = subprocess.run(
            ["bash", str(REPO_ROOT / "hooks" / "recover-daemon.sh")],
            cwd=str(REPO_ROOT), env=client_env(state_b),
            capture_output=True, text=True, timeout=120.0)
        step("recover-daemon.sh for client B exits 0",
             rec.returncode == 0 and not (state_b / "hooks.disabled").exists(),
             tail(rec.stdout + rec.stderr) if rec.returncode != 0 else "")
        ok = wait_daemon_connected(state_b, timeout=30)
        joined = False
        try:
            st = run_client(state_b, "daemon", "status", timeout=15.0)
            joined = bool(re.search(r"room_joined:\s*true",
                                    st.stdout + st.stderr))
        except Exception:
            pass
        step("after recovery: client B connected:true + room_joined:true",
             ok and joined, "connected=%s joined=%s" % (ok, joined))

        # --- 12. Failed login remains supervised until explicitly stopped --
        scratch = tmp / "state-retry"
        scratch.mkdir()
        unreachable = {"MUSE_UM_RELAY_URL": "http://127.0.0.1:1"}
        try:
            r = run_client(scratch, "daemon", "start", extra_env=unreachable)
            log_path = scratch / "daemon.log"
            retried = wait_for(lambda: log_path.exists() and
                               log_path.read_text().count("retrying in") >= 2, 15)
            status = json.loads(run_client(scratch, "daemon", "status", "--json").stdout)
            step("login retry: unreachable relay stays alive with at least two retrying in lines",
                 r.returncode == 0 and bool(retried) and status.get("daemon") == "running"
                 and status.get("login_attempts", 0) >= 2 and bool(status.get("last_error")))
            r = run_client(scratch, "daemon", "restart", "--timeout", "8", extra_env=unreachable)
            failure = json.loads(r.stdout)
            step("restart timeout returns one error JSON with missing flags and last login error",
                 r.returncode == 1 and not failure.get("ok")
                 and not failure.get("connected") and not failure.get("room_joined")
                 and failure.get("session_backup") is None
                 and all(s in failure.get("error", "") for s in
                         ("connected", "room_joined", "last login error")), tail(r.stdout))
            started_stop = time.monotonic()
            r = run_client(scratch, "daemon", "stop")
            status = json.loads(run_client(scratch, "daemon", "status", "--json").stdout)
            step("login retry: stop interrupts backoff cleanly",
                 r.returncode == 0 and time.monotonic() - started_stop < 3
                 and status.get("daemon") == "stopped")
        finally:
            run_client(scratch, "daemon", "stop")

        # --- 13. IPC and Windows service groundwork ------------------------
        ipc_state = tmp / "state-ipc"
        ipc_state.mkdir()
        check = subprocess.run(
            [PY, "-c", '''
import asyncio
import json
import sys
from pathlib import Path
from tools.muse_client import ipc, winservice
from tools.muse_client.daemon import Daemon, _last_room

state = Path(sys.argv[1])
other = state.parent / "other-state-ipc"
(state / "session.json.bak-test").write_text('{"room_id": "backup-room"}')
(state / "daemon-status.json").write_text('{"room_id": "legacy-room"}')
(state / "session.json").write_text('{"room_id": null}')
existing_room = _last_room(state, {"room_id": None})
(state / "session.json").unlink()
absent_room = _last_room(state, {})
checks = {
    "room_backup_scope": existing_room == "legacy-room" and absent_room == "backup-room",
    "unix_endpoint": ipc.endpoint(state) == str(state / "daemon.sock"),
    "pipe_names": (ipc.pipe_name(state).startswith("\\\\\\\\.\\\\pipe\\\\muse-um-")
                   and ipc.pipe_name(state) == ipc.pipe_name(state / ".")
                   and ipc.pipe_name(state) != ipc.pipe_name(other)
                   and len(ipc.pipe_name(state).rsplit("-", 1)[1]) == 16),
    "service_import": callable(winservice.main) and "win32serviceutil" not in sys.modules,
}

async def framing():
    (state / "daemon.sock").touch()
    daemon = Daemon("http://127.0.0.1:1", state)
    server = await ipc.start_server(daemon._handle_socket, state)
    replies = []
    try:
        for request in (json.dumps({"command": "ping", "padding": "x" * 80000}), "{bad", ""):
            reader, writer = await ipc.open_connection(state, 2)
            try:
                writer.write((request + "\\n").encode())
                await writer.drain()
                replies.append(json.loads(await asyncio.wait_for(reader.readline(), 2)))
            finally:
                writer.close()
                await writer.wait_closed()
    finally:
        await ipc.close_server(server, state)
    return (replies[0].get("pong") and not replies[1].get("ok")
            and not replies[2].get("ok") and not (state / "daemon.sock").exists())

async def startup_status():
    legacy_state = state / "legacy"
    legacy_state.mkdir()
    (legacy_state / "daemon-status.json").write_text('{"room_id": "legacy-room"}')
    daemon = Daemon("http://127.0.0.1:1", legacy_state)
    observed = {}

    async def capture_startup():
        observed.update(json.loads((legacy_state / "daemon-status.json").read_text()))

    daemon._serve_forever = capture_startup
    await daemon.run()
    return observed.get("room_id") == "legacy-room" and not observed.get("room_joined")

checks["legacy_status"] = asyncio.run(startup_status())
checks["framing"] = asyncio.run(framing())
print(json.dumps(checks))
''', str(ipc_state)],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=15)
        checks = json.loads(check.stdout) if check.returncode == 0 else {}
        detail = tail(check.stdout + check.stderr)
        step("room backups used only when session.json is absent",
             checks.get("room_backup_scope", False), detail)
        step("initial status write preserves legacy room without claiming joined",
             checks.get("legacy_status", False), detail)
        step("IPC endpoint: Unix daemon.sock path", checks.get("unix_endpoint", False), detail)
        step("Windows pipe names: deterministic, prefixed, unique per state dir",
             checks.get("pipe_names", False), detail)
        step("winservice imports on Linux without pywin32", checks.get("service_import", False), detail)
        step("IPC newline framing: no half-close, large request, invalid/empty JSON, cleanup",
             checks.get("framing", False), detail)

        # --- 14. /health again at the end -----------------------------------
        code_http, health = http_json("GET", "/health")
        step("GET /health -> 200 at end",
             code_http == 200 and health.get("status") == "ok",
             f"HTTP {code_http}: {health}")

        return summary()
    finally:
        # Daemons first (clean shutdown via the CLI), then Relay/Panel.
        for state in (state_a, state_b):
            try:
                run_client(state, "daemon", "stop", timeout=15.0)
            except Exception:
                pass
        stop_all()
        if KEEP_LOGS:
            print(f"logs kept in {tmp}", flush=True)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        stop_all()
        print("\ninterrupted", flush=True)
        sys.exit(130)
