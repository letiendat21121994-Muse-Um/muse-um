#!/usr/bin/env bash
# muse-daemon-watch.sh — Kiểm tra daemon muse-um mỗi 60 giây.
#
# Cách dùng (một lần kiểm tra; vòng lặp do cron/systemd gọi lại, hoặc --loop):
#   hooks/muse-daemon-watch.sh [--loop]
#
#   - Chạy `daemon status`.
#   - Nếu KHÔNG thấy `connected: true` -> gọi hooks/recover-daemon.sh
#     (quy trình khôi phục, PROTOCOL.md §13 / hướng dẫn gốc mục 10).
#   - Nếu file hooks.disabled tồn tại (đang khôi phục) -> im lặng, exit 0.
#
# Lệnh client: .venv/bin/python -m tools.muse_client (chạy từ repo root).

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${MUSE_UM_STATE_DIR:-$HOME/.cache/muse-um}"
DISABLED_FILE="$STATE_DIR/hooks.disabled"
RECOVER_SCRIPT="$REPO_DIR/hooks/recover-daemon.sh"

LOOP=0
for arg in "$@"; do
    case "$arg" in
        --loop) LOOP=1 ;;
        *) echo "muse-daemon-watch: unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [ -x "$REPO_DIR/.venv/bin/python" ]; then
    CLIENT_PY="$REPO_DIR/.venv/bin/python"
else
    CLIENT_PY="python3"
fi

check_once() {
    if [ -f "$DISABLED_FILE" ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') daemon-watch: hooks disabled, skipping"
        return 0
    fi

    STATUS_OUT="$(cd "$REPO_DIR" && "$CLIENT_PY" -m tools.muse_client daemon status 2>&1)"
    STATUS_RC=$?

    if [ $STATUS_RC -eq 0 ] && printf '%s\n' "$STATUS_OUT" | grep -q 'connected: *true'; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') daemon-watch: connected: true"
        return 0
    fi

    echo "$(date '+%Y-%m-%d %H:%M:%S') daemon-watch: daemon NOT connected (rc=$STATUS_RC), running recovery"
    printf '%s\n' "$STATUS_OUT" | sed 's/^/daemon-watch: status: /'
    if [ ! -x "$RECOVER_SCRIPT" ]; then
        echo "daemon-watch: recover script not found/executable: $RECOVER_SCRIPT" >&2
        return 1
    fi
    "$RECOVER_SCRIPT"
    return $?
}

if [ "$LOOP" -eq 1 ]; then
    while true; do
        check_once || true
        sleep 60
    done
else
    check_once
fi
