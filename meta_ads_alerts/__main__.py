import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread

from .rules import evaluate, load_config
from .sources import DemoSource, MetaSource, SourceError


STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class Monitor:
    def __init__(self, source, mode, config, log_path):
        self.source = source
        self.mode = mode
        self.config = config
        self.log_path = log_path
        self._lock = Lock()
        self._state = {"mode": mode, "poll_seconds": config.poll_seconds,
                       "snapshot": None, "error": None, "log_error": False}
        self._logged = set()

    def _record(self, alerts, now):
        new = [alert for alert in alerts if alert.key not in self._logged]
        if new:
            self.log_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            fd = os.open(self.log_path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as file:
                for alert in new:
                    file.write(json.dumps({"logged_at": now, "mode": self.mode, **asdict(alert)},
                                          ensure_ascii=False) + "\n")
        self._logged = {alert.key for alert in alerts}

    def poll(self):
        now = datetime.now(timezone.utc).isoformat()
        try:
            snapshot = self.source.snapshot(self.config.thresholds.baseline_days)
            alerts = evaluate(snapshot, self.config.thresholds)
        except (SourceError, KeyError, TypeError, AttributeError) as error:
            message = str(error) if isinstance(error, SourceError) else "Nguồn trả dữ liệu không đúng định dạng."
            with self._lock:
                self._state.update(error=message, last_attempt=now)
            return
        log_error = False
        try:
            self._record(alerts, now)
        except OSError:
            log_error = True
        account = next((entity.today for entity in snapshot.entities if entity.level == "account"), None)
        payload = {
            "name": snapshot.name, "currency": snapshot.currency, "data_at": snapshot.at.isoformat(),
            "timezone": str(snapshot.at.tzinfo), "baseline_days": snapshot.baseline_days,
            "notes": snapshot.notes, "alerts": [asdict(alert) for alert in alerts],
            "metrics": {"spend": account.spend, "purchases": account.purchases,
                        "cpa": account.cpa, "roas": account.roas} if account else {},
        }
        with self._lock:
            self._state.update(snapshot=payload, error=None, log_error=log_error, last_attempt=now)

    def state(self):
        with self._lock:
            return dict(self._state)

    def run(self, stop):
        while not stop.is_set():
            try:
                self.poll()
            except Exception:
                with self._lock:
                    self._state.update(
                        error="Kiểm tra thất bại; app sẽ thử lại ở chu kỳ tiếp theo.",
                        last_attempt=datetime.now(timezone.utc).isoformat(),
                    )
            stop.wait(self.config.poll_seconds)


def make_handler(monitor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            port = self.server.server_port
            if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if self.path == "/api/state":
                body = json.dumps(monitor.state(), ensure_ascii=False).encode()
                content_type = "application/json; charset=utf-8"
            elif self.path in STATIC:
                filename, content_type = STATIC[self.path]
                body = Path(__file__).with_name(filename).read_bytes()
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def cli(argv=None):
    parser = argparse.ArgumentParser(description="Cảnh báo Meta Ads chỉ đọc.")
    parser.add_argument("command", nargs="?", choices=("serve", "check"), default="serve")
    parser.add_argument("--source", choices=("demo", "meta"), default="demo")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-path", type=Path,
                        default=Path.home() / ".local/state/meta-ads-alerts/alerts.jsonl")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        source = DemoSource() if args.source == "demo" else MetaSource.from_env()
        if args.command == "check":
            snapshot = source.snapshot(config.thresholds.baseline_days)
            for note in snapshot.notes:
                print(f"Lưu ý: {note}", file=sys.stderr)
            alerts = evaluate(snapshot, config.thresholds)
            if not alerts:
                print("Không có cảnh báo.")
            for alert in alerts:
                label = "KHẨN" if alert.severity == "critical" else "CẢNH BÁO"
                print(f"[{label}] {alert.entity_name} — {alert.title}: {alert.message} {alert.suggestion}")
            return 0
        if not 1 <= args.port <= 65535:
            raise ValueError("Port phải nằm trong khoảng 1–65535.")
        monitor = Monitor(source, args.source, config, args.log_path)
        with ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(monitor)) as server:
            stop = Event()
            Thread(target=monitor.run, args=(stop,), daemon=True).start()
            print(f"Meta Ads Alerts: http://127.0.0.1:{args.port} ({args.source})", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                stop.set()
        return 0
    except (SourceError, ValueError, OSError) as error:
        print(f"Lỗi: {error}", file=sys.stderr)
        return 1
    except (KeyError, TypeError, AttributeError):
        print("Lỗi: Nguồn trả dữ liệu không đúng định dạng.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
