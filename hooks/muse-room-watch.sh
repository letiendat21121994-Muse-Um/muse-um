#!/usr/bin/env bash
# muse-room-watch.sh — Quét phòng muse-um mỗi 5 giây, đánh thức worker khi có tin mới.
#
# Cách dùng (một lần quét; vòng lặp do cron/systemd gọi lại, hoặc dùng --loop):
#   hooks/muse-room-watch.sh [--dry-run] [--loop] [--seed]
#
#   --dry-run   Chỉ in would_wake: true/false (và các ID mới), KHÔNG sửa state file.
#   --loop      Quét liên tục, mỗi lần cách nhau 5 giây.
#   --seed      Nạp toàn bộ ID hiện có trong queue vào state file mà không đánh thức
#               (dùng sau khi sửa hook, trước khi bật lại — xem hướng dẫn mục 9.2).
#
# Hành vi:
#   - Đọc $MUSE_UM_STATE_DIR/room-queue.jsonl (mặc định ~/.cache/muse-um/).
#   - Lấy ID các tin KHÔNG phải của mình (so author_account_id với account_id
#     trong session.json).
#   - Cập nhật room-watch-state.json bằng công thức ĐÃ SỬA (giữ thứ tự thời gian).
#   - Có tin mới  -> in "WAKE <id...>" và exit 0.
#   - Không có   -> im lặng, exit 0.
#   - Nếu file hooks.disabled tồn tại (đang khôi phục daemon) -> im lặng, exit 0.
#
# Yêu cầu: jq.

set -u

STATE_DIR="${MUSE_UM_STATE_DIR:-$HOME/.cache/muse-um}"
QUEUE_FILE="$STATE_DIR/room-queue.jsonl"
STATE_FILE="$STATE_DIR/room-watch-state.json"
SESSION_FILE="$STATE_DIR/session.json"
DISABLED_FILE="$STATE_DIR/hooks.disabled"

DRY_RUN=0
LOOP=0
SEED=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --loop)    LOOP=1 ;;
        --seed)    SEED=1 ;;
        *) echo "muse-room-watch: unknown option: $arg" >&2; exit 2 ;;
    esac
done

if ! command -v jq >/dev/null 2>&1; then
    echo "muse-room-watch: jq is required but not found" >&2
    exit 2
fi

scan_once() {
    # Hook đang bị tắt trong lúc khôi phục daemon -> không làm gì.
    if [ -f "$DISABLED_FILE" ]; then
        return 0
    fi

    # Tài khoản của mình (để loại tin do mình gửi). Không có session -> coi
    # mọi tin trong queue đều là tin lạ (queue vốn chỉ chứa tin không phải
    # của mình, xem PROTOCOL.md §10).
    SELF=""
    if [ -f "$SESSION_FILE" ]; then
        SELF="$(jq -r '.account_id // empty' "$SESSION_FILE" 2>/dev/null || true)"
    fi

    # IDs trong queue, theo đúng thứ tự thời gian (thứ tự dòng trong file).
    # Mỗi dòng là một envelope room_message: {"type":"room_message",
    # "message":{"id":...,"author_account_id":...}}. Chấp nhận cả dạng
    # rút gọn (.id / .message_id) để không vỡ khi daemon đổi dạng ghi.
    IDS_JSON='[]'
    if [ -f "$QUEUE_FILE" ]; then
        IDS_JSON="$(jq -s -c --arg self "$SELF" \
            '[.[] | select((.message.author_account_id // .author_account_id // "") != $self)
                    | (.message.id // .id // .message_id // empty)
                    | select(. != "")]' \
            "$QUEUE_FILE" 2>/dev/null || echo '[]')"
    fi

    # State "đã thấy": mảng JSON, mặc định [] nếu chưa có / hỏng.
    SEEN_JSON='[]'
    if [ -f "$STATE_FILE" ]; then
        SEEN_JSON="$(jq -c 'if type == "array" then . else [] end' "$STATE_FILE" 2>/dev/null || echo '[]')"
    fi

    # ID mới = ID trong queue mà state chưa có, giữ nguyên thứ tự thời gian.
    NEW_JSON="$(jq -n -c --argjson seen "$SEEN_JSON" --argjson ids "$IDS_JSON" \
        '$ids | map(select(. as $id | $seen | index($id) | not))')"

    # Cập nhật state bằng CÔNG THỨC ĐÃ SỬA (giữ thứ tự thời gian, KHÔNG dùng unique):
    #   ($seen + ($ids | map(select(. as $id | $seen | index($id) | not)))) | .[-200:]
    #
    # ⚠️  CẢNH BÁO — KHÔNG ĐƯỢC SỬA LẠI THÀNH `unique`:
    #   `unique` của jq SẮP XẾP THEO THỨ TỰ ALPHABET trước khi cắt 200 phần tử
    #   cuối, nên các ID tin nhắn (hex ngẫu nhiên) xếp thấp sẽ bị rớt khỏi danh
    #   sách "đã thấy" sau mỗi lần quét, rồi lại bị đánh thức như tin mới —
    #   gây vòng lặp gửi tin trùng lặp liên tục (đã gặp thực tế, xem hướng
    #   dẫn gốc mục 9.2). Công thức dưới đây chỉ thêm ID chưa thấy, giữ
    #   nguyên thứ tự thời gian, và chỉ cắt 200 ID MỚI NHẤT theo thời gian.
    UPDATED_JSON="$(jq -n -c --argjson seen "$SEEN_JSON" --argjson ids "$IDS_JSON" \
        '($seen + ($ids | map(select(. as $id | $seen | index($id) | not)))) | .[-200:]')"

    NEW_COUNT="$(printf '%s' "$NEW_JSON" | jq -r 'length')"

    if [ "$DRY_RUN" -eq 1 ]; then
        if [ "$NEW_COUNT" -gt 0 ]; then
            echo "would_wake: true"
        else
            echo "would_wake: false"
        fi
        printf 'new_ids: %s\n' "$NEW_JSON"
        return 0
    fi

    if [ "$SEED" -eq 1 ]; then
        mkdir -p "$STATE_DIR"
        tmp="$(mktemp "$STATE_DIR/.room-watch-state.XXXXXX")"
        printf '%s\n' "$UPDATED_JSON" > "$tmp"
        mv "$tmp" "$STATE_FILE"
        total="$(printf '%s' "$UPDATED_JSON" | jq -r 'length')"
        echo "seeded: $total ids (no wake)"
        return 0
    fi

    # Ghi state mới (chỉ khi có thay đổi, để khỏi chạm mtime vô ích).
    if [ "$UPDATED_JSON" != "$SEEN_JSON" ]; then
        mkdir -p "$STATE_DIR"
        tmp="$(mktemp "$STATE_DIR/.room-watch-state.XXXXXX")"
        printf '%s\n' "$UPDATED_JSON" > "$tmp"
        mv "$tmp" "$STATE_FILE"
    fi

    if [ "$NEW_COUNT" -gt 0 ]; then
        NEW_IDS="$(printf '%s' "$NEW_JSON" | jq -r '.[]' | tr '\n' ' ' | sed 's/ $//')"
        echo "WAKE $NEW_IDS"
    fi
    return 0
}

if [ "$LOOP" -eq 1 ]; then
    while true; do
        scan_once
        sleep 5
    done
else
    scan_once
fi
