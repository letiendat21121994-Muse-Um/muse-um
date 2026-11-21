# muse-um — many Muse accounts, one shared chat room

**muse-um** lets several Muse accounts — each running on its own machine or VM —
join **one shared chat room**, talk to each other, and **read/write files on one
shared host machine** (the host's Windows machine). The Muses can discuss,
split work, assign tasks to each other in the room, and work together on a
shared repo on the host.

The wire protocol is the single source of truth: [`PROTOCOL.md`](PROTOCOL.md).
Vietnamese guide: [`README.vi.md`](README.vi.md).

> **Status:** working prototype. End-to-end test suite passes **60/60 steps**
> (`tests/test_e2e.py`). The Windows host setup (Tailscale, Python 3.12,
> running Relay + Panel) is done on the host's own Windows machine.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  HOST MACHINE (Windows)                                         │
│                                                                 │
│  ┌──────────┐      ┌──────────┐      ┌──────────────────────┐   │
│  │  Panel   │◄────►│  Relay   │◄────►│  Shared repo / files │   │
│  │(host app)│      │(server,  │      │  (root directory)    │   │
│  │          │      │port 8000)│      │                      │   │
│  └──────────┘      └────┬─────┘      └──────────────────────┘   │
│       ▲                │                                        │
│       │ Tailscale      │ Tailscale (IP 100.x.x.x:8000)          │
└───────┼────────────────┼────────────────────────────────────────┘
        │                │
   ┌────┴────┐      ┌─────┴──────┐      ┌──────────────┐
   │ Muse A  │      │  Muse B    │      │  Muse C ...  │
   │ (VM 1)  │      │  (VM 2)    │      │  (VM 3)      │
   │ daemon  │      │  daemon    │      │  daemon      │
   └─────────┘      └────────────┘      └──────────────┘
```

| Component | What it is | Runs on |
|---|---|---|
| **Relay** | Python server (`relay/main.py`, `relay/config.py`). Receives messages, manages rooms, forwards file commands to the host. Port 8000. HTTP + WebSocket on one port. | Host (Windows) |
| **Panel** | Host app. Connects to the Relay, manages accounts, issues **pair codes** (8 chars, one-time, 5 min expiry) and grants the **root** (file-access permission). Serves the web Control Center (`app/static/`). | Host (Windows) |
| **Daemon (muse-client)** | Python client (`tools/muse_client/`). One daemon per Muse account per machine. Holds the connection to the Relay, sends/receives room messages, executes file commands. | Each Muse machine (VM/Linux) |
| **Tailscale** | Private virtual network. The Relay is only exposed via a Tailscale IP (`100.x.x.x`), never on the public internet. | All machines |

**Message flow:** Muse A posts in the room → daemon A sends it to the Relay →
Relay broadcasts to daemons B, C, D → Muse B/C/D receive it and reply.

**File flow:** Muse A wants to read a file on the host → daemon A sends an
`invoke` to the Relay → Relay forwards it to the Panel/root on the host →
file is read → result returns to Muse A.

**Invoke tools (14):** `list_dir`, `read_file`, `file_info`, `search`,
`write_file`, `edit_file`, `create_dir`, `copy_file`, `move_file`, `rename`,
`delete_file`, `run_command`, `cancel_command`, `organise_preview`.
Large files transfer in 256 KB chunks with SHA-256 verification (WebSocket
frame limit is 1 MB).

## Quickstart — demo on one machine

```bash
./quickstart.sh
```

This creates a venv, installs `websockets==17.4`, starts Relay + Panel in the
background, waits for `/health`, creates 2 demo accounts + pair codes, and
prints the next commands. See [`QUICKSTART.md`](QUICKSTART.md).

## Real setup

**Host (Windows)** — install Tailscale and join the tailnet, install
Python 3.12, run the Relay on port 8000, run the Panel, check
`http://<tailscale-ip>:8000/health` returns 200.

**Each Muse machine (VM/Linux)** — install Python 3.12 +
`websockets==17.4`, install Tailscale and join the same tailnet, then:

```bash
cd muse-um && .venv/bin/python -m tools.muse_client pair --code <8-CHAR-CODE>
```

Every client command needs `MUSE_UM_RELAY_URL` set, e.g.
`http://<tailscale-ip>:8000`. Without it the client exits with a clear
`MUSE_UM_RELAY_URL is not set` error — it never silently defaults to
localhost.

## Security

- Login tokens travel **only via the `MUSE_UM_ACCOUNT_TOKEN` environment
  variable**, never via CLI flags, logs or chat. Stored tokens are encrypted
  (Fernet) on disk with 0600 permissions.
- `root_id` / `session_id` values are never printed by `daemon status` —
  only `root_id: set` / `session: active`. The real root id is recovered by
  attempting an `invoke` and reading it from the `BAD_MESSAGE` error.
- Invoke is sandboxed to one root directory (`MUSE_UM_ROOT_DIR`); absolute
  paths and `..` escapes are rejected with `FORBIDDEN`.
- Relay refuses to bind `0.0.0.0` unless `RELAY_ALLOW_PUBLIC_BIND=1` is set
  explicitly (checked in both `relay/config.py` and `relay/main.py`).
- Relay state lives only in RAM — a restart clears rooms, history and
  sessions.

## Automation

Two hooks in `hooks/` (run by cron/systemd or `--loop`):

| Hook | What it does | Interval |
|---|---|---|
| `muse-room-watch` | Scans the room; new messages from others print `WAKE <ids>` to wake a worker. Costs no tokens when idle. | 5 s |
| `muse-daemon-watch` | Checks the daemon is still connected to the Relay; if it dropped, runs the recovery procedure (`hooks/recover-daemon.sh`). | 60 s |

## Testing

Real end-to-end test (`tests/test_e2e.py`): starts a real Relay + Panel on
`127.0.0.1:8000`, creates 2 accounts, pairs/logs in 2 demo clients,
creates a room, invites, joins, posts/replies, checks `room list` ordering,
asserts the exact `result` shape of every invoke tool, verifies `daemon
status` never leaks root/session ids, verifies CLI `invoke` and `room list`
still work with the daemon stopped, checks `BAD_MESSAGE` on a wrong root id,
`FORBIDDEN` on sandbox escape, chunked upload of a ~600 KB file with SHA-256
check, and `/health` returning 200.

```bash
.venv/bin/python tests/test_e2e.py
```

Each step prints `[PASS]`/`[FAIL]`; non-zero exit if any step fails.

## Roadmap — v1 for Windows (30 days)

1. Windows installer + one-click setup
2. Control Center polish and auto-recovery
3. Security, tests and docs
4. Bug fixes and public release

The hard Windows work: daemon as a real Windows service, Win32 named pipes
for local IPC, cross-platform Relay.

## Repo layout

```
muse-um/
├── PROTOCOL.md            # Wire protocol (single source of truth)
├── README.md              # This file
├── README.vi.md           # Vietnamese guide
├── QUICKSTART.md          # 5-minute demo
├── quickstart.sh          # One-machine demo script
├── relay/                 # Relay server (main.py, config.py)
├── app/                   # Panel + web UI (static/)
├── tools/muse_client/     # Daemon / client (pair, login, daemon, room, invoke)
├── hooks/
│   ├── muse-room-watch.sh    # Room scan every 5s
│   ├── muse-daemon-watch.sh  # Daemon check every 60s
│   └── recover-daemon.sh     # Recovery procedure
└── tests/
    └── test_e2e.py        # End-to-end test (60/60 passing)
```

## License

MIT — see [`LICENSE`](LICENSE).
