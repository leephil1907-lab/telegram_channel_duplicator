"""Small authenticated web control panel for TeleBridge.

Set TELEBRIDGE_WEB_PASSWORD before starting this service. Telegram's first login
must be completed separately with the CLI so the Telethon session exists.
"""
import base64
import hmac
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from telegram_channel_duplicator.config_controller import ConfigController

ROOT = Path(__file__).resolve().parent
PASSWORD = os.environ.get("TELEBRIDGE_WEB_PASSWORD", "")
HOST = os.environ.get("TELEBRIDGE_WEB_HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("TELEBRIDGE_WEB_PORT", "8080")))
worker = None
worker_lock = threading.Lock()


def authorized(header):
    if not PASSWORD:
        return False
    try:
        scheme, encoded = header.split(" ", 1)
        if scheme.lower() != "basic":
            return False
        decoded = base64.b64decode(encoded).decode("utf-8")
        _, supplied = decoded.split(":", 1)
        return hmac.compare_digest(supplied, PASSWORD)
    except (ValueError, UnicodeError):
        return False


def worker_state():
    global worker
    with worker_lock:
        if worker is not None and worker.poll() is not None:
            worker = None
        return {"running": worker is not None, "pid": worker.pid if worker else None}


class Handler(BaseHTTPRequestHandler):
    server_version = "TeleBridgeWeb/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.client_address[0], fmt % args))

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload), "application/json; charset=utf-8")

    def do_GET(self):
        if not authorized(self.headers.get("Authorization", "")):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="TeleBridge Toolkit"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        path = urlparse(self.path).path
        if path == "/api/status":
            try:
                config = ConfigController.get_config()
                groups = [{"name": g["name"], "sources": len(g["sources"]), "destinations": len(g["destinations"])} for g in config["groups"]]
                self._json(200, {"ok": True, **worker_state(), "groups": groups, "delay": config["delay"]})
            except Exception as exc:
                self._json(200, {"ok": False, **worker_state(), "error": str(exc)})
            return
        if path == "/":
            page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
            self._send(200, page, "text/html; charset=utf-8")
            return
        if path == "/app.js":
            self._send(200, (ROOT / "web" / "app.js").read_text(encoding="utf-8"), "text/javascript; charset=utf-8")
            return
        if path == "/style.css":
            self._send(200, (ROOT / "web" / "style.css").read_text(encoding="utf-8"), "text/css; charset=utf-8")
            return
        self._json(404, {"error": "Not found"})

    def do_POST(self):
        # Reject cross-origin browser requests to state-changing endpoints.
        origin = self.headers.get("Origin")
        if origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc != self.headers.get("Host", ""):
                self._json(403, {"error": "Cross-origin requests are not allowed."})
                return
        elif self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            self._json(403, {"error": "Cross-origin requests are not allowed."})
            return
        if not authorized(self.headers.get("Authorization", "")):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="TeleBridge Toolkit"')
            self.end_headers()
            return
        global worker
        path = urlparse(self.path).path
        if path not in ("/api/start", "/api/stop"):
            self._json(404, {"error": "Not found"})
            return
        if path == "/api/start":
            if not os.environ.get("TELEBRIDGE_WEB_PASSWORD"):
                self._json(503, {"error": "Set TELEBRIDGE_WEB_PASSWORD before starting the web panel."})
                return
            try:
                ConfigController.get_config()
            except Exception as exc:
                self._json(400, {"error": "Configuration is invalid: " + str(exc)})
                return
            with worker_lock:
                if worker is not None and worker.poll() is None:
                    self._json(409, {"error": "Forwarding worker is already running."})
                    return
                session_files = list((ROOT / "sessions").glob("*")) if (ROOT / "sessions").exists() else []
                if not session_files:
                    self._json(409, {"error": "No Telegram session found. Complete first-time Telegram login from the CLI before starting from the web panel."})
                    return
                log_handle = open(ROOT / "logs" / "web-worker.log", "a", encoding="utf-8")
                try:
                    worker = subprocess.Popen(
                        [sys.executable, str(ROOT / "main.py")],
                        cwd=str(ROOT),
                        stdin=subprocess.DEVNULL,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        env=os.environ.copy(),
                        start_new_session=True,
                    )
                finally:
                    log_handle.close()
                self._json(200, {"ok": True, "message": "Forwarding worker started.", "pid": worker.pid})
            return
        with worker_lock:
            if worker is None or worker.poll() is not None:
                worker = None
                self._json(200, {"ok": True, "message": "Forwarding worker is already stopped."})
                return
            worker.terminate()
            try:
                worker.wait(timeout=8)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=3)
            worker = None
        self._json(200, {"ok": True, "message": "Forwarding worker stopped."})


if __name__ == "__main__":
    if not PASSWORD:
        raise SystemExit("Refusing to start: set TELEBRIDGE_WEB_PASSWORD to a strong unique password.")
    (ROOT / "logs").mkdir(exist_ok=True)
    # Binding to loopback is the safe default. Set a public host only behind HTTPS
    # and a trusted reverse proxy.
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
