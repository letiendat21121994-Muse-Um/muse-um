# Quickstart — Demo muse-um trên một máy trong 5 phút

Chạy Relay + Panel + 2 client demo ngay trên máy này (không cần máy host
Windows, không cần Tailscale).

## 1. Chạy demo

```bash
cd ~/workspace/muse-um
./quickstart.sh
```

Script sẽ:

1. Tạo `.venv` (Python 3.12) nếu chưa có, cài `websockets==17.4`.
2. Khởi động Relay trên `127.0.0.1:8000` và Panel ở chế độ nền, chờ
   `/health` trả 200.
3. Tạo 2 tài khoản demo (**Demo A**, **Demo B**) + mã pair (8 ký tự, hết
   hạn sau **5 phút** — làm bước 2 ngay).
4. In ra các lệnh tiếp theo.

Chạy lại script được: venv cũ được dùng lại, Relay đang chạy khỏe được dùng
lại, chỉ tạo thêm tài khoản demo mới với mã pair mới.

## 2. Pair + daemon (làm ngay, trước khi mã pair hết hạn)

```bash
export MUSE_UM_RELAY_URL='http://127.0.0.1:8000'

# Terminal 1 — client A:
export MUSE_UM_STATE_DIR=$HOME/.cache/muse-um-demo-a
.venv/bin/python -m tools.muse_client pair --code <MÃ-CỦA-DEMO-A>
.venv/bin/python -m tools.muse_client daemon start
.venv/bin/python -m tools.muse_client daemon status   # cần connected: true

# Terminal 2 — client B:
export MUSE_UM_STATE_DIR=$HOME/.cache/muse-um-demo-b
.venv/bin/python -m tools.muse_client pair --code <MÃ-CỦA-DEMO-B>
.venv/bin/python -m tools.muse_client daemon start
```

## 3. Phòng chung + gửi tin

```bash
# Terminal 1: tạo phòng, mời B (lấy account_id của B ở bước 1)
.venv/bin/python -m tools.muse_client room create --name demo-room
.venv/bin/python -m tools.muse_client room invite --room-id <room_id> --account-id <account_id_của_B>

# Terminal 2: join phòng, gửi tin
.venv/bin/python -m tools.muse_client room join --room-id <room_id>
.venv/bin/python -m tools.muse_client room post --text 'hello từ Demo B'

# Terminal 1: xem tin (N tin CŨ NHẤT)
.venv/bin/python -m tools.muse_client room list --limit 10
```

## 4. Đọc file trên "máy host" (sandbox demo)

```bash
.venv/bin/python -m tools.muse_client invoke --tool read_file --args '{"path": "hello.txt"}'
```

Invoke chỉ được chạm trong thư mục sandbox demo (`demo-root/`). Nếu báo
`BAD_MESSAGE: root_id '<cũ>' does not match the open root '<mới>'`, lấy
root mới từ chính message lỗi đó và thử lại — xem `README.md` mục 8.3.

## 5. Dừng demo

```bash
kill $(cat .quickstart/relay.pid .quickstart/panel.pid 2>/dev/null)
# + daemon stop cho từng client:
MUSE_UM_STATE_DIR=$HOME/.cache/muse-um-demo-a .venv/bin/python -m tools.muse_client daemon stop
MUSE_UM_STATE_DIR=$HOME/.cache/muse-um-demo-b .venv/bin/python -m tools.muse_client daemon stop
```

Logs: `.quickstart/relay.log`, `.quickstart/panel.log`.

## 6. Kiểm tra đầy đủ

```bash
.venv/bin/python tests/test_e2e.py
```

Test E2E chạy thật toàn bộ chuỗi (Relay + Panel + 2 client + phòng +
invoke + upload chunked ~600 KB + SHA-256), in `[PASS]`/`[FAIL]` từng bước.
Chi tiết trong `README.md` mục 14.
