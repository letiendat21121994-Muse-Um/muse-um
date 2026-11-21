# Devin Task 01 — Daemon lifecycle cleanup + Windows IPC groundwork

> **For the Devin Bot.** Work on branch `devin/daemon-lifecycle-cleanup` and
> open a pull request — do not push directly to `main`. Work in small,
> verifiable steps and report progress after each one. Budget: $69 in Bot
> credits, expiring 7 days after redemption — keep the scope tight and ship
> something working, not everything at once.

## Context

muse-um lets several Muse accounts (each on its own machine/VM) join one
shared chat room and read/write files on one shared host machine. The
components are: **Relay** (Python server, port 8000, HTTP + WebSocket),
**Panel** (host app: accounts, pair codes, file-access root, web Control
Center), **daemon / muse-client** (`tools/muse_client/`: one daemon per Muse
account per machine, holds the WebSocket to the Relay, sends/receives room
messages, executes file commands on the host), **Tailscale** (Relay only
reachable on the private tailnet). The wire protocol is the contract:
`PROTOCOL.md` — do not change the wire format unless you justify it in the
PR; the user guide is `README.md` (English) / `README.vi.md` (Vietnamese).

## The problem (read the code first)

The daemon lifecycle is currently manual and fragile:

1. **Stale sessions need a 6-step manual shell recovery**
   (`hooks/recover-daemon.sh`): disable both hooks, move `session.json`
   aside to a timestamped backup, `daemon stop` then `daemon start` in one
   shell, wait a **fixed 30 s blind sleep**, then `daemon status` and
   **grep the text output** for `connected: true` / `room_joined: true`.
   A plain stop/start does *not* fix a broken session — the session file
   must be moved aside first.
2. **Login is only retried at startup.** If login fails, the supervisor can
   stop after a few attempts instead of retrying with backoff.
3. **Room rejoin depends on `daemon-status.json` memory.** That file can be
   clobbered by a status write (see the seeding comment in
   `tools/muse_client/daemon.py::run()`), so the daemon can lose the room
   it must rejoin after a recovery.
4. **No Windows support.** The daemon talks to the CLI over a Unix socket
   (`daemon.sock`) and runs as a spawned background process (setsid/nohup
   style). There is no Windows service and no Win32 named pipes — the
   daemon cannot run natively on the Windows host yet.

## Scope — do this, in this order

### 1. One `daemon restart` command (highest priority)

Add `daemon restart` to the CLI (`tools/muse_client/`) that does the whole
recovery in one command, with no manual steps:

- Stop the daemon (socket `stop`, fall back to SIGTERM as today).
- Detect a stale/broken session and move `session.json` aside to a
  timestamped backup automatically (same naming as today:
  `session.json.bak-YYYYMMDD-HHMM`). A healthy session must be left alone.
- Start the daemon.
- Wait on **real readiness, not a fixed sleep**: poll the daemon socket
  until status shows `connected: true` and `room_joined: true`, with a
  timeout (60 s) and a clear error if it never becomes ready.
- Return a machine-readable result (`{ok, connected, room_joined}`), never
  text that a caller has to grep.
- Rewrite `hooks/recover-daemon.sh` to call `daemon restart` (keep the hook
  disable/enable behaviour around it) and delete the duplicated logic.

### 2. Resilient login retry + room rejoin

- Retry login with backoff when it fails at startup (bounded retries, clear
  log lines), instead of giving up after a few attempts.
- Make room rejoin robust: persist the last joined `room_id` in the session
  state itself so a status-file rewrite can never lose it. Keep the
  fallback to the previous room when the Relay restarted and the old room
  is gone (stay logged in without a room, as today).

### 3. Windows IPC groundwork

- Introduce an IPC abstraction so the daemon/CLI transport is not hardcoded
  to a Unix socket: Unix socket on Linux, **Win32 named pipes on Windows**.
- Implement the named-pipe transport and a minimal Windows service entry
  point (start/stop the daemon as a service). A full installer is out of
  scope for this task — the transport + service skeleton is the deliverable.
- Keep the Linux path working exactly as today.

### 4. Tests

- Extend `tests/test_e2e.py`: restart from a stale session (must recover
  with no manual steps), readiness wait (must not sleep a fixed 30 s),
  rejoin after restart, and the new IPC path where it can run. The suite
  is currently 60/60 passing — keep every existing case green and add the
  new ones.

## Acceptance criteria

- [ ] `daemon restart` recovers a daemon with a stale session, end to end,
      with no manual steps; afterwards `daemon status` shows
      `connected: true` and `room_joined: true`.
- [ ] No fixed 30 s sleep and no grep-parsing of status text anywhere in
      the restart/recovery path.
- [ ] Login failures are retried with backoff and logged; the daemon does
      not silently give up.
- [ ] Room rejoin survives a status-file rewrite.
- [ ] Windows named-pipe transport exists with a service entry point; the
      Linux Unix-socket path is unchanged and green.
- [ ] `tests/test_e2e.py` passes fully (existing 60 cases + new cases).
- [ ] `PROTOCOL.md` updated only if the wire format changed, with the
      change called out in the PR description.
- [ ] No secrets, tokens, or absolute local paths committed. `.gitignore`
      already excludes runtime state — keep it that way.

## Out of scope for this task

- The one-click Windows installer (Week 1 follow-up).
- Panel UI polish and auto-recovery UI.
- Changing the Relay/Panel protocol or the 14 invoke tools.
- Anything that needs the host's Windows machine interactively — work
  against the repo and the Linux demo (`./quickstart.sh`) and note what
  needs a Windows run to verify.
