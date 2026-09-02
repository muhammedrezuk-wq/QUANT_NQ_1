import asyncio
import importlib.util
import sys
import threading
from pathlib import Path

root = Path(__file__).resolve().parents[3]
folder = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(folder))
spec = importlib.util.spec_from_file_location("a622", folder / "atom.py")
module = importlib.util.module_from_spec(spec)
sys.modules["a622"] = module
assert spec.loader is not None
spec.loader.exec_module(module)

from shared import fix_session as fx  # noqa: E402

PASSWORD = "TOP-SECRET-VALUE"


class Logger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class Bus:
    def __init__(self):
        self.events = []

    def subscribe(self, *args):
        pass

    async def publish(self, name, payload):
        self.events.append((name, payload))

    def of(self, name):
        return [payload for event, payload in self.events if event == name]


class FakeStream:
    """Stands in for transport.StreamSession: same surface, no socket."""

    def __init__(self, *args, **kwargs):
        self.sent = []
        self.inbox = b""
        self.connected = True
        self.error = ""

    def connect(self):
        self.connected = True

    def send(self, data):
        self.sent.append(data)

    def drain(self):
        data, self.inbox = self.inbox, b""
        return data

    def close(self):
        self.connected = False

    def stats(self):
        return {"connected": self.connected}

    def feed(self, raw):
        self.inbox += raw


def config(**over):
    base = {"host": "h", "port": 5211, "use_tls": True,
            "sender_comp_id": "live4.icmarkets.6175701", "target_comp_id": "cServer",
            "sub_id": "QUOTE", "username": "6175701", "broker": "Raw Trading Ltd",
            "password_secret_key": "ctrader_fix_password",
            "symbols": ["BTCUSD", "XAUUSD"], "market_depth": 0, "heartbeat_s": 30,
            "poll_interval_s": 0.001, "reconnect_backoff_s": 1, "live_max_backlog_bytes": 131072,
            "tick_stale_after_s": 30, "depth_stale_after_s": 30,
            "specs_stale_after_s": 600, "dead_after_s": 120}
    base.update(over)
    return base


def run(coro):
    """A private loop per call.

    The fleet runs thousands of tests in one process and some of them close the
    global loop behind them; a shared `get_event_loop()` here passed in
    isolation and failed 14/14 the moment the file ran with the fleet.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def build(monkey_stream=True, **over):
    bus = Bus()
    atom = module.Atom()
    ctx = module.AtomContext(atom_id=622, config=config(**over), logger=Logger(),
                             publish=bus.publish, subscribe=bus.subscribe)
    run(atom.initialize(ctx))
    atom._password = lambda: PASSWORD
    stream = FakeStream()
    if monkey_stream:
        atom._stream = stream
        atom._session = fx.FixSession("live4.icmarkets.6175701", "cServer", "QUOTE", "6175701")
    return atom, bus, stream


def venue(msg_type, fields):
    return fx.encode([(fx.TAG_MSG_TYPE, msg_type), (fx.TAG_SENDER, "cServer"),
                      (fx.TAG_SEQ, 1), (fx.TAG_SENDING_TIME, "20260821-15:30:00.500")] + fields)


def logon_and_list(atom, stream):
    stream.feed(venue("A", []))
    run(atom._pump())
    stream.feed(venue("y", [(fx.TAG_SEC_REQ_ID, "R"), (fx.TAG_NO_RELATED_SYM, 2),
                            (fx.TAG_SYMBOL, "1"), (fx.TAG_SYMBOL_NAME, "BTCUSD"),
                            (fx.TAG_SYMBOL, "2"), (fx.TAG_SYMBOL_NAME, "XAUUSD")]))
    run(atom._pump())


SNAPSHOT = [(fx.TAG_SYMBOL, "1"), (fx.TAG_NO_MD_ENTRIES, 4),
            (fx.TAG_MD_ENTRY_TYPE, "0"), (fx.TAG_MD_ENTRY_ID, "b1"),
            (fx.TAG_MD_ENTRY_PX, "77600.5"), (fx.TAG_MD_ENTRY_SIZE, "3"),
            (fx.TAG_MD_ENTRY_TYPE, "0"), (fx.TAG_MD_ENTRY_ID, "b2"),
            (fx.TAG_MD_ENTRY_PX, "77599.0"), (fx.TAG_MD_ENTRY_SIZE, "8"),
            (fx.TAG_MD_ENTRY_TYPE, "1"), (fx.TAG_MD_ENTRY_ID, "a1"),
            (fx.TAG_MD_ENTRY_PX, "77606.0"), (fx.TAG_MD_ENTRY_SIZE, "2"),
            (fx.TAG_MD_ENTRY_TYPE, "1"), (fx.TAG_MD_ENTRY_ID, "a2"),
            (fx.TAG_MD_ENTRY_PX, "77610.0"), (fx.TAG_MD_ENTRY_SIZE, "5")]


def test_logon_is_sent_and_password_never_published():
    atom, bus, stream = build()
    stream.feed(venue("A", []))
    run(atom._pump())
    assert atom._session.logged_on is True
    assert any(b"35=x" in raw for raw in stream.sent), "security list requested after logon"
    for _, payload in bus.events:
        assert PASSWORD not in repr(payload)


def test_security_list_builds_map_and_subscribes():
    atom, bus, stream = build()
    logon_and_list(atom, stream)
    assert atom._by_name == {"BTCUSD": "1", "XAUUSD": "2"}
    requests = [raw for raw in stream.sent if b"35=V" in raw]
    assert len(requests) == 2
    ready = [p for p in bus.of("feed.ctrader.state") if p.get("state") == "READY"]
    assert ready and ready[-1]["subscribed"] == ["BTCUSD", "XAUUSD"]
    assert ready[-1]["missing"] == []


def test_missing_symbol_is_declared_not_invented():
    atom, bus, stream = build(symbols=["BTCUSD", "DOESNOTEXIST"])
    logon_and_list(atom, stream)
    ready = [p for p in bus.of("feed.ctrader.state") if p.get("state") == "READY"][-1]
    assert ready["missing"] == ["DOESNOTEXIST"]
    assert ready["subscribed"] == ["BTCUSD"]


def test_snapshot_yields_tick_and_depth_with_venue_time():
    atom, bus, stream = build()
    logon_and_list(atom, stream)
    stream.feed(venue("W", SNAPSHOT))
    run(atom._pump())
    tick = bus.of("feed.ctrader.tick")[-1]
    depth = bus.of("market.depth")[-1]
    assert tick["symbol"] == "BTCUSD" and tick["provider"] == "CTRADER"
    assert tick["bid"] == 77600.5 and tick["ask"] == 77606.0
    assert tick["price"] == (77600.5 + 77606.0) / 2.0
    assert tick["account_id"] == "6175701" and tick["broker"] == "Raw Trading Ltd"
    # الوقت من رسالة المصدر لا من ساعة الجهاز
    assert abs(tick["timestamp"] - fx.parse_sending_time("20260821-15:30:00.500")) < 1e-6
    assert [row["price"] for row in depth["bids"]] == [77600.5, 77599.0]
    assert [row["price"] for row in depth["asks"]] == [77606.0, 77610.0]


def test_incremental_change_and_delete_move_the_top():
    atom, bus, stream = build()
    logon_and_list(atom, stream)
    stream.feed(venue("W", SNAPSHOT))
    run(atom._pump())
    stream.feed(venue("X", [(fx.TAG_NO_MD_ENTRIES, 2),
                            (fx.TAG_MD_ENTRY_TYPE, "0"), (fx.TAG_MD_UPDATE_ACTION, "1"),
                            (fx.TAG_MD_ENTRY_ID, "b1"), (fx.TAG_SYMBOL, "1"),
                            (fx.TAG_MD_ENTRY_PX, "77604.0"), (fx.TAG_MD_ENTRY_SIZE, "4"),
                            (fx.TAG_MD_ENTRY_TYPE, "1"), (fx.TAG_MD_UPDATE_ACTION, "2"),
                            (fx.TAG_MD_ENTRY_ID, "a1"), (fx.TAG_SYMBOL, "1")]))
    run(atom._pump())
    tick = bus.of("feed.ctrader.tick")[-1]
    assert tick["bid"] == 77604.0, "change applied"
    assert tick["ask"] == 77610.0, "delete removed the old best ask"


def test_every_update_publishes_its_own_tick():
    atom, bus, stream = build()
    logon_and_list(atom, stream)
    stream.feed(venue("W", SNAPSHOT))
    run(atom._pump())
    before = len(bus.of("feed.ctrader.tick"))
    for price in ("77601.0", "77602.0", "77603.0"):
        stream.feed(venue("X", [(fx.TAG_NO_MD_ENTRIES, 1),
                                (fx.TAG_MD_ENTRY_TYPE, "0"), (fx.TAG_MD_UPDATE_ACTION, "1"),
                                (fx.TAG_MD_ENTRY_ID, "b1"), (fx.TAG_SYMBOL, "1"),
                                (fx.TAG_MD_ENTRY_PX, price), (fx.TAG_MD_ENTRY_SIZE, "1")]))
        run(atom._pump())
    ticks = bus.of("feed.ctrader.tick")
    assert len(ticks) - before == 3, "tick by tick: no batching, no conflation"
    assert [t["bid"] for t in ticks[-3:]] == [77601.0, 77602.0, 77603.0]


def test_group_delimiter_is_read_from_the_wire_not_assumed():
    """عيّنة حرفيّة من cServer الحيّ ٢٠٢٦-٠٨-٢١: المجموعة تفتح بـ279 لا بـ269.

    افتراض 269 كان يزيح فعل التحديث صفًّا كاملًا، فيقع الحذف على السعر الخطأ.
    """
    atom, bus, stream = build()
    logon_and_list(atom, stream)
    stream.feed(venue("X", [(fx.TAG_MD_REQ_ID, "MD-1"), (fx.TAG_NO_MD_ENTRIES, 2),
                            (fx.TAG_MD_UPDATE_ACTION, "0"), (fx.TAG_MD_ENTRY_TYPE, "1"),
                            (fx.TAG_MD_ENTRY_ID, "a9"), (fx.TAG_SYMBOL, "1"),
                            (fx.TAG_MD_ENTRY_PX, "77580"), (fx.TAG_MD_ENTRY_SIZE, "50"),
                            (fx.TAG_MD_UPDATE_ACTION, "0"), (fx.TAG_MD_ENTRY_TYPE, "0"),
                            (fx.TAG_MD_ENTRY_ID, "b9"), (fx.TAG_SYMBOL, "1"),
                            (fx.TAG_MD_ENTRY_PX, "77570"), (fx.TAG_MD_ENTRY_SIZE, "20")]))
    run(atom._pump())
    tick = bus.of("feed.ctrader.tick")[-1]
    assert tick["bid"] == 77570.0 and tick["ask"] == 77580.0
    stream.feed(venue("X", [(fx.TAG_MD_REQ_ID, "MD-1"), (fx.TAG_NO_MD_ENTRIES, 1),
                            (fx.TAG_MD_UPDATE_ACTION, "2"), (fx.TAG_MD_ENTRY_TYPE, "1"),
                            (fx.TAG_MD_ENTRY_ID, "a9"), (fx.TAG_SYMBOL, "1")]))
    run(atom._pump())
    depth = bus.of("market.depth")[-1]
    assert [row["price"] for row in depth["asks"]] == [], "الحذف أصاب المستوى المقصود"


def test_delete_without_a_side_still_removes_the_level():
    """مقيس على cServer الحيّ: الحذف يأتي بـ279 و278 فقط — بلا 269.

    تخطّي أي صفّ بلا نوع كان يُسقط كل حذف، فتضخّم الدفتر إلى 1595 مستوى
    وتجمّد أفضل سعر عند قيمة واحدة.
    """
    atom, bus, stream = build()
    logon_and_list(atom, stream)
    stream.feed(venue("W", SNAPSHOT))
    run(atom._pump())
    assert len(bus.of("market.depth")[-1]["asks"]) == 2
    stream.feed(venue("X", [(fx.TAG_MD_REQ_ID, "MD-1"), (fx.TAG_NO_MD_ENTRIES, 2),
                            (fx.TAG_MD_UPDATE_ACTION, "2"), (fx.TAG_MD_ENTRY_ID, "a1"),
                            (fx.TAG_MD_UPDATE_ACTION, "2"), (fx.TAG_MD_ENTRY_ID, "b1")]))
    run(atom._pump())
    depth = bus.of("market.depth")[-1]
    assert [r["price"] for r in depth["asks"]] == [77610.0], "حُذف a1 من جهة البيع"
    assert [r["price"] for r in depth["bids"]] == [77599.0], "وحُذف b1 من جهة الشراء"
    assert atom._books["BTCUSD"].orphan_updates == 0, "لا يتيم: كلا المعرّفين كان موجودًا"


def test_delete_of_an_unseen_level_is_counted_not_silent():
    atom, bus, stream = build()
    logon_and_list(atom, stream)
    stream.feed(venue("W", SNAPSHOT))
    run(atom._pump())
    stream.feed(venue("X", [(fx.TAG_MD_REQ_ID, "MD-1"), (fx.TAG_NO_MD_ENTRIES, 1),
                            (fx.TAG_MD_UPDATE_ACTION, "2"), (fx.TAG_MD_ENTRY_ID, "ghost")]))
    run(atom._pump())
    assert atom._books["BTCUSD"].orphan_updates == 1


def test_backlog_jumps_to_the_tail_and_declares_the_gap():
    """قانون التغذية: الخطّ الحيّ لا يحمل خلفية — قفزة وإعلان، لا تراكم."""
    atom, bus, stream = build(live_max_backlog_bytes=2048)
    logon_and_list(atom, stream)
    stream.feed(venue("W", SNAPSHOT))
    run(atom._pump())
    assert atom._books["BTCUSD"].bids, "الدفتر مبنيّ قبل القفزة"
    stream.feed(venue("X", [(fx.TAG_MD_REQ_ID, "MD-1"), (fx.TAG_NO_MD_ENTRIES, 1),
                            (fx.TAG_MD_UPDATE_ACTION, "0"), (fx.TAG_MD_ENTRY_TYPE, "0"),
                            (fx.TAG_MD_ENTRY_ID, "z"), (fx.TAG_SYMBOL, "1"),
                            (fx.TAG_MD_ENTRY_PX, "1"), (fx.TAG_MD_ENTRY_SIZE, "1")]) * 40)
    run(atom._pump())
    gap = [p for p in bus.of("feed.ctrader.state") if p.get("gap") == "LIVE_LINE_JUMPED"]
    assert gap, "الفجوة تُعلَن ولا تُبتلع"
    assert gap[-1]["skipped_bytes"] > 0
    assert atom._jumps == 1
    # الدفتر يُمسح عند القفزة ثم يُعاد بناؤه ممّا نجا: مستويات ما قبلها لا تعود،
    # لأنّ دفترًا مبنيًّا فوق تحديثات مقفوزة دفتر يكذب.
    bids = atom._books["BTCUSD"].bids
    assert "b1" not in bids and "b2" not in bids, "مستويات ما قبل القفزة زالت"
    assert set(bids) == {"z"}, "ولم يبقَ إلّا ما وصل بعدها"


def test_no_jump_while_the_line_keeps_up():
    atom, bus, stream = build()
    logon_and_list(atom, stream)
    stream.feed(venue("W", SNAPSHOT))
    run(atom._pump())
    assert atom._jumps == 0 and atom._skipped_bytes == 0


def test_unknown_symbol_id_is_counted_not_guessed():
    atom, bus, stream = build()
    logon_and_list(atom, stream)
    stream.feed(venue("W", [(fx.TAG_SYMBOL, "999"), (fx.TAG_NO_MD_ENTRIES, 1),
                            (fx.TAG_MD_ENTRY_TYPE, "0"), (fx.TAG_MD_ENTRY_ID, "x"),
                            (fx.TAG_MD_ENTRY_PX, "1.0"), (fx.TAG_MD_ENTRY_SIZE, "1")]))
    run(atom._pump())
    assert bus.of("feed.ctrader.tick") == []
    assert atom._unknown == 1


def test_crossed_or_empty_book_publishes_no_tick():
    atom, bus, stream = build()
    logon_and_list(atom, stream)
    stream.feed(venue("W", [(fx.TAG_SYMBOL, "1"), (fx.TAG_NO_MD_ENTRIES, 1),
                            (fx.TAG_MD_ENTRY_TYPE, "0"), (fx.TAG_MD_ENTRY_ID, "b1"),
                            (fx.TAG_MD_ENTRY_PX, "100.0"), (fx.TAG_MD_ENTRY_SIZE, "1")]))
    run(atom._pump())
    assert bus.of("feed.ctrader.tick") == [], "one-sided book is not a price"
    assert bus.of("market.depth"), "the half book is still reported honestly"


def test_test_request_is_answered():
    atom, bus, stream = build()
    stream.feed(venue("A", []))
    run(atom._pump())
    stream.feed(venue("1", [(fx.TAG_TEST_REQ_ID, "PING-7")]))
    run(atom._pump())
    assert any(b"112=PING-7" in raw for raw in stream.sent)


def test_reject_is_named_and_link_declared_down():
    atom, bus, stream = build()
    stream.feed(venue("A", []))
    run(atom._pump())
    stream.feed(venue("3", [(fx.TAG_TEXT, "Invalid MDReqID")]))
    run(atom._pump())
    assert atom._session.rejects == 1
    assert "Invalid MDReqID" in atom._last_error
    assert atom._link == module.STATE_DOWN


def test_refused_logon_stops_retrying_instead_of_hammering_the_account():
    atom, bus, stream = build()
    stream.feed(venue("5", [(fx.TAG_TEXT, "RET_NOT_AUTHORIZED")]))
    try:
        run(atom._pump())
    except Exception:  # noqa: BLE001 — الخروج يهدم الوصلة عمدًا
        pass
    assert atom._auth_failed is True, "a refused logon must not look like a flaky link"
    atom._running = True
    health = run(atom.health_check())
    assert "FIX_LOGON_REFUSED" in health.message
    assert "RET_NOT_AUTHORIZED" in health.message
    state = [p for p in bus.of("feed.ctrader.state") if p.get("state") == module.STATE_DOWN][-1]
    assert state["auth_failed"] is True


def test_logout_after_a_good_session_is_a_link_drop_not_an_auth_failure():
    atom, bus, stream = build()
    stream.feed(venue("A", []))
    run(atom._pump())
    stream.feed(venue("5", [(fx.TAG_TEXT, "session end")]))
    raised = ""
    try:
        run(atom._pump())
    except Exception as exc:  # noqa: BLE001
        raised = str(exc)
    assert atom._auth_failed is False, "a normal logout must still allow reconnect"
    assert "LOGOUT" in raised, "الخروج يهدم الوصلة فورًا بدل تركها مفتوحة بلا جلسة"


def test_health_walks_from_not_started_to_healthy():
    atom, bus, stream = build()
    assert run(atom.health_check()).state.name == "UNHEALTHY"
    atom._running = True
    assert run(atom.health_check()).message.startswith("FIX_NOT_LOGGED_ON")
    stream.feed(venue("A", []))
    run(atom._pump())
    assert run(atom.health_check()).message.startswith("FIX_AWAITING_SECURITY_LIST")
    stream.feed(venue("y", [(fx.TAG_NO_RELATED_SYM, 1), (fx.TAG_SYMBOL, "1"),
                            (fx.TAG_SYMBOL_NAME, "BTCUSD")]))
    run(atom._pump())
    assert run(atom.health_check()).state.name == "UNKNOWN"
    stream.feed(venue("W", SNAPSHOT))
    run(atom._pump())
    atom._official_time = fx.parse_sending_time("20260821-15:30:00.500")
    health = run(atom.health_check())
    assert health.state.name == "HEALTHY" and "ticks=" in health.message


def test_snapshot_restore_keeps_symbol_map_and_counters():
    atom, bus, stream = build()
    logon_and_list(atom, stream)
    stream.feed(venue("W", SNAPSHOT))
    run(atom._pump())
    state = run(atom.snapshot())
    fresh = module.Atom()
    run(fresh.restore(state))
    assert fresh._by_name == {"BTCUSD": "1", "XAUUSD": "2"}
    assert fresh._ticks == atom._ticks and fresh._depth == atom._depth


def test_restore_rejects_a_broken_state():
    atom, _, _ = build()
    try:
        run(atom.restore("not-a-dict"))
    except ValueError as exc:
        assert "INVALID_FIX_FEED_STATE" in str(exc)
    else:
        raise AssertionError("a corrupt snapshot must not be accepted silently")


def test_link_loss_raises_so_the_caller_reconnects():
    atom, _, stream = build()
    stream.feed(venue("A", []))
    run(atom._pump())
    stream.connected = False
    stream.error = "PEER_CLOSED"
    try:
        run(atom._pump())
    except Exception as exc:  # noqa: BLE001
        assert "PEER_CLOSED" in str(exc)
    else:
        raise AssertionError("a dead link must not look alive")


def test_connect_runs_off_the_event_loop_thread():
    """Item 20/27 of the 27-atom review ("a synchronous TLS connect
    freezes the loop, no connection timeout"): _connect() used to call
    stream.connect() (TCP + TLS handshake, up to several seconds) DIRECTLY
    on the atom's own coroutine -- the shared core event loop. Any
    slowness there stalls every other atom on the same loop. Must now run
    via asyncio.to_thread, i.e. never on the thread that's running the
    event loop."""
    main_thread = threading.current_thread()
    seen = {}

    class RecordingStream:
        def __init__(self, *a, **k): pass
        def connect(self): seen["thread"] = threading.current_thread()
        def send(self, data): pass
        def close(self): pass

    atom, _bus, _stream = build(monkey_stream=False)
    original = module.transport.StreamSession
    module.transport.StreamSession = RecordingStream
    try:
        run(atom._connect())
    finally:
        module.transport.StreamSession = original
    assert seen.get("thread") is not None, "connect() لم يُستدعَ إطلاقاً"
    assert seen["thread"] is not main_thread, (
        "connect() نُفِّذ على خيط الحلقة الرئيسي -- سيجمّد كل الذرّات الأخرى")


def test_teardown_close_runs_off_the_event_loop_thread():
    """Item 20/27: _teardown() (called as _connect()'s own first line, so
    it runs before every reconnect) called stream.close() directly too --
    close() can block for up to the connect timeout waiting for the
    background read-thread to join. Must also run via asyncio.to_thread."""
    main_thread = threading.current_thread()
    seen = {}

    class RecordingStream:
        def close(self): seen["thread"] = threading.current_thread()

    atom, _bus, _stream = build(monkey_stream=True)
    atom._stream = RecordingStream()
    run(atom._teardown())
    assert seen.get("thread") is not None, "close() لم يُستدعَ إطلاقاً"
    assert seen["thread"] is not main_thread, (
        "close() نُفِّذ على خيط الحلقة الرئيسي -- سيجمّد كل الذرّات الأخرى")
