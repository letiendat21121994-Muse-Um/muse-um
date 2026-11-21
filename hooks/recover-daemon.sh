#!/usr/bin/env bash
# recover-daemon.sh — Quy trình khôi phục daemon muse-um (PROTOCOL.md §13,
# hướng dẫn gốc mục 10). Áp dụng khi Relay + Panel trên máy host đều đang chạy
# mà daemon mất kết nối (session cũ hỏng).
#
#   1. Tắt cả 2 hook (touch ~/.cache/muse-um/hooks.disabled).
#   2. mv session.json sang session.json.bak-YYYYMMDD-HHMM
#      — mv lỗi (file đã không còn) KHÔNG phải vật cản, cứ tiếp tục.
#   3. daemon stop rồi daemon start TRONG MỘT shell invocation.
#   4. Chờ 25–35 giây, rồi daemon status phải có connected: true
#      và room_joined: true.
#   5. Bật lại cả 2 hook (xóa flag file).
#   6. Không cần mã pair (daemon tự đăng nhập bằng tài khoản đã lưu).
#
# Thoát 0 khi khôi phục thành công, 1 khi bước kiểm tra (4) thất bại.
# LƯU Ý: bước (5) luôn chạy kể cả khi (4) thất bại, đúng §13 — daemon-watch
# sẽ gọi lại quy trình này sau 60 giây nếu daemon vẫn rớt. Nếu khôi phục
# thất bại lặp lại, xem bảng xử lý sự cố trong README.md (khả năng cao là
# Panel trên máy host đang offline — lỗi APP_OFFLINE — chứ không phải lỗi
# session), và báo host.
#
# Lệnh client: .venv/bin/python -m tools.muse_client (chạy từ repo root).

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${MUSE_UM_STATE_DIR:-$HOME/.cache/muse-um}"
DISABLED_FILE="$STATE_DIR/hooks.disabled"
SESSION_FILE="$STATE_DIR/session.json"

if [ -x "$REPO_DIR/.venv/bin/python" ]; then
    CLIENT_PY="$REPO_DIR/.venv/bin/python"
else
    CLIENT_PY="python3"
fi

client() {
    (cd "$REPO_DIR" && "$CLIENT_PY" -m tools.muse_client "$@")
}

echo "=== recover-daemon: start $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "state dir: $STATE_DIR"

# --- Bước 1: tắt cả 2 hook -----------------------------------------------
echo "[1/6] Disabling hooks (muse-room-watch + muse-daemon-watch)..."
mkdir -p "$STATE_DIR"
touch "$DISABLED_FILE"
echo "[1/6] OK: hooks disabled via $DISABLED_FILE"

# --- Bước 2: dời session.json cũ sang file backup -------------------------
echo "[2/6] Moving old session.json to backup..."
STAMP="$(date +%Y%m%d-%H%M)"
if [ -f "$SESSION_FILE" ]; then
    if mv "$SESSION_FILE" "$SESSION_FILE.bak-$STAMP"; then
        echo "[2/6] OK: session.json -> session.json.bak-$STAMP"
    else
        echo "[2/6] NOTE: mv failed (file already gone?) — NOT an error, continuing"
    fi
else
    echo "[2/6] NOTE: session.json already absent — NOT an error, continuing"
fi

# --- Bước 3: daemon stop rồi daemon start TRONG MỘT shell ----------------
echo "[3/6] daemon stop, then daemon start (single shell invocation)..."
client daemon stop
STOP_RC=$?
echo "[3/6] daemon stop exited with rc=$STOP_RC (non-zero here is fine if the daemon was already stopped)"
client daemon start
START_RC=$?
echo "[3/6] daemon start exited with rc=$START_RC"
if [ "$START_RC" -ne 0 ]; then
    echo "[3/6] FAIL: daemon start failed (rc=$START_RC)"
fi

# --- Bước 4: chờ 25–35 giây rồi kiểm tra status ---------------------------
WAIT_SECS=30
echo "[4/6] Waiting ${WAIT_SECS}s (25-35s window) for the daemon to log in and re-join the room..."
sleep "$WAIT_SECS"
STATUS_OUT="$(client daemon status 2>&1)"
STATUS_RC=$?
echo "[4/6] daemon status (rc=$STATUS_RC):"
printf '%s\n' "$STATUS_OUT" | sed 's/^/  /'
CONNECTED=0
JOINED=0
printf '%s\n' "$STATUS_OUT" | grep -q 'connected: *true' && CONNECTED=1
printf '%s\n' "$STATUS_OUT" | grep -q 'room_joined: *true' && JOINED=1
if [ "$CONNECTED" -eq 1 ] && [ "$JOINED" -eq 1 ]; then
    echo "[4/6] OK: connected: true and room_joined: true"
    RECOVERED=1
else
    echo "[4/6] FAIL: expected connected: true and room_joined: true (got connected=$CONNECTED room_joined=$JOINED)"
    echo "[4/6] Hint: if the status mentions APP_OFFLINE / 'Could not reach the Relay',"
    echo "       the Panel on the host machine is probably offline — restart the Panel"
    echo "       on the host first, then run this recovery again. No pair code needed yet."
    RECOVERED=0
fi

# --- Bước 5: bật lại cả 2 hook --------------------------------------------
echo "[5/6] Re-enabling hooks..."
rm -f "$DISABLED_FILE"
echo "[5/6] OK: hooks re-enabled (flag file removed)"

# --- Bước 6: không cần mã pair --------------------------------------------
echo "[6/6] No pair code needed: the daemon logs in with the saved account."

if [ "$RECOVERED" -eq 1 ]; then
    echo "=== recover-daemon: SUCCESS $(date '+%Y-%m-%d %H:%M:%S') ==="
    exit 0
else
    echo "=== recover-daemon: FAILED $(date '+%Y-%m-%d %H:%M:%S') — hooks are re-enabled, daemon-watch will retry in 60s; report to the host if this repeats ==="
    exit 1
fi
