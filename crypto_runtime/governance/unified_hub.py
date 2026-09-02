"""Single-address dashboard hub for the unified Forex/Crypto project.

The browser stays on one origin/port. A small cookie selects which internal
market governance service receives /gov and /api requests. Switching the
market is a same-page state change; the React client reconnects its one
WebSocket to the new backend without changing the address bar.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("QUANT_HUB_PORT", "8090"))
BACKENDS = {"forex": ("127.0.0.1", 8092), "crypto": ("127.0.0.1", 8093)}
DIST = ROOT / "governance" / "ui" / "built"
# منافذ اللوحتين العلنيّتين (نفس جدول scripts/launch_market.py).
UI_PORTS = {"forex": 8090, "crypto": 8091}
# ٢٠٢٦-٠٨-٣١ (ختم NQ): الكوكي كان `QUANT_MARKET` مجرّدًا، والمتصفّح **لا يفصل
# الكوكي بالمنفذ** — 8090 و8091 على نفس المضيف فيتشاركان الجرّة. فأوّل تبديل
# إلى الكريبتو كان يكتب `QUANT_MARKET=crypto` على `127.0.0.1` كلّه، فيصير
# منفذ الفوركس نفسه يخدم كريبتو لأنّ الكوكي يسبق افتراض المنفذ في `_market`
# (مقاس حيًّا: المساران يفتحان كريبتو). الاسم صار مرقّمًا بالمنفذ، فلكلّ لوحة
# كوكيها، والتبديل داخل الصفحة يبقى كما هو.
COOKIE = f"QUANT_MARKET_{PORT}"
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _market(handler: BaseHTTPRequestHandler) -> str:
    raw = handler.headers.get("Cookie", "")
    for item in raw.split(";"):
        key, _, value = item.strip().partition("=")
        if key == COOKIE and value in BACKENDS:
            return value
    default = os.environ.get("QUANT_HUB_DEFAULT_MARKET", "forex").lower()
    return default if default in BACKENDS else "forex"


def _accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + _WS_MAGIC).encode("ascii")).digest()).decode("ascii")


def _backend_url(market: str, path: str) -> str:
    host, port = BACKENDS[market]
    return f"http://{host}:{port}{path}"


class HubHandler(BaseHTTPRequestHandler):
    server_version = "QUANT-Unified-Hub/1.0"

    def _headers(self, *, content_type: str = "application/json; charset=utf-8", length: int = 0) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _json(self, status: int, payload: dict, *, cookie: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._headers(length=len(body))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        try:
            length = max(0, min(int(self.headers.get("Content-Length", "0")), 2_000_000))
        except ValueError:
            length = 0
        return self.rfile.read(length) if length else b""

    def _select(self) -> None:
        try:
            body = json.loads(self._read_body() or b"{}")
            market = str(body.get("market") or "").lower()
        except (ValueError, TypeError):
            market = ""
        if market not in BACKENDS:
            self._json(400, {"ok": False, "error": "market must be forex or crypto"})
            return
        self._json(200, {
            "ok": True, "market": market,
            "label": "فوركس" if market == "forex" else "كريبتو",
        }, cookie=f"{COOKIE}={market}; Path=/; SameSite=Lax")

    def _proxy(self, market: str, method: str) -> None:
        path = self.path
        body = self._read_body() if method == "POST" else None
        headers = {}
        for name in ("Accept", "Content-Type", "X-API-Key", "Origin"):
            value = self.headers.get(name)
            if value:
                headers[name] = value
        request = urllib.request.Request(
            _backend_url(market, path), data=body, headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "application/octet-stream")
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            status = exc.code
            content_type = exc.headers.get("Content-Type", "application/json; charset=utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._json(502, {"error": f"market-backend-unreachable:{market}:{type(exc).__name__}"})
            return
        self.send_response(status)
        self._headers(content_type=content_type, length=len(payload))
        self.end_headers()
        self.wfile.write(payload)

    def _inline_assets(self, html: bytes) -> bytes:
        """إصلاح الملعب: حقن CSS/JS داخل الـHTML نفسه — صفحة مكتفية بذاتها.

        السبب: عبر سلاسل البروكسي/الكاش المتعددة كان يمكن أن يُفقد ملف الستايل
        فتظهر اللوحة نصًّا خامًا بلا تصميم. بالحقن يستحيل ذلك: كل شيء يصل
        مع الـHTML نفسه في استجابة واحدة."""
        import re as _re
        text = html.decode("utf-8")
        m = _re.search(r'<link[^>]+href="\./assets/([^"]+\.css)"[^>]*>', text)
        if m and (DIST / "assets" / m.group(1)).is_file():
            css = (DIST / "assets" / m.group(1)).read_text(encoding="utf-8")
            text = text.replace(m.group(0), "<style>" + css + "</style>")
        m = _re.search(r'<script[^>]+src="\./assets/([^"]+\.js)"[^>]*></script>', text)
        if m and (DIST / "assets" / m.group(1)).is_file():
            js = (DIST / "assets" / m.group(1)).read_text(encoding="utf-8")
            text = text.replace(m.group(0), "<script type=" + chr(34) + "module" + chr(34) + ">" + js + "</script>")
        return text.encode("utf-8")

    def _serve_static(self) -> None:
        path = urlparse(self.path).path
        rel = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        target = (DIST / rel).resolve()
        if not target.is_relative_to(DIST.resolve()) or not target.is_file():
            target = DIST / "index.html"
        if not target.is_file():
            self._json(503, {"error": "dashboard-build-missing"})
            return
        types = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".woff": "font/woff"}
        data = target.read_bytes()
        if target.name == "index.html":
            data = self._inline_assets(data)
        self.send_response(200)
        self._headers(content_type=types.get(target.suffix, "application/octet-stream"), length=len(data))
        self.end_headers()
        self.wfile.write(data)

    def _ws_relay(self, market: str) -> None:
        client_key = self.headers.get("Sec-WebSocket-Key", "")
        if not client_key:
            self._json(400, {"error": "websocket-key-missing"})
            return
        host, port = BACKENDS[market]
        try:
            upstream = socket.create_connection((host, port), timeout=5)
            upstream_key = base64.b64encode(os.urandom(16)).decode("ascii")
            request = (
                f"GET /gov/ws/core HTTP/1.1\r\nHost: {host}:{port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {upstream_key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            )
            upstream.sendall(request.encode("ascii"))
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = upstream.recv(4096)
                if not chunk:
                    raise ConnectionError("backend websocket closed")
                response += chunk
            if not response.startswith(b"HTTP/1.1 101"):
                raise ConnectionError("backend websocket handshake rejected")
            self.connection.sendall((
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {_accept(client_key)}\r\n\r\n"
            ).encode("ascii"))
            # إصلاح م-5 (ورقة ٤١ ← ٣٩ بند ٥، بأمر المالك 2026-08-28): بايتات أول
            # إطار بثّ تلتف غالبًا مع ردّ المصافحة؛ كانت تُرمى فتتزحزح محاذاة
            # البثّ ويتجمّد بعد أول لقطة. تُمرَّر الآن كما هي.
            _, _, leftover = response.partition(b"\r\n\r\n")
            if leftover:
                self.connection.sendall(leftover)
        except (OSError, ConnectionError) as exc:
            try:
                self._json(502, {"error": f"websocket-backend-unreachable:{market}:{type(exc).__name__}"})
            finally:
                try: upstream.close()
                except Exception: pass
            return

        def pump(source: socket.socket, target: socket.socket) -> None:
            try:
                while True:
                    data = source.recv(65536)
                    if not data:
                        break
                    target.sendall(data)
            except OSError:
                pass
            finally:
                try: target.shutdown(socket.SHUT_WR)
                except OSError: pass

        a = threading.Thread(target=pump, args=(self.connection, upstream), daemon=True)
        b = threading.Thread(target=pump, args=(upstream, self.connection), daemon=True)
        a.start(); b.start(); a.join(); b.join()
        try: upstream.close()
        except OSError: pass
        self.close_connection = True

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/unified/market" or path == "/gov/market":
            market = _market(self)
            alternate = "crypto" if market == "forex" else "forex"
            self._json(200, {
                "market": market,
                "label": "فوركس" if market == "forex" else "كريبتو",
                # كان `PORT` (منفذ هذه اللوحة نفسها)، فزرّ الانتقال للسوق الآخر
                # يعيدك إلى مكانك. الآن منفذ اللوحة الأخرى فعلًا — ختم NQ ٢٠٢٦-٠٨-٣١.
                "alternate_port": UI_PORTS[alternate],
                "alternate_label": "كريبتو" if alternate == "crypto" else "فوركس",
                "single_origin": True,
            })
            return
        if path == "/gov/ws/core":
            self._ws_relay(_market(self))
            return
        if path.startswith("/gov/") or path.startswith("/api/"):
            self._proxy(_market(self), "GET")
            return
        self._serve_static()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/unified/select":
            self._select()
            return
        if path.startswith("/gov/") or path.startswith("/api/"):
            self._proxy(_market(self), "POST")
            return
        self._json(404, {"error": "not-found"})

    def log_message(self, *_args) -> None:
        return


def main() -> None:
    # Public-preview safe default: the single visible dashboard origin is
    # reachable outside localhost when the environment supports it. The market
    # governance backends remain localhost-only implementation details.
    host = os.environ.get("QUANT_HUB_HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, PORT), HubHandler)
    print(f"Unified dashboard listening on {host}:{PORT}")
    print("Internal market dashboards: 8092=forex, 8093=crypto")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
