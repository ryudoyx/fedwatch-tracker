"""本地看板：http://127.0.0.1:8766/"""
from __future__ import annotations

import json
import sys
import threading
import traceback
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import analysis
from .config import Config
from .pipeline import Tracker, safe_update
from .store import Store

WEB_DIR = (Path(__file__).resolve().parent / "web").resolve()


class App:
    def __init__(self, cfg: Config, store: Store):
        self.cfg = cfg
        self.store = store
        self.logs: deque[str] = deque(maxlen=200)
        self.tracker = Tracker(cfg, store, log=self._log)
        self._lock = threading.Lock()
        self.busy: str | None = None

    def _log(self, msg: str):
        print(msg, flush=True)
        self.logs.append(msg)

    def run_async(self, kind: str) -> str | None:
        with self._lock:
            if self.busy:
                return f"正在{self.busy}，稍等"
            self.busy = {"update": "抓取数据", "import": "导入官方文件"}[kind]

        def job():
            try:
                if kind == "update":
                    safe_update(self.tracker)
                else:
                    s = self.tracker.import_cme()
                    self._log(f"导入完成：{s['files']} 个文件 {s['rows']} 行" +
                              (f"，失败 {len(s['errors'])} 个" if s["errors"] else ""))
            except Exception as exc:
                traceback.print_exc()
                self._log(f"[x] 出错：{exc}")
            finally:
                self.busy = None

        threading.Thread(target=job, daemon=True).start()
        return None


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):
            try:
                url = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                source = q.get("source", "calc")
                if source not in analysis.SOURCES:
                    return self._json({"error": "source 只能是 calc 或 cme"}, 400)
                st = app.store
                if url.path == "/api/state":
                    out = analysis.state(st, app.cfg)
                    out["busy"] = app.busy
                    out["logs"] = list(app.logs)[-40:]
                    return self._json(out)
                if url.path == "/api/meeting":
                    return self._json(analysis.meeting_series(st, source, q.get("meeting", "")))
                if url.path == "/api/snapshot":
                    return self._json(analysis.snapshot(st, source, q.get("asof")))
                if url.path == "/api/terminal":
                    mode = "trough" if q.get("mode") == "trough" else "peak"
                    return self._json(analysis.terminal(st, source, q.get("horizon"), mode))
                name = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
                f = (WEB_DIR / name).resolve()
                if WEB_DIR not in f.parents or not f.is_file():
                    return self._send(404, b"not found", "text/plain")
                ctype = "text/html; charset=utf-8" if f.suffix == ".html" else "text/plain; charset=utf-8"
                return self._send(200, f.read_bytes(), ctype)
            except Exception as exc:
                traceback.print_exc()
                self._json({"error": str(exc)}, 500)

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                if self.path.startswith("/api/update"):
                    err = app.run_async("update")
                    return self._json({"ok": not err, "error": err})
                if self.path.startswith("/api/import"):
                    err = app.run_async("import")
                    return self._json({"ok": not err, "error": err})
                return self._json({"ok": False, "error": "未知接口"}, 404)
            except Exception as exc:
                traceback.print_exc()
                self._json({"ok": False, "error": str(exc)}, 500)

    return Handler


class _Server(ThreadingHTTPServer):
    # Windows 上 SO_REUSEADDR 允许两个实例抢同一个端口，关掉（同 ETF 监控）
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True


def _already_running(host: str, port: int) -> bool:
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 不走 Clash
    try:
        with opener.open(f"http://{host}:{port}/api/state", timeout=2) as r:
            return "sources" in json.loads(r.read().decode("utf-8"))
    except Exception:
        return False


def run(cfg: Config, *, host: str = "127.0.0.1", port: int | None = None, open_browser: bool = True):
    port = port or cfg.port
    url = f"http://{host}:{port}/"
    if _already_running(host, port):
        print(f"看板已经在运行：{url}")
        if open_browser:
            webbrowser.open(url)
        return
    store = Store(cfg.db_path)
    app = App(cfg, store)
    try:
        httpd = _Server((host, port), make_handler(app))
    except OSError as exc:
        store.close()
        raise SystemExit(f"端口 {port} 被占用（{exc}），换一个：serve --port {port + 1}")
    print(f"FedWatch 看板：{url}")
    print(f"数据库：{cfg.db_path}")
    print("关掉这个窗口就停止。\n")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        httpd.server_close()
        store.close()
