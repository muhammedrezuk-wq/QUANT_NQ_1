"""ورقة X — البناء ٢: محرّك المحاكاة — اختبارات التكافؤ.

شروط الورقة الأربعة القابلة للقياس هنا:
  ١ · نفس المقطع مرّتين ← نفس cycle_id لكل تِكّة
  ٢ · نفس المقطع مرّتين ← نفس القرارات بالترتيب نفسه
  ٣ · عدّاد market.tick.validated في المحاكاة = عدد الصفوف المقروءة
  ٤ · صفر كتابة في var/store أثناء المحاكاة
  (الخامس — مطابقة ما سجّله الخطّ الحيّ — يُقاس على جهاز المالك ببياناته)

والجسر الصوري 626: انزلاق من السبريد المسجَّل، عمولة، حتميّة، ورموز أسباب
مرئية في health_check (بناء ٣).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from core.contracts.atom import AtomContext
from core.event_bus import EventBus
from shared.tick_contract import as_validated_tick

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)



class _Log:
    def __getattr__(self, _name: str):
        return lambda *a, **k: None


def load_atom(atom_id: int):
    folder = next((ATOM_ROOT).glob(f"{atom_id}_*/atom.py"))
    sys.path.insert(0, str(folder.parent))
    try:
        spec = importlib.util.spec_from_file_location(f"x_build2_atom_{atom_id}", folder)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def make_replay_db(path: Path, rows: int = 25) -> None:
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE market_data (
        id INTEGER PRIMARY KEY, symbol TEXT, provider TEXT, bid REAL, ask REAL,
        occurred_at REAL, payload_json TEXT)""")
    for i in range(1, rows + 1):
        price = 20000.0 + i
        payload = json.dumps({
            "account_id": "A", "broker": "Raw Trading Ltd", "symbol": "NQ",
            "bid": price - 0.10, "ask": price + 0.10, "price": price,
            "timestamp": 1_800_000_000.0 + i, "exchange_timestamp": 1_800_000_000.0 + i,
            "volume": 5 + (i % 7)})
        conn.execute(
            "INSERT INTO market_data (id, symbol, provider, bid, ask, occurred_at, payload_json)"
            " VALUES (?,?,?,?,?,?,?)",
            (i, "NQ", "CTRADER", price - 0.10, price + 0.10,
             1_800_000_000.0 + i, payload))
    conn.commit(); conn.close()


SIM_ROUTES = {"feed.replay.tick": "market.tick"}


class SimChain:
    """625 → (613 بنمط إعداد المحاكاة) → 112 → 451 → 452 → 453 على ناقل حقيقي."""

    def __init__(self, db_path: Path, store_root: Path | None = None):
        self.db_path = db_path
        self.store_root = store_root
        self.bus = EventBus()
        self.modules = {i: load_atom(i) for i in (625, 613, 112, 451, 452, 453)}
        self.atoms: dict[int, Any] = {}
        self.scored: list[dict] = []
        self.validated: list[dict] = []
        self.replay_ticks: list[dict] = []

    async def start(self) -> None:
        # قرار العزل ٤ (ختم ٢٠٢٦-٠٨-٢٣): مخازن التشغيل تحت var/store_sim/<run> —
        # قناة الإعداد الرسميّة للمخازن (QUANT_ANALYSIS_SETTINGS_DB) تُوجَّه هناك،
        # فلا تلمس المحاكاة مخازن الإنتاج أبدًا.
        import os
        if self.store_root is not None:
            self.store_root.mkdir(parents=True, exist_ok=True)
            os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = str(self.store_root / "analysis_settings.db")
        configs = {
            625: {"db_path": str(self.db_path), "symbols": [], "limit": 0,
                  "account_id": "A", "broker": "Raw Trading Ltd",
                  "tick_prefix": "replay", "pace_seconds": 0.01, "progress_every": 1000},
            613: {"routes": dict(SIM_ROUTES), "provider_timeout_s": 30,
                  "max_input_silence_seconds": 60, "preferred_provider": ""},
            112: {},
            451: {"expected_families": ["150", "400"], "require_same_cycle": True},
            452: {"low_quality_factor": 0.5, "min_confidence": 0.0},
            453: {"directional_weight": 1.0, "context_weight": 0.0556,
                  "min_participation": 0.2, "directional_sources": ["400"]},
        }
        for atom_id, module in self.modules.items():
            atom = module.Atom()
            self.atoms[atom_id] = atom
            await atom.initialize(AtomContext(
                atom_id, configs[atom_id], _Log(),
                lambda name, payload, aid=atom_id: self.bus.publish(name, payload, publisher=str(aid)),
                lambda name, handler, aid=atom_id: self.bus.subscribe(name, handler, subscriber=str(aid))))
            await atom.start()
        self.bus.subscribe("decision.scored.state", self.scored.append, subscriber="probe")
        self.bus.subscribe("market.tick.validated", self.validated.append, subscriber="probe")
        self.bus.subscribe("feed.replay.tick", self.replay_ticks.append, subscriber="probe")

    async def feed_cards_and_run(self) -> None:
        # بطاقتا 166 و400 قبل الجلسة — كل تِكّة تختمهما في دورتها (العقد الحيّ)
        # البناء ٤: التحليل بطاقة قسم 150 على القناة القسمية
        await self.bus.publish("analysis.section.live", {
            "account_id": "A", "broker": "Raw Trading Ltd", "symbol": "NQ",
            "section_id": "150", "status": "ok", "signal": "up", "score": 70,
            "confidence": 72, "quality": "good", "state": "READY", "ready": True,
            "unified": {"state": "READY", "direction": 60.0, "strength": 70.0,
                        "weight": 16.6667, "weight_effect": 16.6667}}, publisher="150")
        await self.bus.publish("strategy.section.live", {
            "account_id": "A", "broker": "Raw Trading Ltd", "symbol": "NQ",
            "section_id": "400", "status": "ok", "signal": "buy", "score": 80,
            "confidence": 90, "quality": "good", "state": "READY", "ready": True,
            "unified": {"state": "READY", "direction": 1.0, "strength": 80.0,
                        "weight": 20.0, "weight_effect": 20.0}}, publisher="400")
        await self.bus.publish("replay.session.start", {}, publisher="test")
        # EventBus publish enqueues mailbox work; drain the real transport
        # before asserting the replay cascade, rather than relying on one
        # scheduler tick.
        await self.bus.drain()

    def cycle_ids(self) -> list[str]:
        return [as_validated_tick(t)["cycle_id"] for t in self.validated]


@pytest.mark.asyncio
async def test_x1_same_segment_twice_same_cycle_ids(tmp_path: Path):
    db = tmp_path / "market_data.db"; make_replay_db(db)
    run_a = SimChain(db, store_root=tmp_path / "store_sim" / "run-a"); await run_a.start(); await run_a.feed_cards_and_run()
    ids_a = run_a.cycle_ids()

    run_b = SimChain(db, store_root=tmp_path / "store_sim" / "run-b"); await run_b.start(); await run_b.feed_cards_and_run()
    ids_b = run_b.cycle_ids()

    assert len(ids_a) == 25 and len(ids_b) == 25
    assert ids_a == ids_b, "هوية الدورة ليست حتميّة عبر إعادات التشغيل"
    assert len(set(ids_a)) == 25, "تكرار معرّفات دورات"


@pytest.mark.asyncio
async def test_x2_same_segment_twice_same_decisions_in_order(tmp_path: Path):
    db = tmp_path / "market_data.db"; make_replay_db(db)
    run_a = SimChain(db, store_root=tmp_path / "store_sim" / "run-a"); await run_a.start(); await run_a.feed_cards_and_run()
    run_b = SimChain(db, store_root=tmp_path / "store_sim" / "run-b"); await run_b.start(); await run_b.feed_cards_and_run()

    def verdicts(run: SimChain):
        return [(s["cycle_id"], s.get("direction"), s.get("score")) for s in run.scored]

    assert verdicts(run_a) == verdicts(run_b), "القرارات ليست حتميّة بالترتيب نفسه"
    assert verdicts(run_a), "لم تصدر قرارات أصلًا"


@pytest.mark.asyncio
async def test_x3_validated_counter_equals_rows_read(tmp_path: Path):
    db = tmp_path / "market_data.db"; make_replay_db(db, rows=17)
    run = SimChain(db, store_root=tmp_path / "store_sim" / "run-x3"); await run.start(); await run.feed_cards_and_run()
    published = run.bus.stats()["published"].get("market.tick.validated", 0)
    rows_read = run.atoms[625]._rows_read
    assert rows_read == 17
    assert published == rows_read == len(run.validated)


@pytest.mark.asyncio
async def test_x4_zero_writes_to_production_store(tmp_path: Path, monkeypatch):
    """صفر كتابة في var/store أثناء المحاكاة — بشرط قرار العزل ٤ (المخازن تحت
    store_sim لكل تشغيل) الذي تطبّقه السلسلة أعلاه (شرط ٤ من ورقة X)."""
    store = ROOT / "var" / "store"
    before = {str(p): p.stat().st_mtime_ns for p in store.rglob("*")} if store.is_dir() else {}

    db = tmp_path / "market_data.db"; make_replay_db(db, rows=5)
    run = SimChain(db, store_root=tmp_path / "store_sim" / "run-x4"); await run.start(); await run.feed_cards_and_run()
    await asyncio.sleep(0)

    after = {str(p): p.stat().st_mtime_ns for p in store.rglob("*")} if store.is_dir() else {}
    assert before == after, "المحاكاة لمست مخازن الإنتاج"


@pytest.mark.asyncio
async def test_625_health_shows_drops_with_reasons(tmp_path: Path):
    db = tmp_path / "market_data.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE market_data (
        id INTEGER PRIMARY KEY, symbol TEXT, provider TEXT, bid REAL, ask REAL,
        occurred_at REAL, payload_json TEXT)""")
    conn.execute("INSERT INTO market_data VALUES (1,'NQ','CTRADER',1,2,5,'{باطل')")
    conn.commit(); conn.close()

    module = load_atom(625)
    atom = module.Atom()
    published: list[tuple[str, dict]] = []
    async def _pub(n, p): published.append((n, p))
    await atom.initialize(AtomContext(625, {"db_path": str(db), "account_id": "A",
                                            "broker": "BR"}, _Log(), _pub, lambda *a: None))
    await atom.start()
    await atom._on_start({})
    health = await atom.health_check()
    assert atom._rows_read == 1 and atom._published == 0
    assert atom._dropped == 1 and atom._drop_reasons.get("BAD_PAYLOAD_JSON") == 1
    assert health.details["dropped"] == 1


@pytest.mark.asyncio
async def test_626_spread_fill_deterministic_and_visible():
    module = load_atom(626)
    atom = module.Atom()
    fills: list[dict] = []
    async def _pub(n, p):
        if isinstance(p, dict) and "entry_price" in p:
            fills.append(p)
    await atom.initialize(AtomContext(626, {"per_lot_commission": 7.0, "latency_ms": 3},
                                       _Log(), _pub, lambda *a: None))
    await atom.start()

    # سبريد مسجّل: bid=99.90 ask=100.10 → الانزلاق = 0.10
    await atom._on_tick({"symbol": "NQ", "bid": 99.90, "ask": 100.10})
    order = {"request_id": "r1", "account_id": "A", "broker": "BR", "symbol": "NQ",
             "action": "OPEN", "side": "BUY", "volume": 2.0, "reference_price": 100.0}
    await atom._on_order(dict(order))
    assert fills and fills[0]["event_type"] == "OPENED"
    assert fills[0]["entry_price"] == pytest.approx(100.10)  # 100.0 + (0.2/2)
    assert fills[0]["slippage"] == pytest.approx(0.10)
    assert fills[0]["slippage_source"] == "recorded_spread"
    assert fills[0]["commission"] == pytest.approx(14.0)      # 7.0 × 2.0 لوت
    assert fills[0]["trade_id"] == "sim-r1"
    # الحتميّة: نفس الأمر مرّتين ← نفس الحشو
    await atom._on_order(dict(order))
    assert fills[1]["entry_price"] == fills[0]["entry_price"]
    # بيع: يُملأ أسوأ بالانزلاق نفسه
    await atom._on_order({**order, "request_id": "r2", "side": "SELL"})
    assert fills[2]["entry_price"] == pytest.approx(99.90)
    # إجراء غير OPEN → مرميّ برمز سبب مرئي لا صمت
    await atom._on_order({**order, "request_id": "r3", "action": "MODIFY_SL"})
    health = await atom.health_check()
    assert health.details["dropped"] >= 1
    assert "ACTION_NOT_SIMULATED" in health.details["drop_reasons"]


@pytest.mark.asyncio
async def test_626_no_recorded_spread_counted_not_hidden():
    module = load_atom(626)
    atom = module.Atom()
    fills: list[dict] = []
    async def _pub(n, p):
        if isinstance(p, dict) and "entry_price" in p:
            fills.append(p)
    await atom.initialize(AtomContext(626, {}, _Log(), _pub, lambda *a: None))
    await atom.start()
    await atom._on_order({"request_id": "r9", "account_id": "A", "broker": "BR",
                          "symbol": "ES", "action": "OPEN", "side": "BUY",
                          "volume": 1.0, "reference_price": 5000.0})
    assert fills and fills[0]["slippage"] == 0.0
    assert fills[0]["slippage_source"] == "reference_only"
    health = await atom.health_check()
    assert "SPREAD_UNAVAILABLE_REFERENCE_ONLY" in health.details["drop_reasons"]


def test_validator_rule_flags_silent_droppers_not_the_fixed(tmp_path: Path):
    """قاعدة المدقق (بناء ٣): تُرصد الذرّة الصامتة بالاسم، ولا تُرصد من أصلحت."""
    import ast
    sys.path.insert(0, str(ROOT / "governance" / "scripts"))
    try:
        import validate_atoms as va
    finally:
        sys.path.pop(0)

    bad_src = ("async def _on_thing(payload):\n"
               "    if not payload.get(\"symbol\"):\n"
               "        return\n")
    assert va.silent_drop_risk(ast.parse(bad_src), bad_src) == ["_on_thing"]

    # حارس دورة الحياة ليس إسقاطًا — لا يُحسب
    guard_src = ("async def _on_ok(payload):\n"
                 "    if not self._running or not isinstance(payload, dict):\n"
                 "        return\n")
    assert va.silent_drop_risk(ast.parse(guard_src), guard_src) == []

    for atom_id in (451, 452, 453, 613, 112, 625, 626):
        folder = next((ATOM_ROOT).glob(f"{atom_id}_*"))
        src = (folder / "atom.py").read_text(encoding="utf-8")
        assert va.silent_drop_risk(ast.parse(src), src) == [], f"ذرّة {atom_id} رُصدت خطأً"
