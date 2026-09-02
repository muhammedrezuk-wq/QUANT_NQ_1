from __future__ import annotations

import asyncio
import time
from typing import Any

import transport
from core.contracts.atom import AtomBase, AtomContext, HealthState, HealthStatus
from ctrader_stream_state import ACTIVE, DEAD, NEVER_SEEN, STALE, StreamTracker
from shared import fix_session as fx

ATOM_VERSION = "1.4.0"
# v1.4.0 (2026-08-27, item 20/27 of the 27-atom review -- a synchronous
# TLS connect freezes the loop, no connection timeout): _connect() called
# stream.connect() (TCP handshake + TLS handshake, up to
# transport.DEFAULT_TIMEOUT_S=5.0s in the ordinary case -- and DNS
# resolution inside socket.create_connection has NO timeout bound at
# all) directly on the atom's own coroutine, i.e. on the shared core
# event loop. transport/client.py's own docstring states the design
# principle this violated: "an unbounded outbound call is how a
# pulse-driven atom turns into a hung one" -- true here even bounded,
# since ANY multi-second block on the shared loop stalls every other
# atom. _teardown() (called as _connect()'s own first line) had the same
# problem via stream.close()'s thread.join(timeout=...). Both now run
# via asyncio.to_thread.
EVENT_TIME = "SYS_SECOND"
EVENT_TICK = "feed.ctrader.tick"
EVENT_SPECS = "market.ctrader.symbol_specs"
EVENT_DEPTH = "market.depth"
EVENT_HEARTBEAT = "feed.ctrader.heartbeat"
EVENT_STATE = "feed.ctrader.state"

STATE_DOWN, STATE_CONNECTING, STATE_READY = "DOWN", "CONNECTING", "READY"
MD_REQ_PREFIX = "MD-"


class Atom(AtomBase):
    """cTrader price feed over FIX -- no desktop platform, no bridge file.

    Publishes exactly the events the archived atom 617 published, same payload
    shape, so every consumer downstream (613, 106, 521, 103, 150 ...) keeps
    working untouched while the source underneath changes.

    Two facts this atom refuses to invent: the symbol identity (cTrader
    addresses symbols by NUMBER, taken from the venue's own SecurityList) and
    the time of a price (taken from the message that carried it, never from
    this machine's clock).
    """

    def __init__(self) -> None:
        self._context: AtomContext | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._stream: transport.StreamSession | None = None
        self._session: fx.FixSession | None = None
        self._buffer = b""
        self._books: dict[str, fx.Book] = {}
        self._last_top: dict[str, tuple] = {}
        self._by_id: dict[str, str] = {}
        self._by_name: dict[str, str] = {}
        self._link = STATE_DOWN
        self._last_error = ""
        self._auth_failed = False
        self._ever_logged_on = False
        self._last_sent = 0.0
        self._sequence = 0
        self._max_backlog = 131072
        self._skipped_bytes = 0
        self._jumps = 0
        self._transport_dropped_seen = 0
        self._fabric_gap = False
        self._connects = 0
        self._ticks = self._depth = self._heartbeats = self._unknown = 0
        self._tracker = StreamTracker({"tick": 30.0, "depth": 30.0, "spec": 600.0}, 120.0)
        self._official_time = 0.0
        self._wanted: list[str] = []
        self._account = ""
        self._broker = ""
        self._password_key = ""
        self._market_depth = 0
        self._poll_interval_s = 0.005
        self._reconnect_backoff_s = 5.0
        self._heartbeat_s = 30
        self._host = ""
        self._port = 0
        self._use_tls = True

    async def initialize(self, context: AtomContext) -> None:
        self._context = context
        cfg = context.config
        self._host = str(cfg["host"])
        self._port = int(cfg["port"])
        self._use_tls = bool(cfg["use_tls"])
        # v1.3.0 (2026-08-25): the FEED CREDENTIAL is not a trading identity.
        # Ticks used to stamp account_id = the FIX username (6175701, the live
        # quote account), so the WHOLE decision chain ran on a scope no
        # execution account matches -- 601 would reject any passing decision
        # with ACCOUNT_ID_MISMATCH (measured). identity_account_id declares
        # the trading account the ticks belong to.
        # v1.3.1: the two roles are SEPARATE fields -- _login goes to the FIX
        # session (must equal the SenderCompID's account or the broker refuses
        # the logon: measured live, FIX_LOGON_REFUSED "Username does not match
        # SenderCompID"), _account stamps the published identity only.
        self._login = str(cfg["username"])
        self._account = str(cfg.get("identity_account_id") or cfg["username"])
        self._broker = str(cfg["broker"])
        self._password_key = str(cfg["password_secret_key"])
        self._wanted = [str(name).strip() for name in cfg["symbols"] if str(name).strip()]
        self._market_depth = int(cfg["market_depth"])
        self._heartbeat_s = int(cfg["heartbeat_s"])
        self._poll_interval_s = float(cfg["poll_interval_s"])
        self._reconnect_backoff_s = float(cfg["reconnect_backoff_s"])
        self._max_backlog = int(cfg["live_max_backlog_bytes"])
        self._tracker = StreamTracker(
            {"tick": float(cfg["tick_stale_after_s"]), "depth": float(cfg["depth_stale_after_s"]),
             "spec": float(cfg["specs_stale_after_s"])}, float(cfg["dead_after_s"]))
        self._session = fx.FixSession(str(cfg["sender_comp_id"]), str(cfg["target_comp_id"]),
                                      str(cfg["sub_id"]), self._login, self._heartbeat_s)
        context.subscribe(EVENT_TIME, self._on_pulse)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._auth_failed = False
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        await self._teardown()

    async def shutdown(self) -> None:
        await self.stop()

    async def _teardown(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            await asyncio.to_thread(stream.close)
        self._link = STATE_DOWN
        self._buffer = b""

    def _password(self) -> str:
        """The atom names a key; it never holds or stores the value (rule 8)."""
        from security import get_secret_provider
        return str(get_secret_provider().get_secret(self._password_key) or "")

    async def _run(self) -> None:
        while self._running:
            try:
                if self._auth_failed:
                    # A refused logon is not a flaky link. Retrying it on a loop
                    # is how a live account gets locked out by its own broker,
                    # so the atom stops and says so until a human restarts it.
                    await asyncio.sleep(self._reconnect_backoff_s)
                    continue
                if self._stream is None or not self._stream.connected:
                    await self._connect()
                await self._pump()
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                self._last_error = "%s: %s" % (type(exc).__name__, exc)
                await self._teardown()
                await self._announce(STATE_DOWN, {"error": self._last_error})
                await asyncio.sleep(self._reconnect_backoff_s)

    async def _connect(self) -> None:
        await self._teardown()
        self._link = STATE_CONNECTING
        password = self._password()
        if not password:
            raise transport.TransportError("NO_PASSWORD_IN_VAULT:%s" % self._password_key)
        stream = transport.StreamSession(
            self._host, self._port, use_tls=self._use_tls,
            max_buffer_bytes=self._max_backlog,
        )
        # v1.4.0: TCP connect + TLS handshake (up to several seconds, DNS
        # resolution unbounded) moved off the shared event loop.
        await asyncio.to_thread(stream.connect)
        self._stream = stream
        self._transport_dropped_seen = 0
        self._session = fx.FixSession(self._session.sender_comp_id, self._session.target_comp_id,
                                      self._session.sub_id, self._login, self._heartbeat_s)
        now = time.time()
        stream.send(self._session.logon(password, now))
        self._last_sent = now
        self._connects += 1
        self._books.clear()
        self._last_top.clear()
        self._tracker.rebase()
        await self._announce(STATE_CONNECTING, {"connects": self._connects})

    async def _pump(self) -> None:
        stream, session = self._stream, self._session
        if stream is None or session is None:
            return
        # The session beat is serviced BEFORE the backlog, never after. Measured
        # 2026-08-21: publishing a burst took longer than the 30s heartbeat
        # window, the venue stopped hearing from us and cut the session every
        # few minutes. A feed must keep its own line alive before it feeds
        # anyone else.
        now = time.time()
        if session.logged_on and now - self._last_sent >= self._heartbeat_s:
            stream.send(session.heartbeat(now))
            self._last_sent = now
        stream_stats = stream.stats()
        transport_dropped = int(stream_stats.get("dropped_bytes") or 0)
        if transport_dropped > self._transport_dropped_seen:
            skipped = transport_dropped - self._transport_dropped_seen
            self._transport_dropped_seen = transport_dropped
            self._skipped_bytes += skipped
            self._jumps += 1
            self._fabric_gap = True
            self._books.clear()
            self._last_top.clear()
            await self._announce(STATE_READY, {
                "gap": "TRANSPORT_LIVE_TAIL",
                "skipped_bytes": skipped,
                "jumps_total": self._jumps,
            })
        self._buffer += stream.drain()
        if len(self._buffer) > self._max_backlog > 0:
            # Owner's feed law: the live line carries no backlog. Above the
            # limit, jump to the tail and declare the gap -- never queue history
            # behind a live price. The ladders are dropped with it: a book
            # rebuilt on top of skipped updates is a book that lies.
            skipped = len(self._buffer) - self._max_backlog
            tail = self._buffer[-self._max_backlog:]
            at = tail.find(fx.HEADER)
            self._buffer = tail[at:] if at > 0 else tail
            self._skipped_bytes += skipped
            self._jumps += 1
            self._fabric_gap = True
            self._books.clear()
            self._last_top.clear()
            await self._announce(STATE_READY, {"gap": "LIVE_LINE_JUMPED",
                                               "skipped_bytes": skipped,
                                               "jumps_total": self._jumps})
        messages, self._buffer = fx.frame(self._buffer)
        for raw in messages:
            await self._dispatch(fx.parse(raw))
        if not stream.connected:
            raise transport.TransportError(stream.error or "LINK_LOST")

    async def _dispatch(self, pairs: list[tuple[int, str]]) -> None:
        session, stream = self._session, self._stream
        if session is None or stream is None:
            return
        msg_type = session.observe(pairs)
        stamp = fx.parse_sending_time(fx.first(pairs, fx.TAG_SENDING_TIME) or "")
        if msg_type == fx.MSG_LOGON:
            self._ever_logged_on = True
            now = time.time()
            stream.send(session.security_list_request("SEC-%d" % self._connects, now))
            self._last_sent = now
            await self._announce(STATE_CONNECTING, {"logged_on": True})
            return
        if msg_type == fx.MSG_TEST_REQUEST:
            now = time.time()
            stream.send(session.heartbeat(now, fx.first(pairs, fx.TAG_TEST_REQ_ID)))
            self._last_sent = now
            return
        if msg_type == fx.MSG_HEARTBEAT:
            self._heartbeats += 1
            await self._publish(EVENT_HEARTBEAT, self._common("*", stamp))
            return
        if msg_type == fx.MSG_SECURITY_LIST:
            await self._on_security_list(pairs, stamp)
            return
        if msg_type in (fx.MSG_SNAPSHOT, fx.MSG_INCREMENTAL):
            await self._on_market_data(pairs, stamp, msg_type == fx.MSG_SNAPSHOT)
            return
        if msg_type in (fx.MSG_REJECT, fx.MSG_MD_REQUEST_REJECT, fx.MSG_LOGOUT):
            self._last_error = session.last_error
            if msg_type == fx.MSG_LOGOUT and not self._ever_logged_on:
                self._auth_failed = True
            await self._announce(STATE_DOWN, {"reject": session.last_error,
                                              "auth_failed": self._auth_failed})
            if msg_type == fx.MSG_LOGOUT:
                # The venue ended the session. Leaving the socket open looked
                # alive to the reconnect check while nothing arrived -- measured
                # as five silent minutes in CloseWait.
                raise transport.TransportError("LOGOUT: %s" % session.last_error)
            return
        if not fx.FixSession.is_admin(msg_type):
            self._unknown += 1

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _common(self, symbol: str, stamp: float | None, *, sequence: int | None = None,
                sequence_gap: bool = False, out_of_order: bool = False,
                fabric_gap: bool = False) -> dict[str, Any]:
        if sequence is None:
            sequence = self._next_sequence()
        return {"provider": "CTRADER", "account_id": self._account, "broker": self._broker,
                "build": "QUANT_NQ_FIX_%s" % ATOM_VERSION, "symbol": symbol,
                "timestamp": stamp if stamp is not None else self._official_time,
                "sequence": sequence, "sequence_gap": sequence_gap,
                "out_of_order": out_of_order, "fabric_gap": fabric_gap}

    async def _publish(self, event: str, payload: dict[str, Any]) -> None:
        if self._context is not None:
            await self._context.publish(event, payload)

    async def _announce(self, state: str, extra: dict[str, Any]) -> None:
        self._link = state
        await self._publish(EVENT_STATE, {"provider": "CTRADER", "state": state,
                                          "account_id": self._account, **extra})

    async def _on_security_list(self, pairs: list[tuple[int, str]], stamp: float | None) -> None:
        rows = fx.read_security_list(pairs)
        if not rows:
            return
        for row in rows:
            symbol_id = row.get(fx.TAG_SYMBOL, "")
            name = row.get(fx.TAG_SYMBOL_NAME, "")
            if symbol_id and name:
                self._by_id[symbol_id] = name
                self._by_name[name] = symbol_id
        stream, session = self._stream, self._session
        if stream is None or session is None:
            return
        missing = [name for name in self._wanted if name not in self._by_name]
        subscribed = []
        for name in self._wanted:
            symbol_id = self._by_name.get(name)
            if not symbol_id:
                continue
            now = time.time()
            stream.send(session.market_data_request(MD_REQ_PREFIX + symbol_id, symbol_id,
                                                    self._market_depth, now))
            self._last_sent = now
            subscribed.append(name)
        await self._publish(EVENT_SPECS, {"provider": "CTRADER", "account_id": self._account,
                                          "published_at": stamp if stamp is not None else self._official_time,
                                          "sequence": self._next_sequence(),
                                          "symbols": [dict(self._common(self._by_id.get(row.get(fx.TAG_SYMBOL, ""), ""), stamp),
                                                           **{"venue_symbol_id": row.get(fx.TAG_SYMBOL, ""),
                                                              "venue_fields": {str(k): v for k, v in row.items()}})
                                                      for row in rows if row.get(fx.TAG_SYMBOL_NAME) in self._by_name]})
        await self._announce(STATE_READY, {"listed": len(rows), "subscribed": subscribed,
                                           "missing": missing})

    async def _on_market_data(self, pairs: list[tuple[int, str]], stamp: float | None,
                              is_snapshot: bool) -> None:
        entries = fx.read_md_entries(pairs)
        if not entries:
            return
        # The request id is ours and rides on every W/X. Tag 55 does not: a
        # delete entry carries 279 and 278 only, so a delete-only message has no
        # symbol at all and would be dropped whole, drifting the book silently.
        request = fx.first(pairs, fx.TAG_MD_REQ_ID) or ""
        symbol_id = (request[len(MD_REQ_PREFIX):] if request.startswith(MD_REQ_PREFIX)
                     else "") or fx.first(pairs, fx.TAG_SYMBOL) or ""
        name = self._by_id.get(symbol_id, "")
        if not name:
            self._unknown += 1
            return
        book = self._books.setdefault(name, fx.Book())
        if is_snapshot:
            book.clear()
        for row in entries:
            entry_type, entry_id, price, size, action = fx.entry_values(row)
            if not entry_id:
                continue
            book.apply(entry_type, entry_id, price, size,
                       fx.ACTION_NEW if is_snapshot else action)
        when = stamp if stamp is not None else self._official_time
        bids, asks = book.ladder()
        if bids or asks:
            self._tracker.mark(self._account, name, "depth", when)
            await self._publish(EVENT_DEPTH, dict(self._common(name, stamp), bids=bids, asks=asks))
            self._depth += 1
        best_bid, best_ask = book.top()
        # A tick is a QUOTE CHANGE. Republishing an unchanged top carries no new
        # price and costs a full fan-out through every consumer downstream --
        # bus time that the live line then loses to a declared gap instead.
        top, prev = (best_bid, best_ask), self._last_top.get(name)
        self._last_top[name] = top
        if top != prev and best_bid is not None and best_ask is not None and best_bid > 0 and best_ask >= best_bid:
            self._tracker.mark(self._account, name, "tick", when)
            nseq = self._next_sequence()
            obs = self._tracker.observe(self._account, name, "tick", when, nseq)
            fg = self._fabric_gap
            self._fabric_gap = False
            await self._publish(EVENT_TICK, dict(
                self._common(name, stamp, sequence=nseq, sequence_gap=obs["sequence_gap"],
                             out_of_order=obs["out_of_order"], fabric_gap=fg),
                bid=best_bid, ask=best_ask, price=(best_bid + best_ask) / 2.0,
                exchange_timestamp=when))
            self._ticks += 1

    async def _on_pulse(self, payload: dict[str, Any]) -> None:
        official = payload.get("official_time") if isinstance(payload, dict) else None
        if isinstance(official, (int, float)):
            self._official_time = float(official)
        if self._context is None:
            return
        await self._publish(EVENT_STATE, {"provider": "CTRADER", "state": "STREAMS",
                                          "streams": self._tracker.view(self._freshness_now()),
                                          "sequence_gaps": self._tracker.gaps,
                                          "out_of_order": self._tracker.out_of_order,
                                          "timestamp": self._official_time})

    def _freshness_now(self) -> float:
        return max(self._tracker.newest_stamp(), self._official_time)

    async def snapshot(self) -> dict[str, Any]:
        return {"version": ATOM_VERSION, "tracker": self._tracker.snapshot(),
                "by_name": dict(self._by_name), "by_id": dict(self._by_id),
                "counts": {"ticks": self._ticks, "depth": self._depth,
                           "heartbeats": self._heartbeats, "unknown": self._unknown,
                           "connects": self._connects, "sequence": self._sequence}}

    async def restore(self, state: dict[str, Any]) -> None:
        if not isinstance(state, dict):
            raise ValueError("INVALID_FIX_FEED_STATE")
        self._tracker.restore(state.get("tracker") or {})
        # v1.3.2 (nq seal 2026-08-25): rows restored under a FOREIGN identity
        # are dropped. After the identity_account_id migration the snapshot
        # still carried ("6175701", symbol) rows whose stamps froze at the
        # switch -- the health view then declared ALL symbols dead/stale
        # (ghost of the old identity) while the live rows under the current
        # account were ACTIVE and ticks flowed (measured live).
        foreign = {pair for pair in self._tracker.symbols
                   if pair[0] != self._account}
        if foreign:
            self._tracker.symbols -= foreign
            for old_account, old_symbol in foreign:
                for kind in ("tick", "depth", "spec"):
                    self._tracker.last.pop(
                        self._tracker.key(old_account, old_symbol, kind), None)
                self._tracker.last_sequence.pop(old_account, None)
                self._tracker.stopped.discard(old_account)
        by_name = state.get("by_name")
        by_id = state.get("by_id")
        self._by_name = {str(k): str(v) for k, v in by_name.items()} if isinstance(by_name, dict) else {}
        self._by_id = {str(k): str(v) for k, v in by_id.items()} if isinstance(by_id, dict) else {}
        counts = state.get("counts") if isinstance(state.get("counts"), dict) else {}
        self._ticks = int(counts.get("ticks") or 0)
        self._depth = int(counts.get("depth") or 0)
        self._heartbeats = int(counts.get("heartbeats") or 0)
        self._unknown = int(counts.get("unknown") or 0)
        self._connects = int(counts.get("connects") or 0)
        self._sequence = int(counts.get("sequence") or 0)

    async def health_check(self) -> HealthStatus:
        if not self._running:
            return HealthStatus(state=HealthState.UNHEALTHY, message="NOT_STARTED")
        streams = self._tracker.view(self._freshness_now())
        ticks = [row for row in streams if row["stream"] == "tick"]
        session = self._session
        details = {"link": self._link, "host": "%s:%d" % (self._host, self._port),
                   "logged_on": bool(session and session.logged_on),
                   "symbols_listed": len(self._by_name), "subscribed": len(self._books),
                   "ticks": self._ticks, "depth": self._depth, "heartbeats": self._heartbeats,
                   "unknown_messages": self._unknown, "connects": self._connects,
                   "rejects": session.rejects if session else 0,
                   "orphan_updates": sum(book.orphan_updates for book in self._books.values()),
                   "last_error": self._last_error, "streams": streams,
                   "stream_link": self._stream.stats() if self._stream else None,
                   "sequence_gaps": self._tracker.gaps,
                   "out_of_order": self._tracker.out_of_order,
                   "skipped_bytes": self._skipped_bytes}
        details["auth_failed"] = self._auth_failed
        # Counters ride on EVERY message, not only the healthy one: a feed that
        # is stale or dead is exactly when the numbers are needed, and hiding
        # them there is what turned a throughput problem into a guessing game.
        tail = " t=%d d=%d c=%d u=%d q=%d p=%d j=%d s=%d" % (
            self._ticks, self._depth, self._connects, self._unknown,
            (self._stream.stats().get("pending_bytes") or 0) if self._stream else -1,
            len(self._buffer), self._jumps, self._skipped_bytes)
        if self._auth_failed:
            return HealthStatus(state=HealthState.UNHEALTHY, details=details,
                                message="FIX_LOGON_REFUSED_NO_RETRY: %s%s" % (self._last_error, tail))
        if self._last_error and self._link == STATE_DOWN:
            return HealthStatus(state=HealthState.UNHEALTHY, message="FIX_DOWN: %s%s" % (self._last_error, tail),
                                details=details)
        if session is None or not session.logged_on:
            return HealthStatus(state=HealthState.DEGRADED, message="FIX_NOT_LOGGED_ON" + tail, details=details)
        if not self._by_name:
            return HealthStatus(state=HealthState.DEGRADED, message="FIX_AWAITING_SECURITY_LIST" + tail,
                                details=details)
        if not ticks or all(row["state"] == NEVER_SEEN for row in ticks):
            return HealthStatus(state=HealthState.UNKNOWN, message="FIX_NEVER_SEEN" + tail, details=details)
        # A symbol outside its trading session is not a broken link.  Only a
        # feed where EVERY symbol is dead is a real outage; a mixed picture is
        # degraded and must name the silent symbols instead of condemning the
        # whole feed.  Measured 2026-08-25: four index/metal symbols went quiet
        # at their daily break while three kept ticking under one second.
        dead_names = sorted({str(row.get("symbol") or "?")
                             for row in ticks if row["state"] == DEAD})
        live_rows = [row for row in ticks if row["state"] not in (DEAD, NEVER_SEEN)]
        if dead_names and not live_rows:
            return HealthStatus(state=HealthState.UNHEALTHY, details=details,
                                message="FIX_TICK_DEAD_ALL: %s%s" % (",".join(dead_names), tail))
        if dead_names:
            return HealthStatus(state=HealthState.DEGRADED, details=details,
                                message="FIX_SYMBOLS_OUT_OF_SESSION: %s%s" % (",".join(dead_names), tail))
        if any(row["state"] == STALE for row in ticks):
            stale_names = sorted({str(row.get("symbol") or "?")
                                  for row in ticks if row["state"] == STALE})
            return HealthStatus(state=HealthState.DEGRADED, details=details,
                                message="FIX_TICK_STALE: %s%s" % (",".join(stale_names), tail))
        return HealthStatus(state=HealthState.HEALTHY, details=details,
                            message="ticks=%d depth=%d symbols=%d%s"
                                    % (self._ticks, self._depth, len(self._books), tail))
