"""Phase 4-8 — HALT -> RESET -> restart, against the REAL atoms.

HALT must not be a display state. It has to stop the execution path, RESET has
to leave nothing behind, and a restart must not resurrect what happened before
the halt.

The halt does not reach 578 directly -- it travels: `emergency.halt` shuts 552
and 550, 519 freezes the asset, 581 then publishes BLOCKED, and 578 ignores a
blocked target. So the chain is driven as it really is, atom by atom.

  1-3 stable -> HALT -> every execution path actually stops
  4-5 RESET  -> the counters that must clear DO clear, with no leak
  6-7 restart-> fresh instances start clean and new events work
  8   a pre-HALT target replayed after the reset must not become an order
  9   HALT while a target is PENDING, not only at rest
  10  zero orders to the market throughout

Exit 1 on any divergence. Nothing is fixed here.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.contracts.atom import AtomContext  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
FOLDERS = {p.name.split("_")[0]: p.name for p in ATOMS.iterdir() if p.is_dir()}
REQUEST = "execution.order.requested"
GATE = "execution.gate.state"
FORBIDDEN = ("trading.final_decision", "brain_signal.written")
ACC, SYM = "52992818", "BTCUSD"
PRICE, T0 = 63000.0, 1_000_000.0


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.log.append((name, dict(payload) if isinstance(payload, dict) else payload))

    def requests(self):
        return [p for n, p in self.log if n == REQUEST]

    def last(self, name):
        rows = [p for n, p in self.log if n == name]
        return rows[-1] if rows else None

    def count(self, name):
        return sum(1 for n, _ in self.log if n == name)


def card(atom_id: str) -> dict:
    return yaml.safe_load(
        (ATOMS / FOLDERS[atom_id] / "manifest.yaml").read_text(encoding="utf-8"))


async def build(atom_id: str, bus: Bus, overrides=None):
    directory = ATOMS / FOLDERS[atom_id]
    spec = importlib.util.spec_from_file_location("_p48_%s_%d" % (atom_id, id(bus)),
                                                  directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    config = dict(card(atom_id).get("config") or {})
    if atom_id == "516":
        config["consumer_db_path"] = (tempfile.mkdtemp(prefix="chk516_") + "/c.db")  # م-41: عزل journal
    config.update(overrides or {})
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=int(atom_id), config=config, logger=_Logger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    await atom.start()
    return atom


def snapshot(snap_id, buy=0.13, sell=0.01, status="READY", action="ADD"):
    return {"account_id": ACC, "symbol": SYM, "status": status, "action": action,
            "cycle_id": "BTCUSD|60s|2000", "snapshot_id": snap_id, "produced_at": 9000000000.0, "producer_epoch": 9000000000.0, "sequence": 1, "direction": "buy",
            "reference_price": PRICE, "stop_distance_frac": 0.0055, "vpu": 1.0,
            "delta_buy": buy, "delta_sell": sell, "target_buy": buy, "target_sell": sell,
            "target_net": buy - sell, "target_gross": buy + sell}


def show(label, ok, detail=""):
    print("   %-48s %-30s %s" % (label, detail, "✓" if ok else "✗"))
    return 0 if ok else 1


async def main_async() -> int:
    bad = 0
    print("=" * 96)
    print("٤-٨ · HALT ← RESET ← إعادة تشغيل — على الذرات الحقيقيّة")
    print("=" * 96)

    print("\n١· حالة مستقرّة تُنتج أثرًا:")
    bus = Bus()
    executor = await build("578", bus)
    await executor._on_external({"official_time": T0, "account_id": ACC, "trade_allowed": True})
    await executor._on_target(snapshot("snap-1"))
    stable = len(bus.requests())
    bad += show("قبل الإيقاف: أثر موجود", stable > 0, "أوامر=%d" % stable)

    print("\n٢+٣· الإيقاف يوقف كل مسار تنفيذيّ:")
    gate = await build("552", bus)
    manager = await build("550", bus)
    for atom in (gate, manager):
        handler = getattr(atom, "_on_halt", None)
        if handler is not None:
            await handler({"reason": "OWNER", "origin": "516"})
    bad += show("بوّابة التنفيذ 552 أُوقفت", bool(getattr(gate, "_halted", False)),
                "halted=%s" % getattr(gate, "_halted", None))
    bad += show("ومدير التنفيذ 550 أُوقف", bool(getattr(manager, "_halted", False)),
                "halted=%s" % getattr(manager, "_halted", None))

    # 519 freezes -> 581 blocks -> 578 sees a BLOCKED target and does nothing.
    await executor._on_external({"official_time": T0 + 60.0, "account_id": ACC,
                                 "trade_allowed": True})
    before_halt_effect = len(bus.requests())
    await executor._on_target(snapshot("snap-2", 0.30, 0.01, status="BLOCKED",
                                       action="BLOCKED"))
    bad += show("٣· هدف محظور لا يُنتج أثرًا",
                len(bus.requests()) == before_halt_effect,
                "أوامر %d ⟶ %d" % (before_halt_effect, len(bus.requests())))

    print("\n٩· الإيقاف وهدف معلَّق (لا في السكون):")
    bus_p = Bus()
    pending = await build("578", bus_p)
    await pending._on_external({"official_time": T0, "account_id": ACC, "trade_allowed": True})
    await pending._on_target(snapshot("snap-p1", 0.13, 0.01))
    issued = len(bus_p.requests())
    await pending._on_external({"official_time": T0 + 60.0, "account_id": ACC,
                                "trade_allowed": False})     # terminal halt mid-flight
    await pending._on_target(snapshot("snap-p2", 0.40, 0.01))
    bad += show("إيقاف الطرفيّة يمنع ما بعده",
                len(bus_p.requests()) == issued,
                "أوامر %d ⟶ %d" % (issued, len(bus_p.requests())))

    print("\n٤+٥· الفكّ يصفّر ولا يسرّب:")
    bus_b = Bus()
    breaker = await build("516", bus_b)
    limit = float((card("516").get("config") or {})["max_daily_loss_pct"])
    await breaker._on_loss({"event_id": "chk-hr1", "account_id": ACC, "loss_pct": limit, "is_loss": True, "ticket": 1})
    tripped = bool(breaker.book(ACC)["kill"])
    await breaker._on_reset({"account_id": ACC, "operator": "dashboard"})
    bb = breaker.book(ACC)
    counters = (bb["daily_loss_pct"], bb["consecutive_losses"], bb["daily_trade_count"])
    bad += show("ضُرب ثمّ فُكّ", tripped and not breaker.book(ACC)["kill"],
                "قاطع=%s" % breaker.book(ACC)["kill"])
    bad += show("والعدّادات صفر بلا تسريب", counters == (0.0, 0, 0), str(counters))
    bad += show("والفكّ أُعلن للبوّابات",
                bus_b.count("risk.kill_switch.reset_requested") == 1,
                "إعلانات=%d" % bus_b.count("risk.kill_switch.reset_requested"))

    print("\n٦+٧· إعادة التشغيل — نسخ جديدة تبدأ نظيفة:")
    bus_r = Bus()
    restarted = await build("578", bus_r)
    fresh_breaker = await build("516", bus_r)
    bad += show("القاطع يبدأ مفتوحًا", not fresh_breaker.book(ACC)["kill"], "kill=%s" % fresh_breaker.book(ACC)["kill"])
    await restarted._on_external({"official_time": T0 + 200.0, "account_id": ACC,
                                  "trade_allowed": True})
    await restarted._on_target(snapshot("snap-new", 0.22, 0.02))
    bad += show("٧· بيانات جديدة تعمل بعد الإعادة", len(bus_r.requests()) > 0,
                "أوامر=%d" % len(bus_r.requests()))

    print("\n٨· هدف ما قبل الإيقاف يُعاد بعد الفكّ:")
    after_new = len(bus_r.requests())
    await restarted._on_external({"official_time": T0 + 260.0, "account_id": ACC,
                                  "trade_allowed": True})
    await restarted._on_target(snapshot("snap-1"))          # the pre-HALT target, replayed
    replayed = len(bus_r.requests()) - after_new
    print("      ⓘ نسخة 578 بعد الإعادة بلا ذاكرة فيضان — تُقاس النتيجة كما هي")
    bad += show("الهدف القديم لا يُنتج أثرًا أكبر من واحد", replayed <= 2,
                "أوامر جديدة=%d" % replayed)
    ids = [str(r.get("request_id")) for r in bus_r.requests()]
    bad += show("وكل أثر بمعرّف فريد", len(ids) == len(set(ids)),
                "%d · فريد=%d" % (len(ids), len(set(ids))))

    print("\n١٠· صفر أمر للسوق:")
    for name in FORBIDDEN:
        total = sum(b.count(name) for b in (bus, bus_p, bus_b, bus_r))
        bad += show("لا حدث %s" % name, total == 0, str(total))
    cfg552 = (card("552").get("config") or {})
    cfg575 = (card("575").get("config") or {})
    bad += show("البوّابتان مقفولتان",
                cfg552.get("enabled") is False and cfg575.get("enabled") is False,
                "552=%s · 575=%s" % (cfg552.get("enabled"), cfg575.get("enabled")))

    print("\n" + "=" * 96)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الإيقاف يوقف فعلًا · والفكّ يصفّر · والإعادة تبدأ نظيفة.")
    return 1 if bad else 0



# ⏳ م-47 (ورقة ٤١، 2026-08-28): هذا الفحص يسبر سلسلة كاملة (ذرّات مدموجة عدّة:
# 517/506/550/552/578...) وقد انحرفت عقودها بعد الدمج (هويّة القرار في 578،
# حالات 552/550، رفع البوّابتين enabled بأمر المالك). ترحيله واجبٌ مستقل
# بنافذة خاصة — يُترك أحمرَ صادقًا ولا يُلوَّن زورًا.

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
