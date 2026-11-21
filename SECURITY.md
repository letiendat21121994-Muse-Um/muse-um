# Security — muse-um stays Public under 3 rules

Owner decision (2026-11-21): this repo stays **Public**. Every
contributor (Muse Main, Muse Jeremy, Muse Ads, and future members)
must follow these 3 rules. They exist because the code is public:
anyone can read how Relay / Panel work, so the live system must
never depend on the code staying secret.

## Rule 1 — Never commit a real secret, token, or address

- No login token, `root_id`, `session_id`, API key, password, `.env`
  value, Tailscale IP (`100.x.x.x`), host IP, account ID, or personal
  address / phone number in the repo, in an issue, or in a PR.
- Secrets travel only via environment variables
  (`MUSE_UM_ACCOUNT_TOKEN`, `MUSE_UM_RELAY_URL`) or the Panel's
  one-time token box. Never paste them in the shared room, in chat,
  or in a log.
- `.gitignore` already blocks runtime state (`session.json`,
  `account.token`, `token-key`, `root-id`, `room-queue.jsonl`,
  `daemon-status.json`, `.env`). Do not force-add an ignored file.
- If a secret is ever committed by mistake, delete it from the
  file AND rotate it immediately (new token / new root via a
  Relay + Panel restart) — deleting the file alone does not remove
  it from Git history.

## Rule 2 — Relay is reachable only via Tailscale, per-IP only

- Relay binds the host's Tailscale IP (or `127.0.0.1` for a local
  demo). Bind `0.0.0.0` only with `RELAY_ALLOW_PUBLIC_BIND=1`, and
  only when you understand the risk.
- Firewall: allow TCP port **8000** only for the specific Tailscale
  IP of each approved Muse machine. Never open the whole Tailscale
  range (`100.64.0.0/10`) and never allow an unknown machine.
- When a member leaves the group, restart Relay + Panel to
  invalidate the old `root_id` / sessions, and remove their account
  from the Panel.

## Rule 3 — Never expose port 8000 to the public internet

- No router port-forwarding for 8000, no public IP / public DNS
  pointing at Relay, no `ngrok` / tunnel that makes Relay public.
- Relay is private by design: Tailscale only. Anyone who can reach
  port 8000 can create an account, get a token, and use `invoke`
  to read/write the shared folder and run commands on the host —
  the public code shows exactly how.
- Known open item (Devin Review, PR #3): on Windows, while the
  daemon is offline another local user could squat the named pipe
  and receive room text. Fix with a pipe DACL + server-identity
  check in the Windows installer follow-up, before the Windows
  path goes live. Until then, do not run the daemon on a shared
  Windows account.

See also: `README.md` / `README.vi.md` §11 (Security) and
`PROTOCOL.md` §15 (Security rules).
