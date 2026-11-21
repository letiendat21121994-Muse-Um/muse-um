#!/usr/bin/env bash
# recover-daemon.sh — Tắt hook, khôi phục daemon và luôn bật lại hook.
# Không cần mã pair: daemon tự đăng nhập bằng tài khoản đã lưu.

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${MUSE_UM_STATE_DIR:-$HOME/.cache/muse-um}"
DISABLED_FILE="$STATE_DIR/hooks.disabled"

if [ -x "$REPO_DIR/.venv/bin/python" ]; then
    CLIENT_PY="$REPO_DIR/.venv/bin/python"
else
    CLIENT_PY="python3"
fi

# Tắt cả 2 hook; luôn bật lại kể cả khi khôi phục thất bại.
mkdir -p "$STATE_DIR"
touch "$DISABLED_FILE"
trap 'rm -f "$DISABLED_FILE"' EXIT
trap 'exit 1' HUP INT TERM

(cd "$REPO_DIR" && "$CLIENT_PY" -m tools.muse_client daemon restart)
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "Hint: APP_OFFLINE means the Panel on the host is offline; restart the Panel, then retry. No pair code needed." >&2
fi
exit "$RC"
