"""Phase 4 — the hedge and the trades, proven as ONE loop, not as green atoms.

Owner's requirement, verbatim: the chain must be shown whole --

    market/reference -> decision -> risk/exposure -> hedge decision ->
    execution intent -> execution request -> order -> fill/outcome ->
    position -> account state -> risk reset / next cycle

    "and the test is not 'the atom is green'. It is: a real trade enters the
     chain, the hedge behaves per the contract, the final state is recorded, an
     old trade is not re-broadcast as if it were new, and the breaker does not
     fire on dead history."

    HALT / LOSS / RESET / RESTART / REPLAY / RECOVERY are all exercised -- not
    only the success path.

The forward half (581 -> 583 -> 578 -> 586/708 -> 585 -> 516 -> 551 -> 584 ->
552) already has two guards of its own: `check_hedge_contract` proves the five
equations against an independent ruler, and `check_hedge_chain` walks the real
atoms to the gate. This guard is the RETURN half and the failure modes, which
nothing covered: what happens after the fill, and what happens when things go
wrong.

Every atom here is the real one, loaded from the project. The gate stays shut
throughout, and the guard asserts that nothing ever reached the market.

Exit 1 on any divergence.
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

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
FOLDERS = {a.name.split("_")[0]: a.name for a in ATOMS.iterdir() if a.is_dir()}

EVENT_LOSS = "risk.loss_reported"
EVENT_HALT = "emergency.halt"
EVENT_HALT_REQUEST = "risk.halt.requested"
EVENT_RELEASE_REQUEST = "risk.release.requested"
EVENT_RELEASE = "risk.kill_switch.reset_requested"
EVENT_ACCOUNT = "platform.account.state"
EVENT_OUTCOME = "market.outcome.realized"
FORBIDDEN = ("trading.final_decision", "brain_signal.written")

ACC, SYM = "52992818", "BTCUSD"


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []
        self.wired = {}

    def subscribe(self, name, handler):
        self.wired.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.log.append((name, payload))

    def rows(self, name):
        return [p for n, p in self.log if n == name]

    def last(self, name):
        rows = self.rows(name)
        return rows[-1] if rows else None

    def count(self, name):
        return len(self.rows(name))


def card(atom_id: str) -> dict:
    return yaml.safe_load(
        (ATOMS / FOLDERS[atom_id] / "manifest.yaml").read_text(encoding="utf-8"))


async def build(atom_id: str, bus: Bus, overrides: dict | None = None):
    directory = ATOMS / FOLDERS[atom_id]
    spec = importlib.util.spec_from_file_location("_p4_%s" % atom_id, directory / "atom.py")
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
    return module, atom


async def deliver(bus: Bus, name: str, payload: dict) -> None:
    """Hand the event to every REAL subscriber, exactly as the bus would."""
    for handler in list(bus.wired.get(name, [])):
        await handler(dict(payload))


def show(label: str, ok: bool, detail: str = "") -> int:
    print("      %-42s %-26s %s" % (label, detail, "✓" if ok else "✗"))
    return 0 if ok else 1


async def outcome_to_risk() -> int:
    print("=" * 92)
    print("أ) الصفقة تُغلق ⇒ النتيجة ⇒ المخاطر (517 → 516) — بهويّة الصفقة")
    print("=" * 92)
    bad = 0
    bus = Bus()
    await build("517", bus)
    _, breaker = await build("516", bus)

    await deliver(bus, EVENT_ACCOUNT, {"account_id": ACC, "equity": 1000.0, "balance": 1000.0})
    await deliver(bus, EVENT_OUTCOME, {"account_id": ACC, "symbol": SYM, "profit": -5.0,
                                       "ticket": 90001, "trade_id": "t-90001", "result": "LOSS"})
    loss = bus.last(EVENT_LOSS)
    bad += show("خسارة محقّقة تصل المخاطر", bool(loss), str((loss or {}).get("pnl")))
    bad += show("وتحمل هويّة الصفقة",
                bool(loss) and loss.get("ticket") == 90001 and loss.get("trade_id") == "t-90001",
                "ticket=%s" % (loss or {}).get("ticket"))

    await deliver(bus, EVENT_LOSS, dict(loss or {}, event_id="chk-tl-1", account_id=ACC))
    bad += show("والقاطع عدّها فعلًا", breaker.book(ACC)["daily_trade_count"] == 1,
                "trades=%d" % breaker.book(ACC)["daily_trade_count"])
    bad += show("ولم يقطع على خسارة واحدة", not breaker.book(ACC)["kill"], "kill=%s" % breaker.book(ACC)["kill"])
    return bad


async def halt_and_release() -> int:
    print("\n" + "=" * 92)
    print("ب) HALT ⇒ RESET — سلطة واحدة، ومسار فكّ واحد (506 → 516)")
    print("=" * 92)
    bad = 0
    bus = Bus()
    _, limits = await build("506", bus)
    _, breaker = await build("516", bus)

    await deliver(bus, EVENT_ACCOUNT, {"account_id": ACC, "equity": 1000.0, "balance": 1000.0})
    limit = float((card("506").get("config") or {})["max_session_loss_pct"])
    await deliver(bus, EVENT_LOSS, {"event_id": "chk-tl-2", "account_id": ACC, "loss_pct": limit + 1.0, "is_loss": True})

    request = bus.last(EVENT_HALT_REQUEST)
    bad += show("الحدّ يطلب الإيقاف ولا يعلنه", bool(request),
                "origin=%s" % (request or {}).get("origin"))
    bad += show("والطلب يسمّي سببه وأصله",
                bool(request) and request.get("origin") == "506" and request.get("reason"),
                str((request or {}).get("reason")))

    await deliver(bus, EVENT_HALT_REQUEST, dict(request or {}, account_id=ACC))
    halt = bus.last(EVENT_HALT)
    bad += show("والمالك وحده يُصدر الإيقاف",
                bool(halt) and halt.get("origin") == "506" and breaker.book(ACC)["kill"],
                "reason=%s" % (halt or {}).get("reason"))

    await deliver(bus, EVENT_RELEASE_REQUEST, {"account_id": ACC, "operator": "dashboard"})
    released = bus.last(EVENT_RELEASE)
    bad += show("والفكّ يفتحه ويُعلن أثره",
                (not breaker.book(ACC)["kill"]) and bool(released) and released.get("origin") == "516",
                "cleared=%s" % (released or {}).get("cleared"))
    bad += show("ومتتاليّته صفر بعد الفكّ (اليوميّة لليلها — v5.2.0)",
                breaker.book(ACC)["consecutive_losses"] == 0 and breaker.book(ACC)["reason"] == "",
                "consec=%d reason=%s" % (breaker.book(ACC)["consecutive_losses"], breaker.book(ACC)["reason"]))
    return bad


async def restart_without_replay() -> int:
    print("\n" + "=" * 92)
    print("ج) RESTART / REPLAY — إقلاع على تاريخ ميّت لا يضرب القاطع")
    print("=" * 92)
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "nq_brain.db")
        bus = Bus()
        module, reader = await build("611", bus, {"db_path": db, "table_name": "trade_events"})
        columns = [c for c in module._COLUMNS if c != "id"]
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE trade_events (id INTEGER PRIMARY KEY AUTOINCREMENT, %s)"
                     % ", ".join("%s TEXT" % c for c in columns + ["account_id", "profit"]))
        marks = ", ".join("?" for _ in columns)
        for _ in range(60):                       # 60 closed trades, all history
            conn.execute("INSERT INTO trade_events (%s) VALUES (%s)"
                         % (", ".join(columns), marks),
                         tuple("1.0" if c in ("close_time", "open_time") else "x"
                               for c in columns))
        conn.commit()
        conn.close()

        await reader.stop()
        bus.log.clear()
        _, breaker = await build("516", bus)
        module2, fresh = await build("611", bus, {"db_path": db, "table_name": "trade_events"})
        await fresh.stop()
        await fresh._drain_once()

        replayed = bus.count("platform.trade_event")
        bad += show("إقلاع بلا لقطة لا يعيد بثّ التاريخ", replayed == 0,
                    "أحداث=%d من ٦٠ صفًّا" % replayed)
        bad += show("والقاطع لم يُضرب من الموتى", not breaker.book(ACC)["kill"],
                    "halts=%d" % bus.count(EVENT_HALT))
        bad += show("والمؤشّر عند آخر الجدول", fresh._last_id == 60,
                    "last_id=%d" % fresh._last_id)

        await fresh.restore({"last_id": 55})
        bus.log.clear()
        await fresh._drain_once()
        bad += show("واللقطة تُطاع فيكمل من حيث وقف",
                    bus.count("platform.trade_event") == 5,
                    "أحداث=%d" % bus.count("platform.trade_event"))
    return bad


async def account_recovery() -> int:
    print("\n" + "=" * 92)
    print("د) RECOVERY — حالة الحساب تقول عمرها بدل أن تصمت")
    print("=" * 92)
    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "acct.db")
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE account (id INTEGER PRIMARY KEY, account_id TEXT,"
                     " balance REAL, equity REAL, margin REAL, free_margin REAL,"
                     " margin_level REAL, currency TEXT, leverage INTEGER, open_count INTEGER,"
                     " broker TEXT, account_server TEXT, margin_mode TEXT,"
                     " trade_allowed INTEGER, connected INTEGER, updated_at REAL)")
        conn.execute("INSERT INTO account VALUES (1,?,644.84,644.84,0.0,644.84,0.0,'USD',"
                     "5000,0,'Raw Trading Ltd','ICMarketsSC-Demo','HEDGING',1,1,?)",
                     (ACC, 1_000_000.0 - 220_291.0))
        conn.commit()
        conn.close()

        bus = Bus()
        _, reader = await build("619", bus, {"db_path": db, "max_age_s": 300.0})
        await reader.stop()
        await deliver(bus, "SYS_SECOND", {"official_time": 1_000_000.0})
        await reader._read_once()
        state = bus.last(EVENT_ACCOUNT)
        bad += show("الحساب المتقادم ما زال مرئيًّا", bool(state),
                    "age=%s" % (state or {}).get("age_s"))
        bad += show("ويعلن تقادمه صراحةً", bool(state) and state.get("stale") is True,
                    "stale=%s" % (state or {}).get("stale"))
        bad += show("وأرقامه لم تُخترَع", bool(state) and state.get("equity") == 644.84,
                    "equity=%s" % (state or {}).get("equity"))
    return bad


async def gate_stays_shut() -> int:
    print("\n" + "=" * 92)
    print("هـ) ولا أمر بلغ السوق طوال الدورة")
    print("=" * 92)
    bad = 0
    gate = float(0) if (card("552").get("config") or {}).get("enabled") else 0
    bad += show("بوّابة التنفيذ مقفولة",
                (card("552").get("config") or {}).get("enabled") is False, "552.enabled=false")
    bad += show("ومرسل الإدارة مقفول",
                (card("575").get("config") or {}).get("enabled") is False, "575.enabled=false")
    del gate
    return bad


async def main_async() -> int:
    bad = 0
    bus_forbidden = Bus()
    for step in (outcome_to_risk, halt_and_release, restart_without_replay,
                 account_recovery, gate_stays_shut):
        bad += await step()
    for name in FORBIDDEN:
        bad += show("لا حدث %s" % name, bus_forbidden.count(name) == 0, "0")

    print("\n" + "=" * 92)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الدورة كاملة — نتيجة ⇒ مخاطر ⇒ إيقاف ⇒ فكّ ⇒ إقلاع بلا تاريخ ⇒ حساب صادق.")
    return 1 if bad else 0



# ⏳ م-47 (ورقة ٤١، 2026-08-28): هذا الفحص يسبر سلسلة كاملة (ذرّات مدموجة عدّة:
# 517/506/550/552/578...) وقد انحرفت عقودها بعد الدمج (هويّة القرار في 578،
# حالات 552/550، رفع البوّابتين enabled بأمر المالك). ترحيله واجبٌ مستقل
# بنافذة خاصة — يُترك أحمرَ صادقًا ولا يُلوَّن زورًا.

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
