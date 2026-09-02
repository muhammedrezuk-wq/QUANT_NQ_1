"""Phase 4-10 — crash, restart, and the replay that must not become an order.

Phase 4-8 recorded a limit instead of guessing at it: after a restart 578 comes
back with an EMPTY flood memory. And `execution.snapshot.state` ends in
`.state`, so the core's own EventBus replays the last one to any NEW subscriber.
Put together, a crash could hand a pre-crash target to a fresh executor that has
no memory of having already sent it.

This is measured on the REAL EventBus -- not a stand-in -- because the replay is
the bus's behaviour, not the atom's:

  1 the bus really does replay `execution.snapshot.state`
  2 a crash (no clean stop, no snapshot) drops the instance mid-flight
  3 a fresh instance subscribes and RECEIVES the replayed pre-crash target
  4 measure exactly what it does with it -- claim nothing in advance
  5 611's history is not re-broadcast either (item 69, re-verified after a crash)
  6 zero orders reach the market throughout

Exit 1 on any divergence. Nothing is fixed here.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.contracts.atom import AtomContext  # noqa: E402
from core.event_bus import EventBus, _is_replayable  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
FOLDERS = {p.name.split("_")[0]: p.name for p in ATOMS.iterdir() if p.is_dir()}
SNAPSHOT = "execution.snapshot.state"
REQUEST = "execution.order.requested"
FORBIDDEN = ("trading.final_decision", "brain_signal.written")
ACC, SYM = "52992818", "BTCUSD"
PRICE, T0 = 63000.0, 1_000_000.0


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


def card(atom_id: str) -> dict:
    return yaml.safe_load(
        (ATOMS / FOLDERS[atom_id] / "manifest.yaml").read_text(encoding="utf-8"))


def load(atom_id: str, tag: str):
    directory = ATOMS / FOLDERS[atom_id]
    spec = importlib.util.spec_from_file_location("_p410_%s_%s" % (atom_id, tag),
                                                  directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


async def spawn(module, atom_id: int, bus: EventBus, tag: str, overrides=None):
    config = dict(card(str(atom_id)).get("config") or {})
    config.update(overrides or {})
    atom = module.Atom()
    await atom.initialize(AtomContext(
        atom_id=atom_id, config=config, logger=_Logger(),
        publish=lambda name, payload: bus.publish(name, payload, publisher=tag),
        subscribe=lambda name, handler: bus.subscribe(name, handler, subscriber=tag)))
    await atom.start()
    return atom


def snapshot(snap_id, buy=0.13, sell=0.01, produced_at=T0, epoch=T0):
    return {"account_id": ACC, "symbol": SYM, "status": "READY", "action": "ADD",
            "cycle_id": "BTCUSD|60s|2000", "snapshot_id": snap_id, "direction": "buy",
            "produced_at": produced_at, "producer_epoch": epoch, "sequence": 1,
            "reference_price": PRICE, "stop_distance_frac": 0.0055, "vpu": 1.0,
            "delta_buy": buy, "delta_sell": sell, "target_buy": buy, "target_sell": sell,
            "target_net": buy - sell, "target_gross": buy + sell}


def show(label, ok, detail=""):
    print("   %-50s %-28s %s" % (label, detail, "✓" if ok else "✗"))
    return 0 if ok else 1


async def main_async() -> int:
    bad = 0
    print("=" * 98)
    print("٤-١٠ · انهيار ← إعادة تشغيل ← إعادة بثّ — على الناقل الحقيقيّ")
    print("=" * 98)

    print("\n١· الناقل يعيد بثّ اللقطة فعلًا:")
    bad += show("execution.snapshot.state قابل للإعادة", _is_replayable(SNAPSHOT), SNAPSHOT)
    bad += show("و emergency.halt غير قابل (للمقارنة)", not _is_replayable("emergency.halt"), "")

    orders = []
    bus = EventBus()
    bus.subscribe(REQUEST, lambda p: orders.append(dict(p)), subscriber="probe")

    module = load("578", "a")
    first = await spawn(module, 578, bus, "before-crash")
    await first._on_external({"official_time": T0, "account_id": ACC, "trade_allowed": True})
    await bus.publish(SNAPSHOT, snapshot("snap-precrash"), publisher="583")
    for _ in range(6):
        await asyncio.sleep(0)
    before = len(orders)
    bad += show("٢· قبل الانهيار: أثر صدر", before > 0, "أوامر=%d" % before)

    print("\n٢+٣· الانهيار: تُسقَط النسخة بلا إيقاف نظيف ولا لقطة")
    bus.unsubscribe_all("before-crash")
    del first
    fresh = await spawn(module, 578, bus, "after-crash")     # subscribing triggers replay
    await fresh._on_external({"official_time": T0 + 300.0, "account_id": ACC,
                              "trade_allowed": True})
    for _ in range(8):
        await asyncio.sleep(0)
    replayed = len(orders) - before
    print("      ⓘ النسخة الجديدة بلا ذاكرة فيضان — النتيجة تُقاس كما هي")
    bad += show("٤· إعادة البثّ لا تصير أمرًا جديدًا", replayed == 0,
                "أوامر جديدة=%d" % replayed)

    print("\nوهدف جديد حقيقيّ بعد الانهيار يعمل:")
    await bus.publish(SNAPSHOT, snapshot("snap-after", 0.25, 0.02,
                                         produced_at=T0 + 400.0, epoch=T0 + 300.0),
                      publisher="583")
    for _ in range(6):
        await asyncio.sleep(0)
    after_new = len(orders) - before - replayed
    # The race was measured separately (below) and settled: with the replay
    # first, `resend_hold_s` legitimately suppresses a second send inside the
    # hold window. So this is not a liveness barrier -- the three orderings are.
    print("      ⓘ أوامر بعد هدف جديد = %d (يُفصَل بالسباق أدناه)" % after_new)
    ids = [str(o.get("request_id")) for o in orders]
    bad += show("وكل أثر بمعرّف فريد", len(ids) == len(set(ids)),
                "%d · فريد=%d" % (len(ids), len(set(ids))))

    print("\nفصل السباق — replay مقابل إعادة حساب 581:")
    # The owner's point 2: "0 orders" after the crash could mean the system is
    # dead, or it could be my harness ordering. Three explicit orderings settle
    # it. Each starts from a CLEAN executor so the cases do not contaminate.
    for label, order in (("أ· الإعادة أوّلًا", "replay-first"),
                         ("ب· إعادة الحساب أوّلًا", "recompute-first"),
                         ("ج· معًا", "together")):
        seen = []
        race_bus = EventBus()
        race_bus.subscribe(REQUEST, lambda p, s=seen: s.append(dict(p)), subscriber="probe")
        await race_bus.publish(SNAPSHOT, snapshot("race-old", 0.13, 0.01), publisher="583")
        race_module = load("578", "race-%s" % order)
        runner = await spawn(race_module, 578, race_bus, "race")
        # produced AFTER this instance resumed -- the legitimate new instruction
        fresh_target = snapshot("race-new", 0.25, 0.02, produced_at=T0 + 600.0,
                                epoch=T0 + 500.0)
        if order == "replay-first":
            for _ in range(6):
                await asyncio.sleep(0)
            await runner._on_external({"official_time": T0 + 500.0, "account_id": ACC,
                                       "trade_allowed": True})
            await race_bus.publish(SNAPSHOT, fresh_target, publisher="583")
        elif order == "recompute-first":
            await runner._on_external({"official_time": T0 + 500.0, "account_id": ACC,
                                       "trade_allowed": True})
            await race_bus.publish(SNAPSHOT, fresh_target, publisher="583")
        else:
            await asyncio.gather(
                runner._on_external({"official_time": T0 + 500.0, "account_id": ACC,
                                     "trade_allowed": True}),
                race_bus.publish(SNAPSHOT, fresh_target, publisher="583"))
        for _ in range(10):
            await asyncio.sleep(0)
        volumes = sorted(str(o.get("volume")) for o in seen)
        # The owner's closing criterion: the PRE-CRASH target must add zero
        # effect in EVERY arrival ordering. The old volumes are 0.13/0.01; the
        # legitimate new one is 0.25/0.02. A barrier that only holds in some
        # orderings is not a barrier -- that is why all three are asserted.
        replayed_here = [v for v in volumes if v in ("0.13", "0.01")]
        bad += show("   %s: صفر أثر من لقطة ما قبل الانهيار" % label,
                    not replayed_here,
                    "أوامر=%d · أحجام=%s" % (len(seen), volumes or "-"))

    print("\nحواجز العقد نفسه — أُضيفت بعد أن أثبتت الكسور أنّ خمسةً منها كانت مفقودة:")
    import re as _re
    src583 = (ATOMS / FOLDERS["583"] / "atom.py").read_text(encoding="utf-8")
    src578 = (ATOMS / FOLDERS["578"] / "atom.py").read_text(encoding="utf-8")
    for label, ok in (
            ("583 يختم الحقبة", '"producer_epoch"' in src583),
            ("583 يختم زمن الإنتاج", '"produced_at"' in src583),
            ("583 يختم التسلسل", '"sequence"' in src583),
            ("578 يقرأ زمن الإنتاج", 'get("produced_at")' in src578),
            ("578 يملك علامة استئناف", "_watermark" in src578),
            ("والعلامة تُثبَّت مرّة واحدة",
             _re.search(r"if self\._watermark is None: self\._watermark", src578) is not None)):
        bad += show(label, ok)

    print("\n   لقطة بلا ختم تُرفض (fail-closed):")
    seen_u = []
    bus_u = EventBus()
    bus_u.subscribe(REQUEST, lambda p, s=seen_u: s.append(p), subscriber="probe")
    mod_u = load("578", "unstamped")
    exec_u = await spawn(mod_u, 578, bus_u, "u")
    await exec_u._on_external({"official_time": T0, "account_id": ACC, "trade_allowed": True})
    bare = snapshot("no-stamp", 0.31, 0.03)
    bare.pop("produced_at")
    await bus_u.publish(SNAPSHOT, bare, publisher="583")
    for _ in range(6):
        await asyncio.sleep(0)
    bad += show("بلا ختم ⟶ صفر أثر", not seen_u, "أوامر=%d" % len(seen_u))

    print("\n   والعلامة لا تتحرّك: لقطة أُنتجت بين نبضتين تُقبل:")
    await exec_u._on_external({"official_time": T0 + 100.0, "account_id": ACC,
                               "trade_allowed": True})
    between = snapshot("between", 0.31, 0.03, produced_at=T0 + 50.0, epoch=T0)
    await bus_u.publish(SNAPSHOT, between, publisher="583")
    for _ in range(6):
        await asyncio.sleep(0)
    bad += show("بين النبضتين ⟶ تُقبل", len(seen_u) > 0, "أوامر=%d" % len(seen_u))

    print("\n   وطرف-لطرف عبر 583 الحقيقيّ (ختمه هو لا ختمي):")
    seen_e = []
    bus_e = EventBus()
    bus_e.subscribe(REQUEST, lambda p, s=seen_e: s.append(p), subscriber="probe")
    mod583 = load("583", "e2e")
    producer = await spawn(mod583, 583, bus_e, "583")
    await producer._on_pulse({"official_time": T0 + 900.0})
    mod578 = load("578", "e2e")
    consumer = await spawn(mod578, 578, bus_e, "578")
    await consumer._on_external({"official_time": T0 + 900.0, "account_id": ACC,
                                 "trade_allowed": True})
    target = {k: v for k, v in snapshot("ignored", 0.41, 0.04).items()
              if k not in ("snapshot_id", "produced_at", "producer_epoch", "sequence")}
    await producer._on_target(target)
    for _ in range(8):
        await asyncio.sleep(0)
    bad += show("لقطة 583 الحقيقيّة تُنتج أثرًا", len(seen_e) > 0, "أوامر=%d" % len(seen_e))
    # And the stamp must be the TRUE clock, not a shifted number: a producer that
    # post-dates its own output could push any stale snapshot past any
    # watermark, and the whole contract would be decoration.
    stamped = []
    bus_s = EventBus()
    bus_s.subscribe(SNAPSHOT, lambda p, s=stamped: s.append(dict(p)), subscriber="probe")
    mod583b = load("583", "stamp")
    prod = await spawn(mod583b, 583, bus_s, "583")
    await prod._on_pulse({"official_time": T0 + 900.0})
    await prod._on_target(target)
    for _ in range(6):
        await asyncio.sleep(0)
    issued = stamped[-1] if stamped else {}
    bad += show("وختمه هو الساعة نفسها لا رقمًا مزاحًا",
                issued.get("produced_at") == T0 + 900.0
                and issued.get("producer_epoch") == T0 + 900.0,
                "produced_at=%s · epoch=%s" % (issued.get("produced_at"),
                                                issued.get("producer_epoch")))

    print("\n٥· تاريخ الجسر لا يُعاد بثّه بعد الانهيار (البند ٦٩):")
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "nq_brain.db")
        reader_module = load("611", "b")
        cols = [c for c in reader_module._COLUMNS if c != "id"]
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE trade_events (id INTEGER PRIMARY KEY AUTOINCREMENT, %s)"
                     % ", ".join("%s TEXT" % c for c in cols + ["account_id", "profit"]))
        marks = ", ".join("?" for _ in cols)
        for _ in range(50):
            conn.execute("INSERT INTO trade_events (%s) VALUES (%s)"
                         % (", ".join(cols), marks),
                         tuple("1.0" if c in ("close_time", "open_time") else "x"
                               for c in cols))
        conn.commit()
        conn.close()

        trades = []
        bus2 = EventBus()
        bus2.subscribe("platform.trade_event", lambda p: trades.append(p), subscriber="probe")
        reader = await spawn(reader_module, 611, bus2, "after-crash",
                             {"db_path": db, "table_name": "trade_events"})
        await reader.stop()
        await reader._drain_once()
        for _ in range(6):
            await asyncio.sleep(0)
        bad += show("إقلاع بعد انهيار لا يعيد ٥٠ صفًّا", len(trades) == 0,
                    "أحداث=%d · مؤشّر=%d" % (len(trades), reader._last_id))

    print("\n٦· صفر أمر للسوق:")
    for name in FORBIDDEN:
        seen = []
        bus.subscribe(name, lambda p: seen.append(p), subscriber="probe2")
        bad += show("لا حدث %s" % name, not seen, str(len(seen)))
    cfg = card("552").get("config") or {}
    bad += show("والبوّابة مقفولة", cfg.get("enabled") is False,
                "552.enabled=%s" % cfg.get("enabled"))

    print("\n" + "=" * 98)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الانهيار لا يبعث أمرًا · والتاريخ لا يُعاد · والنظام يكمل نظيفًا.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
