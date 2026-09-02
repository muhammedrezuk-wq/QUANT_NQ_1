"""The single egress. No atom opens a socket or a URL of its own.

Three primitives, because the project needs exactly three shapes today:
a JSON fetch over HTTP (620), a UDP request/reply (608, NTP), and a persistent
TLS byte stream (622, cTrader FIX). All three take an explicit timeout -- an
unbounded outbound call is how a pulse-driven atom turns into a hung one.

The stream primitive is deliberately protocol-blind: it moves bytes and nothing
else. Framing a byte stream into messages is the caller's business, so a wire
protocol never leaks into the transport layer.
"""
from __future__ import annotations

import json
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_TIMEOUT_S = 5.0


class TransportError(Exception):
    """Any outbound failure, named once instead of leaking urllib/socket types."""


def quote(value: str) -> str:
    return urllib.parse.quote(value)


def http_get_json(url: str, headers: dict[str, str] | None = None,
                  timeout_s: float = DEFAULT_TIMEOUT_S) -> Any:
    request = urllib.request.Request(url, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise TransportError(str(exc)) from exc


class StreamSession:
    """A persistent outbound byte stream, optionally over TLS.

    Why a thread and not asyncio: the consumer is a pulse-driven atom on the
    core's event loop. A blocking recv on that loop would stall every other
    atom, and a market-data session must never be the reason a decision is
    late. The socket lives here, on its own thread, and the atom only ever
    calls the non-blocking `drain`.

    The session never reconnects by itself. Reconnect policy is a decision
    (how often, how many times, whether to announce a gap), and decisions
    belong to the caller, not to the plumbing.
    """

    def __init__(self, host: str, port: int, *, use_tls: bool = True,
                 timeout_s: float = DEFAULT_TIMEOUT_S, read_chunk: int = 65536,
                 max_buffer_bytes: int = 0) -> None:
        self._host = host
        self._port = int(port)
        self._use_tls = bool(use_tls)
        self._timeout_s = float(timeout_s)
        self._read_chunk = int(read_chunk)
        self._max_buffer_bytes = max(0, int(max_buffer_bytes))
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._running = False
        self._error = ""
        self._connected_at = 0.0
        self._bytes_in = 0
        self._bytes_out = 0
        self._dropped_bytes = 0

    @property
    def connected(self) -> bool:
        return self._running and self._sock is not None

    @property
    def error(self) -> str:
        return self._error

    def stats(self) -> dict[str, Any]:
        with self._lock:
            pending = len(self._buffer)
        return {"host": self._host, "port": self._port, "tls": self._use_tls,
                "connected": self.connected, "bytes_in": self._bytes_in,
                "bytes_out": self._bytes_out, "pending_bytes": pending,
                "max_buffer_bytes": self._max_buffer_bytes,
                "dropped_bytes": self._dropped_bytes, "error": self._error}

    def connect(self) -> None:
        if self._running:
            return
        self._error = ""
        try:
            raw = socket.create_connection((self._host, self._port), timeout=self._timeout_s)
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if self._use_tls:
                context = ssl.create_default_context()
                sock = context.wrap_socket(raw, server_hostname=self._host)
            else:
                sock = raw
        except (OSError, ssl.SSLError, ValueError) as exc:
            self._error = str(exc)
            raise TransportError(str(exc)) from exc
        sock.settimeout(self._timeout_s)
        with self._lock:
            self._buffer = bytearray()
        self._sock = sock
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, name="transport-stream", daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        while self._running and self._sock is not None:
            try:
                chunk = self._sock.recv(self._read_chunk)
            except socket.timeout:
                continue
            except (OSError, ssl.SSLError) as exc:
                self._error = str(exc)
                break
            if not chunk:
                self._error = "PEER_CLOSED"
                break
            self._append_received(chunk)
        self._running = False

    def _append_received(self, chunk: bytes) -> None:
        """Append one receive chunk under the live-tail memory ceiling."""
        with self._lock:
            self._buffer.extend(chunk)
            self._bytes_in += len(chunk)
            # Optional live-tail policy: a protocol consumer may request a
            # strict memory/latency ceiling. Drop the oldest bytes while
            # holding the same lock, so pending_bytes is never observed above
            # the limit. The caller sees dropped_bytes and declares the gap.
            if self._max_buffer_bytes and len(self._buffer) > self._max_buffer_bytes:
                excess = len(self._buffer) - self._max_buffer_bytes
                del self._buffer[:excess]
                self._dropped_bytes += excess

    def send(self, data: bytes) -> None:
        sock = self._sock
        if sock is None or not self._running:
            raise TransportError("stream not connected")
        try:
            sock.sendall(data)
        except (OSError, ssl.SSLError) as exc:
            self._error = str(exc)
            self._running = False
            raise TransportError(str(exc)) from exc
        self._bytes_out += len(data)

    def drain(self) -> bytes:
        """Take everything received since the last call. Never blocks."""
        with self._lock:
            if not self._buffer:
                return b""
            data = bytes(self._buffer)
            self._buffer = bytearray()
        return data

    def close(self) -> None:
        self._running = False
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=self._timeout_s)


def udp_exchange(host: str, port: int, payload: bytes, reply_bytes: int,
                 timeout_s: float = DEFAULT_TIMEOUT_S) -> bytes:
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    if not addresses: raise TransportError("host resolution returned no address")
    family, socktype, proto, _, address = addresses[0]
    sock = socket.socket(family, socktype, proto)
    sock.settimeout(timeout_s)
    try:
        sock.connect(address)
        sock.send(payload)
        data = sock.recv(reply_bytes)
    except OSError as exc:
        raise TransportError(str(exc)) from exc
    finally:
        sock.close()
    return data
