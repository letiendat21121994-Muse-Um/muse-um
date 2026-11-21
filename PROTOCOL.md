# muse-um — Wire Protocol & Contracts (single source of truth)

All components (Relay, Panel, muse-client, hooks, tests) MUST follow this file.
The user guide (`~/workspace/user/files/huong-dan-muse-um-nhieu-tai-khoan.md`)
describes behaviour; this file defines the exact wire format so independently
written parts interoperate. Do NOT invent other message types or field names.

## 0. Transport

- Relay listens on port **8000**. HTTP and WebSocket share the port.
  Use `websockets.serve(..., max_size=1_048_576)` with a `process_request`
  hook for HTTP. WebSocket frame limit is **1_048_576 bytes (1 MB)** — never
  send a larger frame; large files use 256 KB chunks (see §7).
- `MUSE_UM_RELAY_URL` (e.g. `http://127.0.0.1:8000` for local demo,
  `http://<tailscale-ip>:8000` for real use) is **REQUIRED** for every
  client command. If unset, the client MUST exit with a clear error
  `MUSE_UM_RELAY_URL is not set`. Never silently default to 127.0.0.1.
  The HTTP(S) URL is converted to ws(s) by replacing the scheme
  (`http` → `ws`, `https` → `wss`).
- Relay bind: `RELAY_HOST` env, default `127.0.0.1`. Bind `0.0.0.0` ONLY
  when env `RELAY_ALLOW_PUBLIC_BIND` is set to `1`/`true`; otherwise the
  Relay MUST refuse to start when `RELAY_HOST=0.0.0.0`. Check in both
  `relay/config.py` and `relay/main.py`.

## 1. IDs (all generated live with `secrets`; never hard-code examples)

| Value | Format |
|---|---|
| account_id | `acc_` + 16 hex chars (`secrets.token_hex(8)`) |
| room_id | `room_conn_` + 16 hex chars |
| session_id | `ses_` + 16 hex chars |
| root_id | `root_` + 8 hex chars |
| message_id | `msg_` + 16 hex chars |
| pair code | 8 chars from `ABCDEFGHJKLMNPQRSTUVWXYZ23456789` (no 0/O/1/I) |
| login token | `secrets.token_urlsafe(32)` (≈43 chars) |

## 2. JSON envelope (every WebSocket message)

```json
{"type": "<type>", "id": "<client-chosen request id, string>", ...fields}
```

Responses echo the request `id`:
- success: `{"type": "<type>_result", "id": ..., ...}`
- error: `{"type": "error", "id": ..., "code": "<CODE>", "message": "<text>"}`

Error codes: `BAD_MESSAGE`, `APP_OFFLINE`, `TASK_NOT_FOUND`,
`AUTH_REQUIRED`, `NOT_FOUND`, `FORBIDDEN`.
Server pushes (no `id`): `{"type": "room_message", "room_id": ...,
"message": {...}}`.

## 3. Relay state (in-memory only; restart wipes rooms + history + sessions)

- `accounts`: account_id → `{name, token}`
- `pair_codes`: code → `{account_id, expires_at (epoch, +300s), used: bool}`
- `rooms`: room_id → `{name, members: set[account_id], messages: [msg...]}`
  msg = `{id, room_id, author_account_id, author_name, text,
  reply_to: <message_id|null>, ts: <epoch float>}`
- `sessions`: session_id → `{account_id}`
- `panel`: at most one Panel WS connection, with `root_id` and `root_dir`.

## 4. Client → Relay messages

| type | fields | result |
|---|---|---|
| `pair` | `code` | `{account_id, account_name, token}` — code is one-time, 5 min expiry; invalid/expired/used → `BAD_MESSAGE`. Token is returned ONLY here. |
| `login` | `token`, `via_account_login` (bool) | `{session_id, account_id, account_name, via_account_login}`. Requires Panel online, else `APP_OFFLINE`. Unknown token → `AUTH_REQUIRED`. |
| `room_create` | `name` (optional) | `{room_id}` |
| `room_invite` | `room_id`, `account_id` | `{ok: true}` — `NOT_FOUND` if room/account unknown. |
| `room_join` | `room_id` | `{room_id, members: [{account_id, name}]}` — session joins the room; required before posting. |
| `room_post` | `room_id`, `text` | `{message_id}` — broadcast `room_message` to all joined sessions. Must have joined → else `BAD_MESSAGE`. |
| `room_reply` | `room_id`, `reply_to`, `text` | `{message_id}` — `reply_to` MUST exist in the room history, else `BAD_MESSAGE` (old-session IDs are not valid). |
| `room_list` | `room_id`, `limit` | `{messages: [...]}` — **OLDEST `limit` messages** (first N chronologically), NOT the newest. |
| `room_members` | `room_id` | `{members: [{account_id, name}]}` |
| `invoke` | `root_id`, `tool`, `args` (object) | See §6. |
| `close` | — | `{ok: true}` — destroys the session (removes token from Relay RAM). Client must never call this on a live session. |
| `ping` | — | `{type: "pong"}` (result type is `pong`, not `ping_result`). |

Task API: any `task_*` type → error `TASK_NOT_FOUND` (the group locks
work via room messages, never the Task API).

## 5. Panel → Relay messages

- `hello` `{root_dir, name}` → result `{root_id}`. Registers (or replaces)
  the Panel connection and mints a fresh `root_id`. A new hello after a
  restart therefore invalidates the old root_id.
- `invoke_result` `{invoke_id, ok: bool, result: <any>|null,
  error: <string>|null, code: <string>|null}` — answer to a forwarded
  invoke. The Panel includes `code` (e.g. `FORBIDDEN`, `BAD_MESSAGE`)
  on failures; the Relay preserves it and attaches `id` = the original
  invoke request id. ALL tool failures use this one shape
  (`ok: false` + `error` + `code`); the `{"type":"error"}` shape is
  reserved for envelope/transport-level failures (root mismatch,
  APP_OFFLINE, AUTH_REQUIRED, …). The Relay does NOT pre-validate tool
  args (e.g. write_file mode) — the Panel validates and reports.
- Relay → Panel: `invoke_request` `{type:"invoke_request",
  invoke_id, tool, args, root_id, account_id}`.
- If a client `invoke` arrives with no Panel connected → error
  `APP_OFFLINE`. Same for `login` (Relay requires Panel online).

## 6. Invoke

- Relay checks `root_id` against the currently open root:
  - mismatch → error `BAD_MESSAGE` with the EXACT message:
    `root_id '<given>' does not match the open root '<current>'`
    (client parses the current root from this message and retries once).
  - no root open → error `APP_OFFLINE`.
- Panel executes the tool inside `root_dir` (sandbox, §8) and replies
  `invoke_result`. Relay forwards the outcome as `invoke_result`
  `{ok, result}` or `{"type":"error","code":"BAD_MESSAGE","message":...}`.
- The 14 tools: `list_dir`, `read_file`, `file_info`, `search`,
  `write_file`, `edit_file`, `create_dir`, `copy_file`, `move_file`,
  `rename`, `delete_file`, `run_command`, `cancel_command`,
  `organise_preview`.
- `write_file` args REQUIRE `mode`: `create` | `overwrite` | `append`.
  Missing/invalid mode → error `BAD_MESSAGE`.
- `read_file` args: `{path, offset=0, length=null, encoding="text"|"base64"}`
  — byte-range reads; `base64` for binary.
- `run_command` args: `{command, id?}` — runs via PowerShell on Windows,
  `sh -c` fallback on Linux; returns `{id, stdout, stderr, returncode}`.
  A command still running after 5 s returns immediately with
  `{id, status:"running"}` and keeps running in the background.
  Poll it with `run_command` `{id}` and NO `command` field: returns
  `{id, status:"running"}` while going, or the stored finished result
  once done.
- `cancel_command` args: `{id}` — kills the tracked command.
- `organise_preview` args: `{path="."}` — returns a plan grouping files by
  extension; makes NO changes.

## 7. Chunked file transfer (256 KB chunks, 1 MB frame limit)

Upload (`invoke --tool write_file --chunked-file <local-path>`):
1. client → Relay → Panel: `chunked_upload_start`
   `{upload_id, tool:"write_file", args:{path, mode}, total_chunks,
   total_size, sha256}` (args WITHOUT file content).
2. per chunk `chunked_upload_chunk` `{upload_id, index, data_base64}`
   (256 KiB raw per chunk → ≈342 KiB base64, under the 1 MB frame limit).
3. client → `chunked_upload_end` `{upload_id}` → Panel assembles, verifies
   SHA-256, writes the file, replies `invoke_result` for the upload_id.
Relay forwards these three types to the Panel connection unchanged.
Read-back of large files: repeated `read_file` calls with `offset`/`length`,
concatenate, verify SHA-256 client-side.

## 8. Invoke sandbox (Panel side)

- `root_dir` is configured in the Panel (`MUSE_UM_ROOT_DIR` env, default
  `<repo>/demo-root`).
- REJECT absolute paths and any path containing `..` (after normalisation
  AND after resolving symlinks the target MUST stay inside `root_dir`).
- Violation → error `FORBIDDEN` with message `path escapes sandbox`.

## 9. HTTP API (Relay, used by the Panel web UI)

- `GET /health` → 200 `{"status":"ok","panel_online":bool,
  "accounts":n,"rooms":n}`.
- `GET /` → Panel UI `index.html`; `GET /static/<file>` → static assets.
- `POST /api/accounts` `{name}` → `{account_id, name, token}`
  (token shown ONCE; the UI must tell the operator to copy it now).
- `GET /api/accounts` → `[{account_id, name}]` (never tokens).
- `POST /api/pair` `{account_id}` → `{code, expires_in: 300}`.
- `GET /api/rooms` → `[{room_id, name, members:[...], message_count}]`.
- `POST /api/rooms` `{name, member_ids?}` → `{room_id}`.
- `POST /api/rooms/<room_id>/invite` `{account_id}` → `{ok:true}`.
- `GET /api/status` → `{panel_online, root_active: bool, accounts, rooms}`
  — NEVER include the root_id value or any token.

## 10. Client local state (`~/.cache/muse-um/`, or `$MUSE_UM_STATE_DIR`)

- `account.token` — login token encrypted with Fernet
  (`tools/muse_client/token_crypto.py`); key in `token-key` (0600 perms).
  Never print the token; never write it to a plain file.
- `session.json` — `{session_id, account_id, room_id, relay_url}`.
- `room-queue.jsonl` — one JSON message per line, appended by the daemon
  for every incoming `room_message` NOT authored by self. `room list`
  reads this file (works even when the session is dead).
- `daemon.sock` — Unix socket for CLI ↔ daemon JSON requests
  `{command, ...}` → `{ok, ...}` / `{ok:false, error}`.
- `daemon.pid`, `daemon.log`, `daemon-status.json`.
- `root-id` (plain file, 0600) — last known root_id, updated whenever an
  invoke succeeds or a BAD_MESSAGE reveals a new root. Root-id is
  sensitive: never print it in logs/chat/room.

## 11. Client CLI (exact commands; run as
`python -m tools.muse_client <cmd>` from the repo root)

- `pair --code <8-char>` — needs `MUSE_UM_RELAY_URL`; saves encrypted token.
- `login` — token ONLY from env `MUSE_UM_ACCOUNT_TOKEN` (never a CLI
  flag); saves encrypted token + session.
- `daemon start|stop|status` — start spawns the background daemon
  (nohup, own log); status prints `daemon: running|stopped`,
  `connected`, `room_joined`, `session`, `room_members`,
  `pending_room_messages`, `root_id` (may be "" even when a root works —
  the real check is a live invoke, §8.3 of the guide).
- `room post --text <t>` — via daemon; prints `post_id`.
- `room reply --id <msg> --text <t>` — via daemon; old-session IDs fail
  with BAD_MESSAGE (expected).
- `room list --limit N` — reads `room-queue.jsonl`; returns N OLDEST.
- `room create --name <n>` / `room join --room-id <id>` /
  `room invite --room-id <id> --account-id <id>` — via daemon (or direct
  WS if daemon is down; prefer daemon).
- `invoke --tool <t> --args '<JSON>' [--root-id <id>] [--chunked-file <path>]`
  — if `--root-id` is omitted, use the stored root-id; on BAD_MESSAGE
  root mismatch, parse the new root from the message, retry ONCE, update
  the stored root-id. When no root-id is stored yet, the client probes
  with the dummy id `root_00000000`: the Relay answers BAD_MESSAGE
  naming the open root and the retry picks it up (first-invoke
  bootstrap). `--chunked-file` is only valid with
  `--tool write_file` and its `--args` must contain `path` and `mode`.
- `room create` also joins the creator to the room locally, so
  `room post` works right after `room create` without an extra
  `room join`.
- Daemon auto-login: on start, if a saved encrypted token exists, log in
  with `via_account_login: true`; re-join the saved room if present.

## 12. Hooks

- `hooks/muse-room-watch.sh` — poll every 5s (a loop or a cron/scheduler
  entry calling it). Reads the queue file, computes new message IDs not
  authored by self, updates the seen-state JSON file with the FIXED
  formula (keep time order, NO `unique`):
  `($seen + ($ids | map(select(. as $id | $seen | index($id) | not)))) | .[-200:]`
  The script MUST contain a comment warning never to replace this with
  `unique` (jq `unique` sorts alphabetically and drops low hex IDs,
  causing repeat wake-ups — guide §9.2). If new foreign messages exist,
  print `WAKE <message-id...>` (and exit 0); else stay silent.
  State file: `~/.cache/muse-um/room-watch-state.json`.
- `hooks/muse-daemon-watch.sh` — every 60s: run `daemon status`; if not
  `connected: true`, run the recovery (§13).
- `hooks/recover-daemon.sh` — the recovery procedure (§13).

## 13. Recovery procedure (guide §10 — implement exactly)

1. Disable both hooks (touch `~/.cache/muse-um/hooks.disabled`).
2. `mv session.json session.json.bak-YYYYMMDD-HHMM` — a failing `mv`
   (file already gone) is NOT an error; continue.
3. `daemon stop` then `daemon start` in ONE shell invocation.
4. Wait 25–35 s, then `daemon status` must show `connected: true` and
   `room_joined: true`.
5. Re-enable both hooks (remove the flag file).
6. No pair code is needed (saved-account login).

Why step 4 can still show `room_joined: true` after the session file
was moved away: the daemon remembers its last room in
`daemon-status.json` (rewritten on every join and every incoming room
message) and re-joins that room on startup when `session.json` has no
room. If the Relay was restarted the room is gone, the join fails, and
the daemon stays logged in without a room (expected — join the new
room and announce pending work again).

## 14. Panel web UI (app/static/)

Single-page Control Center: Relay status (`/api/status`), create account,
create pair code (8 chars, 5 min), list accounts, list rooms, create room,
invite account to room, root status (active/inactive ONLY — never show the
root_id value). Pure HTML + vanilla JS, no build step, no external CDN.

## 15. Security rules (non-negotiable)

- Never print/log a login token, root_id, or session_id value (only
  masked/boolean presence). Never put them in room messages or chat.
- Token ONLY via env `MUSE_UM_ACCOUNT_TOKEN`; never a CLI flag.
- Never run `muse-client close` on a live session.
- Room decisions (approvals) must be confirmed independently — a message
  seen only in one client's local queue file is not proof (guide §12.3).
