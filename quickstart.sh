#!/usr/bin/env bash
# quickstart.sh — Demo muse-um trên MỘT máy trong ~5 phút.
#
#   ./quickstart.sh
#
# Làm gì:
#   1. Tạo .venv (Python 3.12) nếu chưa có, cài websockets==17.4.
#   2. Khởi động Relay (127.0.0.1:8000) + Panel ở chế độ nền, chờ /health.
#   3. Tạo 2 tài khoản demo + mã pair (8 ký tự, hết hạn sau 5 phút).
#   4. In ra các lệnh tiếp theo: pair, daemon start, tạo phòng, gửi tin.
#
# Idempotent phần nào: chạy lại sẽ dùng lại venv có sẵn, dùng lại Relay
# đang chạy khỏe (kiểm tra /health trước khi khởi động mới), và chỉ tạo
# thêm tài khoản demo mới (mã pair cũ hết hạn sau 5 phút nên mỗi lần chạy
# đều cần mã mới — đó là hành vi đúng của hệ thống, không phải lỗi).
#
# Dừng demo:  kill $(cat .quickstart/relay.pid .quickstart/panel.pid)
#              (và `daemon stop` cho từng client đã start).
# Token đăng nhập KHÔNG được in ra màn hình ở bất kỳ bước nào.

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"
RUN_DIR="$REPO_DIR/.quickstart"
mkdir -p "$RUN_DIR"

RELAY_URL="${MUSE_UM_RELAY_URL:-http://127.0.0.1:8000}"
# Thư mục sandbox demo cho Panel (invoke chỉ được chạm trong này).
DEMO_ROOT="${MUSE_UM_ROOT_DIR:-$REPO_DIR/demo-root}"
mkdir -p "$DEMO_ROOT"
printf 'hello from muse-um demo\n' > "$DEMO_ROOT/hello.txt"

# --- 1. Python + venv ------------------------------------------------------
PYBIN=""
for cand in python3.12 python3; do
    if command -v "$cand" >/dev/null 2>&1; then PYBIN="$cand"; break; fi
done
if [ -z "$PYBIN" ]; then
    echo "ERROR: need python3.12 (or python3); install Python 3.12 first." >&2
    exit 1
fi
echo "python: $($PYBIN --version)"

if [ ! -x "$REPO_DIR/.venv/bin/python" ]; then
    echo "== creating .venv =="
    "$PYBIN" -m venv "$REPO_DIR/.venv" || { echo "ERROR: venv creation failed" >&2; exit 1; }
else
    echo "== .venv already exists, reusing =="
fi
VENV_PY="$REPO_DIR/.venv/bin/python"
if ! "$VENV_PY" -c "import websockets; assert websockets.__version__ == '17.4'" 2>/dev/null; then
    echo "== installing websockets==17.4 =="
    "$VENV_PY" -m pip install -q "websockets==17.4" || { echo "ERROR: pip install failed" >&2; exit 1; }
fi
echo "websockets: $("$VENV_PY" -c "import websockets; print(websockets.__version__)")"

# --- helper: chờ HTTP 200 --------------------------------------------------
wait_http() {  # wait_http <url> <seconds> <label>
    "$VENV_PY" - "$1" "$2" <<'EOF'
import sys, time, urllib.request
url, timeout = sys.argv[1], float(sys.argv[2])
deadline = time.time() + timeout
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            if r.status == 200:
                sys.exit(0)
    except Exception:
        pass
    time.sleep(0.5)
sys.exit(1)
EOF
}

health_ok() {
    wait_http "$RELAY_URL/health" 2 "health" 2>/dev/null
}

# --- 2. Relay --------------------------------------------------------------
if health_ok; then
    echo "== Relay already healthy at $RELAY_URL, reusing it =="
else
    echo "== starting Relay on 127.0.0.1:8000 =="
    if [ ! -f "$REPO_DIR/relay/main.py" ]; then
        echo "ERROR: relay/main.py not found — Relay not built yet." >&2
        exit 1
    fi
    RELAY_HOST=127.0.0.1 PYTHONPATH="$REPO_DIR" nohup "$VENV_PY" -m relay.main \
        > "$RUN_DIR/relay.log" 2>&1 &
    echo $! > "$RUN_DIR/relay.pid"
    if wait_http "$RELAY_URL/health" 30 "health"; then
        echo "Relay up: $RELAY_URL/health -> 200"
    else
        echo "ERROR: Relay did not answer /health within 30s. Log:" >&2
        tail -n 30 "$RUN_DIR/relay.log" >&2
        exit 1
    fi
fi

# --- 3. Panel --------------------------------------------------------------
panel_online() {
    "$VENV_PY" - "$RELAY_URL" <<'EOF' 2>/dev/null
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + "/api/status", timeout=2) as r:
        sys.exit(0 if json.load(r).get("panel_online") is True else 1)
except Exception:
    sys.exit(1)
EOF
}

PANEL_STARTED=0
if panel_online; then
    echo "== Panel already online, reusing it =="
    PANEL_STARTED=1
else
    PANEL_CMD=""
    if [ -f "$REPO_DIR/app/panel.py" ]; then PANEL_CMD="$VENV_PY app/panel.py";
    elif [ -f "$REPO_DIR/app/main.py" ]; then PANEL_CMD="$VENV_PY app/main.py";
    elif [ -f "$REPO_DIR/app/__main__.py" ]; then PANEL_CMD="$VENV_PY -m app.panel";
    fi
    if [ -z "$PANEL_CMD" ]; then
        echo "WARNING: no Panel entry point found (app/main.py, app/panel.py) —"
        echo "         Relay is up, but login/invoke need the Panel. Skipping Panel start."
    else
        echo "== starting Panel (root_dir=$DEMO_ROOT) =="
        MUSE_UM_ROOT_DIR="$DEMO_ROOT" MUSE_UM_RELAY_URL="$RELAY_URL" \
            PYTHONPATH="$REPO_DIR" nohup $PANEL_CMD \
            > "$RUN_DIR/panel.log" 2>&1 &
        echo $! > "$RUN_DIR/panel.pid"
        for _ in $(seq 1 60); do panel_online && break; sleep 0.5; done
        if panel_online; then
            echo "Panel online."
            PANEL_STARTED=1
        else
            echo "WARNING: Panel did not come online within 30s. Log:" >&2
            tail -n 30 "$RUN_DIR/panel.log" >&2
            echo "         Pair/login will fail with APP_OFFLINE until the Panel runs."
        fi
    fi
fi

# --- 4. Hai tài khoản demo + mã pair ---------------------------------------
if [ "$PANEL_STARTED" -eq 1 ]; then
    echo "== creating 2 demo accounts + pair codes =="
    MUSE_UM_RELAY_URL="$RELAY_URL" "$VENV_PY" - <<'EOF'
import json, os, urllib.request

base = os.environ["MUSE_UM_RELAY_URL"]

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

for name in ("Demo A", "Demo B"):
    acc = call("POST", "/api/accounts", {"name": name})
    pair = call("POST", "/api/pair", {"account_id": acc["account_id"]})
    print(f"  {name}: account_id={acc['account_id']}  pair_code={pair['code']} (expires in 300s)")
print("  (Login tokens are NOT printed; use the pair codes above, one-time use.)")
EOF
else
    echo "== skipping account creation (Panel offline) =="
fi

# --- 5. Hướng dẫn tiếp theo -------------------------------------------------
cat <<EOF

== Demo đã sẵn sàng. Các lệnh tiếp theo (chạy trong 5 phút, trước khi mã pair hết hạn) ==
export MUSE_UM_RELAY_URL='$RELAY_URL'

# Client A (terminal 1):
export MUSE_UM_STATE_DIR=\$HOME/.cache/muse-um-demo-a
$VENV_PY -m tools.muse_client pair --code <MÃ-CỦA-DEMO-A>
$VENV_PY -m tools.muse_client daemon start
$VENV_PY -m tools.muse_client daemon status        # cần connected: true

# Client B (terminal 2):
export MUSE_UM_STATE_DIR=\$HOME/.cache/muse-um-demo-b
$VENV_PY -m tools.muse_client pair --code <MÃ-CỦA-DEMO-B>
$VENV_PY -m tools.muse_client daemon start

# Phòng chung (terminal 1): tạo phòng (người tạo tự join luôn),
# mời B (lấy account_id của B ở trên), rồi terminal 2 join phòng đó:
$VENV_PY -m tools.muse_client room create --name demo-room
$VENV_PY -m tools.muse_client room invite --room-id <room_id> --account-id <account_id_của_B>
# (terminal 2)
$VENV_PY -m tools.muse_client room join --room-id <room_id>
$VENV_PY -m tools.muse_client room post --text 'hello từ Demo B'

# Đọc file trên máy host (sandbox demo: $DEMO_ROOT):
$VENV_PY -m tools.muse_client invoke --tool read_file --args '{"path": "hello.txt"}'
# Nếu root đã đổi, client tự parse root mới từ message lỗi BAD_MESSAGE
# và thử lại một lần — không cần làm gì thêm.

# Kiểm tra đầy đủ:  $VENV_PY tests/test_e2e.py
# Dừng demo:       kill \$(cat $RUN_DIR/relay.pid $RUN_DIR/panel.pid 2>/dev/null)
# Logs:            $RUN_DIR/relay.log  $RUN_DIR/panel.log
EOF
