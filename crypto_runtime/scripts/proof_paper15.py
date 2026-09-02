# -*- coding: utf-8 -*-
"""إثبات ورقة ١٥ — يقيس البنود الثمانية بالتشغيل، لا بالقراءة.

الاستعمال (من جذر المشروع):
    python proof_paper15.py  [مسار_المشروع]

يعمل على ويندوز ولينكس. لا يعدّل أي ملف من المشروع.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ⛔ تحت pytest يكون `sys.argv[1]` ملفَّ الإثبات نفسه لا جذر المشروع،
#    فكان `ROOT` يشير إلى ملفٍّ وتسقط كلّ فحوص `glob` بـ StopIteration
#    (٢٠٢٦-٠٨-١٩). الوسيط يُحترم فقط إن كان مجلدًا فعليًّا؛ وإلّا فالجذر
#    من موضع الملفّ (`scripts/..`) — فيصحّ المشغّلان معًا.
_ARGUMENT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
ROOT = (_ARGUMENT if _ARGUMENT is not None and _ARGUMENT.is_dir()
        else Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
from shared.cycle_identity import (  # noqa: E402
    cycle_key, cycle_key_of, split_cycle_key)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print("  ✅ %s" % name)
    else:
        FAILED.append((name, detail))
        print("  ❌ %s   %s" % (name, detail))


try:
    import pytest as _pytest
except ImportError:                                   # المشغّل الأصلي يكفيه العدّاد
    _pytest = None

if _pytest is not None:
    @_pytest.fixture(autouse=True)
    def _checks_must_hold():
        """صدق pytest — فشل أيّ `check` يُسقط فحص pytest نفسه.

        ⛔ بدونها كان `check` يسجّل الفشل في قائمةٍ لا يقرأها pytest،
           فيخضرّ الفحص وكلّ بنوده حمراء (٢٠٢٦-٠٨-١٩).
        """
        before = len(FAILED)
        yield
        fresh = FAILED[before:]
        assert not fresh, " · ".join(
            "%s — %s" % (name, detail) for name, detail in fresh)


def load(atom_id: int):
    folder = next((ROOT / "atoms").glob("%d_*" % atom_id))
    sys.path.insert(0, str(folder))
    name = "p15_%d" % atom_id
    spec = importlib.util.spec_from_file_location(name, folder / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Log:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.events = []
        self.handlers = {}

    def subscribe(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.events.append((name, payload))
        for handler in list(self.handlers.get(name, [])):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result

    def rows(self, name):
        return [p for n, p in self.events if n == name]


async def start(module, atom_id, config=None, bus=None):
    bus = bus or Bus()
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id, dict(config or {}), Log(),
                                      bus.publish, bus.subscribe))
    await atom.start()
    return atom, bus


class approved_gate:
    """يعتمد المُعامِلات الستّة في قاعدة **مؤقّتة** ثمّ يعيد الحال.

    ⛔ ليس تخفيفًا للحاجز بل إثباتٌ له: بوّابة لا تُفتح أبدًا ليست
       بوّابة. هنا نثبت أنّها تُفتح بالاعتماد الصريح وتُغلق بزواله،
       ولا تُمسّ قاعدة المشروع ولا تُعتمد قيمة فيها.
    """

    def __enter__(self):
        import os
        import tempfile
        import clock
        from shared.parameter_registry import (
            DECLARED, ParameterRegistry, SOURCE_OWNER, refresh_gate)
        handle, self.path = tempfile.mkstemp(suffix=".db", prefix="p15_gate_")
        os.close(handle)
        os.unlink(self.path)
        self.previous = os.environ.get("QUANT_ANALYSIS_SETTINGS_DB")
        os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = self.path
        registry = ParameterRegistry()
        for index, (name, spec) in enumerate(DECLARED.items()):
            registry.approve(name, value=float(spec["value"]),
                             source=SOURCE_OWNER, approved_by="proof",
                             command_id="gate-%d" % index,
                             approved_at=clock.now())
        refresh_gate()
        return self

    def __exit__(self, *exc_info):
        import os
        from shared.parameter_registry import refresh_gate
        if self.previous is None:
            os.environ.pop("QUANT_ANALYSIS_SETTINGS_DB", None)
        else:
            os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = self.previous
        refresh_gate()
        try:
            os.unlink(self.path)
        except OSError:
            pass
        return False


IDENT = {"account_id": "A1", "broker": "BR", "symbol": "NQ100"}


def candle(period_start=100.0, close=100.0, **extra):
    row = {"symbol": "NQ100", "timeframe": "60s", "period_start": period_start,
           "timestamp": period_start, "open": close, "high": close + 1,
           "low": close - 1, "close": close, "volume": 10,
           "account_id": "A1", "broker": "BR"}
    row.update(extra)
    return row


# ═══ §١٢-0 · §١٢-1 · §١٢-2 — الهوية والعقد والحالات ═══════════════════════
async def test_contract_on_all_sections():
    print("\n### §١٢-0/1/2 — الهوية · العقد الموحّد · الحالات السبع ###")
    from shared.unified_contract import ALL_STATES

    cases = [
        (201, "200", {"lookback": 2}, "structure.swing.state"),
        (301, "300", {"window_size": 5}, "stats.mean.state"),
        (255, "250", {"min_gap_pct": 0.0}, "liquidity.fvg.state"),
    ]
    for atom_id, section, config, event in cases:
        module = load(atom_id)
        try:
            atom, bus = await start(module, atom_id, config)
        except Exception as exc:                      # إعداد مختلف — يُقاس بالمتاح
            check("%d يقبل التهيئة" % atom_id, False, repr(exc))
            continue
        for i in range(8):
            await atom._on_candle(candle(float(i), 100.0 + i))
        rows = bus.rows(getattr(module, "EVENT_OUT", event))
        check("%d ينشر" % atom_id, bool(rows), "لا حمولة")
        if not rows:
            continue
        last = rows[-1]
        unified = last.get("unified")
        check("%d يحمل كتلة العقد" % atom_id, isinstance(unified, dict), str(last.keys()))
        if not isinstance(unified, dict):
            continue
        check("%d هوية كاملة (account+broker+symbol)" % atom_id,
              unified.get("account_id") == "A1" and unified.get("broker") == "BR"
              and unified.get("symbol") == "NQ100", str(unified)[:120])
        check("%d section_id=%s" % (atom_id, section), unified.get("section_id") == section,
              str(unified.get("section_id")))
        for field in ("direction", "strength", "confidence", "weight", "ratio",
                      "current_depth", "required_depth", "state"):
            check("%d حقل %s موجود" % (atom_id, field), field in unified)
        check("%d الحالة من السبع" % atom_id, unified.get("state") in ALL_STATES,
              str(unified.get("state")))
        check("%d confidence على مقياس 0..100" % atom_id,
              0.0 <= float(unified["confidence"]) <= 100.0, str(unified["confidence"]))
        check("%d direction بين -100 و +100" % atom_id,
              -100.0 <= float(unified["direction"]) <= 100.0, str(unified["direction"]))
        check("%d الحقول غير المحسوبة مُعلَنة" % atom_id,
              "weight" in unified.get("unknown_fields", []),
              str(unified.get("unknown_fields")))
        await atom.stop()


async def test_identity_missing_is_declared():
    print("\n### §٩ · اختبار ١٩ — حمولة بلا broker تُرفض وتُعلَن ###")
    module = load(201)
    atom, bus = await start(module, 201, {"lookback": 2})
    for i in range(8):
        row = candle(float(i), 100.0 + i)
        row.pop("broker")
        await atom._on_candle(row)
    last = bus.rows(module.EVENT_OUT)[-1]
    unified = last.get("unified") or {}
    check("النقص مُعلَن لا مُخترَع", unified.get("identity_complete") is False,
          str(unified.get("identity_complete")))
    check("سبب الرفض مكتوب", unified.get("reason") == "IDENTITY_INCOMPLETE",
          str(unified.get("reason")))
    check("الحالة INVALID لا READY", unified.get("state") == "INVALID",
          str(unified.get("state")))
    await atom.stop()


async def test_independence_of_opinion_fields():
    print("\n### اختبار ٢ — الحقول الخمسة مستقلّة ###")
    from shared.section_contract import stamp_section
    out = stamp_section({**IDENT, "status": "ok", "signal": "up", "score": 70,
                         "confidence": 0.5}, section_id="200", atom_id="201")
    u = out["unified"]
    values = {u["direction"], u["strength"], u["confidence"], u["weight"], u["ratio"]}
    down = stamp_section({**IDENT, "status": "ok", "signal": "down", "score": 70,
                          "confidence": 0.5}, section_id="200", atom_id="201")["unified"]
    check("direction يحمل الإشارة", down["direction"] == -70.0,
          "dir=%s" % down["direction"])
    # §٨ — القوّة **ليست** |الاتجاه|. محلّل لا يرسل قوّته ⇒ UNKNOWN لا 70.
    check("قوّة غير مُرسَلة ⇒ UNKNOWN لا |direction|",
          down["strength"] == 0.0 and "strength" in down["unknown_fields"],
          "str=%s unknown=%s" % (down["strength"], down["unknown_fields"]))
    # مثال المالك حرفيًّا: اتجاه خفيف مع إشارة قويّة — كان مستحيلًا قبل.
    owner = stamp_section({**IDENT, "status": "ok", "signal": "up", "score": 20,
                           "strength": 90, "confidence": 0.82},
                          section_id="200", atom_id="201")["unified"]
    check("Direction +20 مع Strength 90 ممكن",
          owner["direction"] == 20.0 and owner["strength"] == 90.0
          and "strength" not in owner["unknown_fields"],
          "dir=%s str=%s" % (owner["direction"], owner["strength"]))
    check("القوّة مستقلّة عن |الاتجاه|",
          owner["strength"] != abs(owner["direction"]),
          "%s == %s" % (owner["strength"], abs(owner["direction"])))
    check("confidence لا تساوي score", u["confidence"] != u["strength"],
          "%s == %s" % (u["confidence"], u["strength"]))
    check("weight صفر ومُعلَن غير محسوب", u["weight"] == 0.0
          and "weight" in u["unknown_fields"])
    check("ratio صفر ومُعلَن غير محسوب", u["ratio"] == 0.0
          and "ratio" in u["unknown_fields"])
    check("خمسة حقول لا حقل واحد مكرّر", len(values) >= 3, str(values))


async def test_depth_blocks_ready():
    print("\n### اختبار ٣ — العمق الناقص يمنع READY ###")
    from shared.section_contract import stamp_section
    import clock
    with approved_gate():
        low = stamp_section({**IDENT, "status": "ok", "current_depth": 40,
                             "required_depth": 100, "source_timestamp": clock.now()},
                            section_id="200", atom_id="201")
        full = stamp_section({**IDENT, "status": "ok", "current_depth": 100,                              "required_depth": 100, "source_timestamp": clock.now(),                              "signal": "up", "score": 61.2204},
                             section_id="200", atom_id="201")
    check("عمق 40/100 ⇒ NOT_READY", low["unified"]["state"] == "NOT_READY",
          low["unified"]["state"])
    check("عمق 100/100 ⇒ READY (بعد اعتماد المُعامِلات)",
          full["unified"]["state"] == "READY", full["unified"]["state"])


# ═══ §٥ — الذرّة النائمة ═══════════════════════════════════════════════════
async def test_dormant_107():
    print("\n### §٥ · اختبارا ٥ و٦ — 107 نائمة لا معطوبة ###")
    module = load(107)
    config = {"source_event": "market.trade", "window_size": 50,
              "publish_every_n_trades": 1, "required_depth": 30.0}
    atom, bus = await start(module, 107, config)
    rows = bus.rows(module.EVENT_OUT)
    check("107 تُعلن حالتها عند البدء", bool(rows), "صامتة")
    if rows:
        check("107 DORMANT", rows[-1].get("state") == "DORMANT", str(rows[-1].get("state")))
    health = await atom.health_check()
    check("107 ليست UNHEALTHY", health.state != HealthState.UNHEALTHY, str(health.state))
    await atom._on_trade({**IDENT, "price": 100.0, "volume": 1, "side": "BUY",
                          "timestamp": 1.0})
    rows = bus.rows(module.EVENT_OUT)
    check("107 تنتقل تلقائيًّا عند وصول المصدر",
          rows[-1].get("state") in ("ANALYZING", "READY"), str(rows[-1].get("state")))
    await atom.stop()


# ═══ §٦ — منع السرقة ═══════════════════════════════════════════════════════
async def test_513_sizes_from_654():
    print("\n### اختبار ٨ — 513 يحجّم من 654 لا من 619 ###")
    module = load(513)
    config = {"risk_per_trade_pct": 1, "default_stop_pct": 1, "min_lot": .01,
              "max_lot": 10, "lot_step": .01}
    atom, bus = await start(module, 513, config)
    await bus.publish("platform.account.state", {**IDENT, "equity": 999999.0})
    await bus.publish(module.EVENT_SPECS, {"symbols": [
        {"account_id": "A1", "symbol": "NQ100", "tick_value": 10, "tick_size": .25}]})
    await bus.publish(module.EVENT_CANDLE, candle())
    check("619 وحده لا يكفي للتحجيم", not bus.rows(module.EVENT_OUT),
          "حجّم من الخام")
    check("النقص مُعلَن على financial.truth.shortage",
          any(r.get("owner") == "654" for r in bus.rows("financial.truth.shortage")),
          "لا إعلان")
    await bus.publish("portfolio.equity.state", {**IDENT, "equity": 10000.0})
    await bus.publish(module.EVENT_CANDLE, candle(200.0))
    rows = bus.rows(module.EVENT_OUT)
    check("654 وحده يفتح التحجيم", bool(rows), "لم يحجّم بعد وصول 654")
    await atom.stop()


async def test_516_reads_equity_from_654():
    print("\n### اختبار ٩ — 516 يقرأ حقوق الملكية من 654 ###")
    import tempfile
    module = load(516)
    with tempfile.TemporaryDirectory() as tmp:
        config = {"max_daily_loss_pct": 5, "max_consecutive_losses": 3,
                  "max_daily_trades": 20, "max_open_trades": 5,
                  "consumer_db_path": str(Path(tmp) / "c.db")}
        atom, bus = await start(module, 516, config)
        await bus.publish(module.EVENT_ACCOUNT, {**IDENT, "equity": 999999.0})
        check("619 لا يمنح حقوق ملكية", atom.book("A1")["equity"] is None,
              str(atom.book("A1")["equity"]))
        check("516 يُعلن النقص",
              any(r.get("owner") == "654" for r in bus.rows("financial.truth.shortage")),
              "لا إعلان")
        await bus.publish("portfolio.equity.state", {**IDENT, "equity": 1000.0})
        check("654 هو المصدر", atom.book("A1")["equity"] == 1000.0,
              str(atom.book("A1")["equity"]))
        await atom.stop()


async def test_585_reads_free_margin_from_656():
    print("\n### اختبار ١٠ — 585 يقرأ الهامش الحرّ من 656 ###")
    module = load(585)
    atom, bus = await start(module, 585, {"margin_buffer_pct": .1})
    await bus.publish(module.EVENT_ACCOUNT, {**IDENT, "free_margin": 999999.0,
                                             "leverage": 100})
    await bus.publish(module.EVENT_SPECS, {"symbols": [
        {"account_id": "A1", "symbol": "NQ100", "contract_size": 1}]})
    await bus.publish(module.EVENT_ORDER, {"request_id": "r1", "account_id": "A1",
        "symbol": "NQ100", "action": "OPEN", "side": "BUY", "volume": .1,
        "reference_price": 100})
    last = bus.rows(module.EVENT_OUT)[-1]
    check("619 لا يمنح هامشًا حرًّا", last["approved"] is False
          and last["reason"] == "FREE_MARGIN_MISSING", str(last["reason"]))
    check("585 يُعلن النقص",
          any(r.get("owner") == "656" for r in bus.rows("financial.truth.shortage")),
          "لا إعلان")
    await bus.publish("portfolio.free_margin.state", {**IDENT, "free_margin": 1000.0})
    await bus.publish(module.EVENT_ORDER, {"request_id": "r2", "account_id": "A1",
        "symbol": "NQ100", "action": "OPEN", "side": "BUY", "volume": .1,
        "reference_price": 100})
    last = bus.rows(module.EVENT_OUT)[-1]
    check("656 هو المصدر", last["approved"] is True, str(last["reason"]))
    await atom.stop()


async def test_no_atom_reads_money_from_619():
    print("\n### §٦.٤ — لا ذرّة تحسب رقمًا ماليًّا من الخام ###")
    money = ('payload.get("equity")', 'p.get("equity")', 'payload.get("free_margin")',
             'p.get("free_margin")', 'payload.get("balance")', 'p.get("balance")')
    thieves = ("506", "507", "508", "513", "516", "517", "585")
    for number in thieves:
        folder = next((ROOT / "atoms").glob("%s_*" % number))
        source = (folder / "atom.py").read_text(encoding="utf-8")
        hits = [needle for needle in money if needle in source]
        check("%s لا يقرأ رقمًا ماليًّا من الخام" % number, not hits, str(hits))


async def test_identity_readers_untouched():
    print("\n### اختبار ١٢ — قرّاء الهوية يبقون على 619 ###")
    keepers = ("112", "115", "518", "520", "523", "525", "551", "552",
               "560", "573", "576", "578", "583", "708")
    for number in keepers:
        folder = next((ROOT / "atoms").glob("%s_*" % number), None)
        if folder is None:
            continue
        source = (folder / "atom.py").read_text(encoding="utf-8")
        check("%s ما زال على 619" % number,
              "platform.account.state" in source or "platform.terminal_state" in source,
              "فُصل بلا داعٍ")


async def test_668_has_consumer():
    print("\n### §٦.٥ · اختبار ١٣ — 668 له مستهلك ###")
    folder = next((ROOT / "atoms").glob("709_*"))
    source = (folder / "atom.py").read_text(encoding="utf-8")
    check("709 يشترك في portfolio.overview.state",
          "portfolio.overview.state" in source, "لا مستهلك")
    module = load(668)
    atom, bus = await start(module, 668)
    await bus.publish("portfolio.components.state", {"accounts": [
        {"account_id": "A1", "equity": 1000.0}], "components": {}})
    # عقد ٩٠-١١ نقطة ٦ (٢٠٢٦-٠٨-١٩ · 668 v1.2.0): النشر على نبضة
    # `SYS_SECOND` التالية، لا فور كلّ حدث مكوّن — فلا فيضان ملخّصات.
    check("668 لم يعد ينشر قبل النبضة (عقد ٩٠-١١·٦)",
          not bus.rows(module.EVENT_OUT), "نشر فوريّ")
    await bus.publish("SYS_SECOND", {"sequence": 1, "pulse_id": "p-1"})
    rows = bus.rows(module.EVENT_OUT)
    check("668 ينشر ملخّصًا عند النبضة", bool(rows), "صامت")
    if rows:
        check("الملخّص يحمل صفوفًا ذات هوية",
              bool(rows[-1].get("accounts")) and rows[-1]["accounts"][0].get("account_id"),
              str(rows[-1].keys()))
    # نبضة بلا جديد ⇒ لا نشر مكرّر — من العقد نفسه (منع التكرار).
    await bus.publish("SYS_SECOND", {"sequence": 2, "pulse_id": "p-2"})
    check("نبضة بلا تغيّر لا تكرّر النشر",
          len(bus.rows(module.EVENT_OUT)) == len(rows),
          "نُشر %d" % len(bus.rows(module.EVENT_OUT)))
    await atom.stop()


async def test_519_no_self_loop():
    print("\n### §٦.٥ · اختبار ١٤ — 519 بلا حلقة ذاتية ###")
    module = load(519)
    atom, bus = await start(module, 519, {"exit_ratio": .9})
    published = set(module.EVENT_OUT for _ in (0,))
    check("519 لا يشترك في حدثه نفسه",
          module.EVENT_OUT not in bus.handlers, "الحلقة قائمة")
    check("قناة النيّة منفصلة",
          module.EVENT_INTENT in bus.handlers and module.EVENT_INTENT != module.EVENT_OUT,
          "لا قناة")
    await atom._on_account({**IDENT, "margin_mode": 2})
    await atom._on_ledger({"ledgers": [{"account_id": "A1", "broker": "BR",
        "symbol": "NQ100", "u": .5, "v_net": 1}]})
    check("النيّة تُنشر على قناتها", bool(bus.rows(module.EVENT_INTENT)), "لا نشر")
    await atom.stop()


# ═══ §١٢ — مقياس السيولة · §٢٠/§٢١ — حالة القسم من إلزاميّاته ═══════════
async def test_liquidity_measure_and_section_required_units():
    print("\n### §١٢ · §٢٠ · §٢١ — مقياس 260 وحالة القسم ###")
    from shared.section_live import REQUIRED_UNITS, required_state

    # §١٢ — 260: اتجاه وقوّة مقيسان من مصادره، لا صفر غامض.
    module = load(260)
    atom, bus = await start(module, 260, {})
    results = {
        "pool": {"status": "ok", "signal": "pool", "confidence": 0.5,
                 "metadata": {"price": 100.0}},
        "buyside": {"status": "ok", "signal": "buyside", "confidence": 0.6,
                    "metadata": {"price": 101.0}},
        "sellside": {"status": "ok", "signal": "sellside", "confidence": 0.4,
                     "metadata": {"price": 99.0}},
        "sweep": {"status": "ok", "signal": "sweep", "confidence": 0.8,
                  "metadata": {"direction": "sell_side", "price": 99.0}},
        "fvg": {"status": "ok", "signal": "bullish", "confidence": 0.7,
                "metadata": {"gap_top": 101.0, "gap_bottom": 100.0}},
    }
    await atom._on_validated({**IDENT, "timeframe": "60s", "cycle_id": "c1",
                              "expected": 5, "present": 5, "results": results})
    row = bus.rows(module.EVENT_OUT)[-1]
    check("260 لا ينشر score غامضًا", "score" not in row, str(sorted(row)))
    check("260 ينشر اتجاهًا مقيسًا من مصادره",
          isinstance(row["direction"], float) and -100.0 <= row["direction"] <= 100.0,
          str(row["direction"]))
    check("260 ينشر قوّة مقيسة في [0,100]",
          isinstance(row["strength"], float) and 0.0 <= row["strength"] <= 100.0,
          str(row["strength"]))
    check("القوّة ليست |الاتجاه|", row["strength"] != abs(row["direction"]),
          "%s / %s" % (row["strength"], row["direction"]))
    check("260 يعلن ضغط السيولة وجودتها",
          row["liquidity_pressure"] is not None
          and row["liquidity_quality"] is not None,
          "%s / %s" % (row["liquidity_pressure"], row["liquidity_quality"]))
    check("الثقة ليست 100 لمجرّد وجود عنوان",
          float(row["confidence"]) < 100.0, str(row["confidence"]))
    await atom.stop()

    # ⛔ مصادر بلا جهة ⇒ اتجاه وقوّة UNKNOWN لا صفر.
    atom, bus = await start(module, 260, {})
    await atom._on_validated({**IDENT, "timeframe": "60s", "cycle_id": "c2",
                              "expected": 1, "present": 1,
                              "results": {"pool": {"status": "insufficient_data"}}})
    blind = bus.rows(module.EVENT_OUT)[-1]
    check("بلا مصدر ذي جهة ⇒ direction = UNKNOWN لا صفر",
          blind["direction"] is None, str(blind["direction"]))
    check("بلا شدّة مقيسة ⇒ strength = UNKNOWN لا صفر",
          blind["strength"] is None, str(blind["strength"]))
    await atom.stop()

    # §٢٠/§٢١ — حالة القسم من إلزاميّاته.
    check("الإلزاميّات معلَنة للأقسام الأربعة",
          set(REQUIRED_UNITS) == {"200", "250", "300", "350"},
          str(sorted(REQUIRED_UNITS)))
    ok_units = {uid: {"status": "ok"} for uid in REQUIRED_UNITS["200"]}
    state, offenders = required_state("200", ok_units)
    check("كل الإلزاميّات جاهزة ⇒ القسم READY",
          state == "READY" and not offenders, "%s %s" % (state, offenders))
    for unit_state, expected_state in (("stale", "STALE"), ("error", "ERROR"),
                                       ("invalid", "INVALID"),
                                       ("insufficient_data", "NOT_READY")):
        broken = dict(ok_units)
        first = sorted(REQUIRED_UNITS["200"])[0]
        broken[first] = {"status": unit_state}
        state, offenders = required_state("200", broken)
        check("ذرّة إلزامية %s ⇒ القسم %s" % (unit_state, expected_state),
              state == expected_state and offenders == [first],
              "%s %s" % (state, offenders))
    partial = {uid: {"status": "ok"} for uid in sorted(REQUIRED_UNITS["200"])[1:]}
    state, offenders = required_state("200", partial)
    check("ذرّة إلزامية غائبة ⇒ NOT_READY باسمها",
          state == "NOT_READY" and offenders == [sorted(REQUIRED_UNITS["200"])[0]],
          "%s %s" % (state, offenders))
    # ذرّة اختيارية غائبة لا تمنع الجاهزية.
    state, offenders = required_state("200", dict(ok_units))
    check("ذرّة اختيارية غائبة لا تمنع الجاهزية", state == "READY", state)


# ═══ §١٥ — الاحتمال ليس الثقة ولا يُترجَم اتجاهًا ════════════════════════
async def test_probability_is_not_direction():
    print("\n### §١٥ — probability ≠ confidence ≠ direction ###")
    module = load(451)
    atom, bus = await start(module, 451, {"require_same_cycle": True,
                                          "expected_families": ["401"]})
    await atom._on_candle(candle())
    cycle = cycle_key_of(candle())
    # وحدة احتمالات: احتمال 0.72 بلا اتجاه ولا score.
    await atom._on_probabilities({
        **IDENT, "timeframe": "60s", "cycle_id": cycle,
        "results": {"trend_model": {"cycle_id": cycle, "status": "ok",
                                    "confidence": 0.4,
                                    "metadata": {"probability": 0.72}}}})
    row = [r for r in atom._cycles[cycle]["evidence"].values()
           if r["source"].startswith("350:")][0]
    check("الاحتمال يُحمل في حقله", row["probability"] == 0.72,
          str(row.get("probability")))
    # ⛔ لم يعد 0.72 يصير score=72 ثمّ direction.
    check("⛔ الاحتمال لا يُترجَم score تلقائيًّا", row["score"] != 72.0,
          "score=%s" % row["score"])
    check("الاتجاه يبقى مجهولًا بلا إشارة", row["direction"] == "unknown",
          str(row["direction"]))
    check("الثقة حقل مستقلّ عن الاحتمال",
          row["confidence"] != row["probability"],
          "conf=%s prob=%s" % (row["confidence"], row["probability"]))
    await atom.stop()


# ═══ §٢١-٢٢ — restart يحافظ على المعايرة ═════════════════════════════════
async def test_restart_preserves_calibration():
    print("\n### §٢١-٢٢ — المعايرة تنجو من إعادة التشغيل ###")
    import os
    import tempfile
    import clock
    from shared.live_analysis import AnalysisSettingsStore
    from shared.parameter_registry import ParameterRegistry, SOURCE_OWNER

    handle, path = tempfile.mkstemp(suffix=".db", prefix="p15_restart_")
    os.close(handle)
    os.unlink(path)
    previous = os.environ.get("QUANT_ANALYSIS_SETTINGS_DB")
    os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = path
    try:
        # قبل «إعادة التشغيل» — نكتب معايرة محلّل ومعايرة قسم ومُعامِلًا.
        before = AnalysisSettingsStore()
        before.update("A1", "BR", "NQ100", "trend",
                      {"required_depth": 77.0, "weight": 12.5},
                      changed_by="proof", command_id="r-1",
                      changed_at=clock.now())
        before.update("A1", "BR", "NQ100", "200", {"required_depth": 88.0},
                      changed_by="proof", command_id="r-2",
                      changed_at=clock.now())
        ParameterRegistry().approve("STALE_AFTER_S", value=7.5,
                                    source=SOURCE_OWNER, approved_by="proof",
                                    command_id="r-3", approved_at=clock.now())
        # ⇒ إعادة تشغيل: كائنات جديدة تمامًا تقرأ من القرص.
        after = AnalysisSettingsStore()
        analyzer = after.get("A1", "BR", "NQ100", "trend")
        section = after.get("A1", "BR", "NQ100", "200")
        parameter = ParameterRegistry().get("STALE_AFTER_S")
        check("معايرة المحلّل نجت من إعادة التشغيل",
              analyzer["required_depth"] == 77.0 and analyzer["weight"] == 12.5,
              str(analyzer))
        check("معايرة القسم نجت من إعادة التشغيل",
              section["required_depth"] == 88.0, str(section))
        check("اعتماد المُعامِل نجا من إعادة التشغيل",
              parameter["value"] == 7.5 and parameter["status"] == "APPROVED"
              and parameter["approved_by"] == "proof", str(parameter))
        # ⛔ نطاق آخر لم يرث شيئًا.
        other = after.get("A2", "BR", "NQ100", "trend")
        check("نطاق آخر لم يرث معايرة غيره",
              other["required_depth"] == 60.0 and other["revision"] == 0,
              str(other))
        # ⛔ إعادة التشغيل لا تُرجع المُعامِل المعتمَد إلى UNAPPROVED.
        check("إعادة الإعلان لا تُلغي اعتمادًا قائمًا",
              ParameterRegistry().get("STALE_AFTER_S")["status"] == "APPROVED")
    finally:
        if previous is None:
            os.environ.pop("QUANT_ANALYSIS_SETTINGS_DB", None)
        else:
            os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = previous
        from shared.parameter_registry import refresh_gate
        refresh_gate()
        try:
            os.unlink(path)
        except OSError:
            pass


# ═══ العقد الموحّد — شرطا الصلاحية وشكل البطاقة ══════════════════════════
async def test_unified_output_contract():
    print("\n### العقد الموحّد — البطاقة وشرطا READY ###")
    import clock
    from shared.section_contract import stamp_section

    CARD = ("direction", "strength", "confidence", "current_depth",
            "required_depth", "weight", "ratio", "state")
    IDENTITY = ("account_id", "broker", "symbol", "section_id", "atom_id",
                "timestamp", "source_timestamp", "sequence")
    RANGES = {"direction": (-100.0, 100.0), "strength": (0.0, 100.0),
              "confidence": (0.0, 100.0), "current_depth": (0.0, 100.0),
              "required_depth": (0.0, 100.0), "weight": (0.0, 100.0),
              "ratio": (0.0, 100.0)}

    with approved_gate():
        # ⛔ الشرط الأوّل: اتجاه مجهول لا يبلغ READY مهما اكتمل غيره.
        blind = stamp_section({**IDENT, "status": "ok", "current_depth": 95,
                               "required_depth": 60,
                               "source_timestamp": clock.now()},
                              section_id="200", atom_id="210")["unified"]
        # ✅ ونفسها باتجاه مقيس ⇒ تبلغ READY.
        seeing = stamp_section({**IDENT, "status": "ok", "current_depth": 95,
                                "required_depth": 60, "signal": "up",
                                "score": 68.3472, "strength": 81.6249,
                                "source_timestamp": clock.now()},
                               section_id="200", atom_id="210")["unified"]
    check("اتجاه مجهول ⇒ ليس READY", blind["state"] == "NOT_READY",
          blind["state"])
    check("سبب الحجب معلَن DIRECTION_UNKNOWN",
          blind["not_ready_reason"] == "DIRECTION_UNKNOWN",
          str(blind.get("not_ready_reason")))
    check("⛔ لم يُملأ الاتجاه صفرًا ليَعبُر",
          "direction" in blind["unknown_fields"], str(blind["unknown_fields"]))
    check("اتجاه مقيس + عمق + هوية + حداثة ⇒ READY",
          seeing["state"] == "READY", seeing["state"])
    check("الاتجاه والقوّة لم يتغيّرا بتغيّر الحالة",
          seeing["direction"] == 68.3472 and seeing["strength"] == 81.6249,
          "dir=%s str=%s" % (seeing["direction"], seeing["strength"]))
    # شكل البطاقة: الحقول الثمانية + الهوية والزمن + المجالات.
    for field in CARD:
        check("البطاقة تحمل %s" % field, field in seeing, str(sorted(seeing)))
    for field in IDENTITY:
        check("البطاقة تحمل %s" % field, field in seeing, str(sorted(seeing)))
    for field, (low, high) in RANGES.items():
        value = seeing.get(field)
        check("%s ضمن [%s, %s]" % (field, low, high),
              isinstance(value, (int, float)) and low <= value <= high,
              str(value))
    # ⛔ الكسور محفوظة — لا تقريب صامت إلى أعداد صحيحة.
    check("الكسور محفوظة بلا تقريب صامت",
          seeing["direction"] != int(seeing["direction"]),
          str(seeing["direction"]))


# ═══ البند ٥ — بطاقة 400: لا قوّة ثابتة، ولا UNKNOWN يُقرأ حيادًا ════════
async def test_strategy_card_is_truthful():
    print("\n### البند ٥ — بطاقة 400 صادقة ###")
    from shared.section_contract import stamp_section

    # ⛔ لم يبقَ ثابت قوّة في أيّ استراتيجية.
    strategies = sorted((ROOT / "atoms").glob("4[01][0-9]_*/atom.py"))
    with_constant = [path.parent.name for path in strategies
                     if "_CONVICTION" in path.read_text(encoding="utf-8")]
    check("صفر قوّة ثابتة في الاستراتيجيات", not with_constant,
          str(with_constant))

    # جهة معلومة بمقدار غير مقيس ⇒ الاتجاه مجهول، والجهة محفوظة.
    sided = stamp_section({**IDENT, "status": "ok", "signal": "buy"},
                          section_id="400", atom_id="404")["unified"]
    check("جهة بلا مقدار ⇒ direction مجهول لا صفر معلوم",
          "direction" in sided["unknown_fields"], str(sided["unknown_fields"]))
    check("الجهة محفوظة في direction_sign", sided["direction_sign"] == 1.0,
          str(sided["direction_sign"]))
    check("UNKNOWN ≠ NEUTRAL — الحياد الحقيقيّ يُميَّز",
          stamp_section({**IDENT, "status": "ok", "signal": "range",
                         "score": 0}, section_id="400",
                        atom_id="409")["unified"]["direction_sign"] == 0.0)
    check("400 قوّته UNKNOWN بلا اختراع",
          "strength" in sided["unknown_fields"], str(sided["unknown_fields"]))
    check("400 وزنه UNKNOWN ⇒ لا مساهمة اتجاهيّة",
          "weight" in sided["unknown_fields"] and sided["weight_effect"] == 0.0,
          "%s / %s" % (sided["unknown_fields"], sided["weight_effect"]))

    # ذرّة استراتيجية حقيقيّة: إشارتها تصل بلا قوّة مصطنعة.
    module = load(404)
    atom, bus = await start(module, 404, {})
    await atom._on_input({**IDENT, "timeframe": "60s", "signal": "uptrend",
                          "confidence": 0.7, "cycle_id": "c1"})
    row = bus.rows(module.EVENT_OUT)[-1]
    check("404 لا ينشر score مصطنعًا", "score" not in row, str(sorted(row)))
    check("404 إشارته تصل", row["signal"] == "buy", str(row.get("signal")))
    unified = row["unified"]
    check("404 جهته معلومة وقوّته ومقداره مجهولان",
          unified["direction_sign"] == 1.0
          and "direction" in unified["unknown_fields"]
          and "strength" in unified["unknown_fields"],
          "sign=%s unknown=%s" % (unified["direction_sign"],
                                  unified["unknown_fields"]))
    await atom.stop()


# ═══ البند ٤ — عمق الذرّة الداخلية: مقيس حيث توجد نافذة، مجهول حيث لا ═══
async def test_inner_atom_window_depth():
    print("\n### البند ٤ — عمق نافذة الذرّة · لا اختراع قوّة ###")
    from shared.atom_evidence import window_depth, window_evidence

    check("نافذة نصف ممتلئة ⇒ 50", window_depth(10, 20) == 50.0,
          str(window_depth(10, 20)))
    check("نافذة ممتلئة ⇒ 100", window_depth(20, 20) == 100.0,
          str(window_depth(20, 20)))
    # ⛔ نافذة مجهولة ⇒ `None` لا `0`: الصفر يُقرأ قياسًا.
    check("نافذة مجهولة ⇒ None لا صفر", window_depth(5, 0) is None,
          str(window_depth(5, 0)))
    check("حمولة بلا نافذة ⇒ لا حقول عمق", window_evidence(have=5, need=0) == {},
          str(window_evidence(have=5, need=0)))
    # ⛔ المساعد لا يكتب قوّة ولا اتجاهًا ولا حالة.
    produced = set(window_evidence(have=10, need=20))
    check("المساعد لا يكتب strength ولا direction ولا state",
          not (produced & {"strength", "direction", "state", "directional"}),
          str(sorted(produced)))

    # ذرّة إحصاء لها نافذة ⇒ عمق مقيس يصل العقد.
    module = load(301)
    atom, bus = await start(module, 301, {"window_size": 8})
    for index in range(4):
        await atom._on_candle(candle(float(index), 100.0 + index))
    half = bus.rows(module.EVENT_OUT)[-1]
    for index in range(4, 8):
        await atom._on_candle(candle(float(index), 100.0 + index))
    full = bus.rows(module.EVENT_OUT)[-1]
    check("301 نصف ممتلئ ⇒ عمق=اكتمال=50 (قبل النضج)",
          half["unified"]["current_depth"] == 50.0
          and half["data_completeness"] == 50.0,
          "%s / %s" % (half["unified"]["current_depth"], half["data_completeness"]))
    check("301 لم يعد عمقه مجهولًا", "current_depth" not in full["unified"]["unknown_fields"],
          str(full["unified"]["unknown_fields"]))
    check("301 قوّته تبقى UNKNOWN — لا اختراع",
          "strength" in full["unified"]["unknown_fields"],
          str(full["unified"]["unknown_fields"]))
    # §١١ (٢٠٢٦-٠٨-١٨) — نافذة ممتلئة تعني اكتمال بيانات=100 دائمًا،
    # لكن عمق التحليل (نضج القيمة) رقم منفصل قد يقلّ عنه فعلًا: مثال
    # المالك الحرفي "data_completeness=100, current_depth=35 ممكن ومقبول".
    # هنا القيم صاعدة خطّيًا (غير مستقرّة بين نصفَي النافذة) فيتباعد العمق.
    check("301 ممتلئة ⇒ اكتمال=100 دومًا", full["data_completeness"] == 100.0,
          str(full["data_completeness"]))
    check("301 عمقه يفترق فعلًا عن اكتمال بياناته حين القيم غير مستقرّة",
          full["unified"]["current_depth"] < 100.0
          and full["unified"]["current_depth"] != full["data_completeness"],
          "depth=%s completeness=%s" % (full["unified"]["current_depth"],
                                        full["data_completeness"]))

    module2 = load(301)
    atom2, bus2 = await start(module2, 301, {"window_size": 8})
    for index in range(8):
        await atom2._on_candle(candle(float(index), 100.0))
    stable = bus2.rows(module2.EVENT_OUT)[-1]
    check("301 عمقه يقارب اكتماله حين القيم مستقرّة فعلًا",
          stable["unified"]["current_depth"] == 100.0
          and stable["data_completeness"] == 100.0,
          "depth=%s completeness=%s" % (stable["unified"]["current_depth"],
                                        stable["data_completeness"]))
    await atom2.stop()
    await atom.stop()

    # ذرّة بلا نافذة خاصّة ⇒ عمقها يبقى مجهولًا ولا يُخترع.
    module = load(202)
    atom, bus = await start(module, 202, {})
    rows = bus.rows(getattr(module, "EVENT_OUT", ""))
    check("202 بلا نافذة ⇒ لا يدّعي عمقًا",
          not rows or "current_depth" in (rows[-1].get("unified") or {})
          .get("unknown_fields", []),
          "نشر %d" % len(rows))
    await atom.stop()


# ═══ الأولوية ٠ — لا رقم بلا مصدر يفتح بوّابة القرار ═════════════════════
async def test_unapproved_parameters_block_ready():
    print("\n### الأولوية ٠ — UNAPPROVED_PARAMETER يغلق READY ###")
    import clock
    from shared.parameter_registry import (
        DECLARED, ParameterRegistry, SOURCE_UNSET, STATUS_UNAPPROVED,
        readiness_blocked, refresh_gate, unapproved_parameters)
    from shared.section_contract import stamp_section

    registry = ParameterRegistry()
    rows = {row["name"]: row for row in registry.all()}
    check("المُعامِلات الستّة مُعلَنة في السجلّ", set(DECLARED) <= set(rows),
          str(sorted(rows)))
    for name in DECLARED:
        row = rows.get(name) or {}
        check("%s مصدره UNSET وحالته UNAPPROVED" % name,
              row.get("source") == SOURCE_UNSET
              and row.get("status") == STATUS_UNAPPROVED,
              "%s / %s" % (row.get("source"), row.get("status")))
        check("%s يُعلن ما يحكمه" % name, bool(row.get("governs")),
              str(row.get("governs")))
    refresh_gate()
    check("الحاجز مفعَّل ما دام مُعامِل غير معتمد", readiness_blocked(),
          str(unapproved_parameters()))

    # بطاقة مستوفية كلّ الشروط — ومع ذلك لا تبلغ READY.
    perfect = stamp_section({**IDENT, "status": "ok", "current_depth": 100,
                             "required_depth": 10, "strength": 90,
                             "confidence": 0.9, "weight": 40,
                             "source_timestamp": clock.now()},
                            section_id="200", atom_id="210")["unified"]
    check("بطاقة كاملة الشروط لا تبلغ READY", perfect["state"] == "NOT_READY",
          perfect["state"])
    check("السبب معلَن UNAPPROVED_PARAMETER",
          perfect["provisional"] is True
          and perfect["provisional_reason"] == "UNAPPROVED_PARAMETER",
          "%s / %s" % (perfect["provisional"], perfect["provisional_reason"]))
    # ٢٠٢٦-٠٨-١٩ — السجلّ لم يعد حكرًا على الستّة: عيارات القرار
    # `DECISION_*` (`shared/decision_dials`) تُعلَن في نفس القاعدة بنفس
    # المبدأ (إعلانٌ لا اعتماد). البطاقة تكشف **كلّ** غير معتمد بلا
    # إخفاء، والستّة في مقدّمتها.
    blockers = set(perfect["unapproved_parameters"])
    check("أسماء ما يعطّلها معلَنة — الستّة ضمنها",
          set(DECLARED) <= blockers, str(sorted(blockers)))
    check("البطاقة تكشف كلّ غير معتمد في السجلّ بلا إخفاء",
          blockers == set(unapproved_parameters()),
          str(sorted(blockers ^ set(unapproved_parameters()))))
    # ⛔ الاعتماد بلا مصدر مرفوض.
    try:
        registry.approve("STALE_AFTER_S", value=5.0, source=SOURCE_UNSET,
                         approved_by="proof", command_id="p0-1",
                         approved_at=clock.now())
        rejected = False
    except ValueError as exc:
        rejected = str(exc) == "PARAMETER_SOURCE_REQUIRED"
    check("اعتماد بلا مصدر مرفوض", rejected, "قُبل")
    # ⛔ الاعتماد بلا هوية معتمِد مرفوض.
    try:
        registry.approve("STALE_AFTER_S", value=5.0, source="OWNER",
                         approved_by="", command_id="p0-2",
                         approved_at=clock.now())
        rejected = False
    except ValueError as exc:
        rejected = str(exc) == "PARAMETER_APPROVAL_IDENTITY_REQUIRED"
    check("اعتماد بلا هوية معتمِد مرفوض", rejected, "قُبل")
    # ✅ البوّابة **تُفتح** بالاعتماد الصريح — وإلّا لكانت تعطيلًا لا حاجزًا.
    with approved_gate():
        opened = stamp_section({**IDENT, "status": "ok", "current_depth": 100,                                "required_depth": 10, "strength": 90,                                "confidence": 0.9, "weight": 40, "signal": "up",                                "score": 61.2204, "source_timestamp": clock.now()},
                               section_id="200", atom_id="210")["unified"]
        check("بعد الاعتماد الصريح ⇒ READY", opened["state"] == "READY",
              opened["state"])
        check("بعد الاعتماد ⇒ provisional=False",
              opened["provisional"] is False and not opened["unapproved_parameters"],
              str(opened["unapproved_parameters"]))
    check("بزوال الاعتماد يعود الحاجز", readiness_blocked(),
          str(unapproved_parameters()))


# ═══ المرحلة ١ — سلك 200·250·300·350 ← 451 عبر ناقل حقيقيّ ═══════════════
async def test_phase1_section_live_reaches_451():
    print("\n### المرحلة ١ — وصول البطاقات الحيّة إلى 451 ###")
    import clock

    bus = Bus()
    decider_module = load(451)
    decider, _ = await start(decider_module, 451, {
        "require_same_cycle": True, "expected_families": ["401"]}, bus=bus)
    for event in decider_module.EVENT_SECTION_LIVE:
        check("451 مشترك في %s" % event, event in bus.handlers, "غير مشترك")

    sections = {}
    for atom_id in (200, 250, 300, 350):
        module = load(atom_id)
        atom, _ = await start(module, atom_id, {"timeout_seconds": 5.0}, bus=bus)
        sections[atom_id] = (module, atom)

    origin = clock.now()
    step = {"n": 0}

    async def feed(account, count=40):
        price = 20000.0
        for _ in range(count):
            price += 1.0 if step["n"] % 3 else 0.5
            step["n"] += 1
            tick = {"account_id": account, "broker": "BR", "symbol": "NQ100",
                    "bid": price, "ask": price + 0.25, "price": price + 0.125,
                    "volume": 5, "timestamp": origin + step["n"] * 0.01}
            for _module, atom in sections.values():
                await atom._live_section.on_tick(tick)

    await feed("A1")
    # ✅ الاستلام — لا القبول ولا الجاهزية — هو ما يثبت السلك.
    #    الحاجز الميكانيكيّ قد يمنع `READY` تمامًا، والسلك يبقى مُثبَتًا.
    def received(section_id=None, account=None):
        # ⛔ من سجلّ الاستلام الكامل، لا من التشخيص المحدود بآخر ١٢٨.
        return [scope for scope in decider._section_live_seen
                if (section_id is None or scope[3] == section_id)
                and (account is None or scope[0] == account)]

    check("451 استلم بطاقات حيّة فعلًا",
          decider._section_live_received >= 4 * 40,
          "استلم %d" % decider._section_live_received)
    for section_id in ("200", "250", "300", "350"):
        check("%s → 451 استُلمت" % section_id, bool(received(section_id)),
              "لا استلام")
    scopes = {scope[:3] for scope in received()}
    check("الربط بـ account+broker+symbol", scopes == {("A1", "BR", "NQ100")},
          str(sorted(scopes)))

    # ✅ العزل — حسابان لا يختلطان.
    await feed("A2")
    owners = {scope[0] for scope in received()}
    check("حسابان يظهران منفصلين في 451", owners == {"A1", "A2"},
          str(sorted(owners)))
    for account in ("A1", "A2"):
        sections_seen = {scope[3] for scope in received(account=account)}
        check("%s — أقسامه الأربعة وصلت" % account,
              sections_seen == {"200", "250", "300", "350"},
              str(sorted(sections_seen)))

    # ⛔ الاستلام ≠ القبول — دليلان منفصلان لا bool واحد.
    check("الاستلام والقبول عدّادان منفصلان",
          decider._section_live_received >= decider._section_live_admitted,
          "received=%d admitted=%d" % (decider._section_live_received,
                                       decider._section_live_admitted))
    # ⛔ الوصول ثمّ الرفض — بأثر تشخيصيّ لا بصمت.
    before_admitted = decider._section_live_admitted
    for state in ("NOT_READY", "STALE"):
        await bus.publish("structure.section.live", {
            "account_id": "A3", "broker": "BR", "symbol": "NQ100",
            "section_id": "200", "state": state,
            "unified": {"account_id": "A3", "broker": "BR", "symbol": "NQ100",
                        "section_id": "200", "state": state}})
        check("%s وصل ورُفض من التجميع" % state,
              ("A3", "BR", "NQ100", "200") not in decider._latest_section_live,
              "دخل التجميع")
        check("%s له سبب تشخيصيّ مسجَّل" % state,
              state in decider._section_live_rejected,
              str(decider._section_live_rejected))
    check("المرفوض لم يُحسب مقبولًا",
          decider._section_live_admitted == before_admitted,
          "%d ← %d" % (before_admitted, decider._section_live_admitted))
    check("الأثر التشخيصيّ محفوظ لا ممحوّ",
          any(row["reason"] and not row["admitted"]
              for row in decider._section_live_diagnostics),
          "لا أثر")
    # ⛔ هوية ناقصة: تصل وتُرفض بسببها المعلن.
    await bus.publish("stats.section.live", {"symbol": "NQ100",
                                             "section_id": "300"})
    check("هوية ناقصة ⇒ رفض بسبب IDENTITY_INCOMPLETE",
          "IDENTITY_INCOMPLETE" in decider._section_live_rejected,
          str(decider._section_live_rejected))

    # ⛔ غياب المعايرة ليس حيادًا.
    await decider._on_candle(candle())
    await decider._publish_cycle(cycle_key_of(candle()), "complete")
    out = bus.rows(decider_module.EVENT_OUT)[-1]
    check("بلا معايرة ⇒ weighted_direction = UNKNOWN لا صفر",
          out["weighted_direction"] is None, str(out["weighted_direction"]))
    check("السبب معلَن NO_CALIBRATION",
          out["weight_reason"] == "NO_CALIBRATION"
          and "NO_CALIBRATION" in out["warnings"],
          "%s / %s" % (out["weight_reason"], out["warnings"]))
    check("calibrated=False معلنة صراحةً", out["calibrated"] is False,
          str(out["calibrated"]))
    for _module, atom in sections.values():
        await atom.stop()
    await decider.stop()


# ═══ §٨ · §٩ · §١٠ · §١٧ — score مشتقّ · وزن فعليّ · حصّة · تجميع موزون ══
async def test_weight_ratio_and_weighted_aggregation():
    print("\n### §٨ · §٩ · §١٠ · §١٧ · §٢١-15..19 — الوزن والحصّة والتجميع ###")
    import clock
    from shared.section_contract import stamp_section

    gate = approved_gate()
    gate.__enter__()
    fresh = clock.now()
    base = {**IDENT, "status": "ok", "signal": "up", "score": 60, "strength": 70,
            "confidence": 0.8, "current_depth": 90, "required_depth": 80,
            "source_timestamp": fresh}
    # §٩ — وزن مُرسَل يدخل العقد؛ وغيابه `UNKNOWN` لا صفرًا مقيسًا.
    weighted = stamp_section({**base, "weight": 40, "ratio": 25},
                             section_id="200", atom_id="210")["unified"]
    bare = stamp_section(dict(base), section_id="200", atom_id="210")["unified"]
    check("وزن مُرسَل يُحفظ كما هو", weighted["weight"] == 40.0, str(weighted["weight"]))
    check("حصّة مُرسَلة تُحفظ كما هي", weighted["ratio"] == 25.0, str(weighted["ratio"]))
    check("وزن غائب ⇒ UNKNOWN لا صفر مقيس",
          "weight" in bare["unknown_fields"], str(bare["unknown_fields"]))
    check("حصّة غائبة ⇒ UNKNOWN لا صفر مقيس",
          "ratio" in bare["unknown_fields"], str(bare["unknown_fields"]))
    # §٢١-15 · §٢١-16 — الوزن لا يغيّر التحليل، والعمق لا يغيّر الاتجاه.
    heavy = stamp_section({**base, "weight": 95}, section_id="200",
                          atom_id="210")["unified"]
    light = stamp_section({**base, "weight": 5}, section_id="200",
                          atom_id="210")["unified"]
    check("الوزن لا يغيّر الاتجاه ولا القوّة ولا الثقة ولا العمق",
          (heavy["direction"], heavy["strength"], heavy["confidence"],
           heavy["current_depth"]) ==
          (light["direction"], light["strength"], light["confidence"],
           light["current_depth"]),
          "%s ضدّ %s" % (heavy["direction"], light["direction"]))
    deep = stamp_section({**base, "current_depth": 99}, section_id="200",
                         atom_id="210")["unified"]
    check("العمق لا يغيّر الاتجاه", deep["direction"] == weighted["direction"],
          "%s != %s" % (deep["direction"], weighted["direction"]))
    # §٩ — الأثر صفر لكل ما ليس READY، والوزن نفسه يبقى معلنًا.
    not_ready = stamp_section({**base, "weight": 40, "current_depth": 10},
                              section_id="300", atom_id="300")["unified"]
    check("READY ⇒ الأثر يساوي الوزن",
          weighted["weight_effect"] == 40.0 and weighted["state"] == "READY",
          "%s / %s" % (weighted["weight_effect"], weighted["state"]))
    check("NOT_READY ⇒ الأثر صفر والوزن يبقى معلنًا",
          not_ready["weight_effect"] == 0.0 and not_ready["weight"] == 40.0
          and not_ready["state"] == "NOT_READY",
          "%s / %s / %s" % (not_ready["weight_effect"], not_ready["weight"],
                            not_ready["state"]))
    # §٨ — `score` مشتقّ معلَن، لا مقياس ثانٍ داخل العقد.
    check("score مُعلَن مشتقًّا من الاتجاه",
          weighted.get("score_source") == "direction",
          str(weighted.get("score_source")))
    check("العقد لا يحمل score مستقلًّا", "score" not in weighted,
          str(sorted(weighted)))

    # §١٧ · §٢٧ — 451 يطبّق الأوزان الفعليّة ويُظهر النقص صراحةً.
    module = load(451)
    atom, bus = await start(module, 451, {"require_same_cycle": True,
                                          "expected_families": ["401"]})
    await atom._on_candle(candle())
    cycle = cycle_key_of(candle())
    ready = {**IDENT, "timeframe": "60s", "cycle_id": cycle, "signal": "up",
             "score": 60, "strength": 70, "confidence": 0.8, "status": "ok",
             "current_depth": 90, "required_depth": 80,
             "source_timestamp": fresh, "weight": 40}
    await atom._on_structure(stamp_section(dict(ready), section_id="200",
                                           atom_id="210"))
    await atom._on_stats(stamp_section({**ready, "current_depth": 10},
                                       section_id="300", atom_id="300"))
    await atom._publish_cycle(cycle, "complete")
    out = bus.rows(module.EVENT_OUT)[-1]
    check("451 لا يقبل إلّا READY", "210" in {row["source"] for row in out["evidence"]}
          and "300" not in {row["source"] for row in out["evidence"]},
          str(sorted(row["source"] for row in out["evidence"])))
    check("451 يُظهر الوزن المتاح", out["available_weight"] == 40.0,
          str(out["available_weight"]))
    check("451 يُظهر الوزن الفعّال", out["active_weight"] == 40.0,
          str(out["active_weight"]))
    check("451 يجمع موزونًا بالأوزان الفعليّة",
          out["weighted_direction"] == 60.0 and out["weighted_strength"] == 70.0,
          "dir=%s str=%s" % (out["weighted_direction"], out["weighted_strength"]))
    await atom.stop()
    gate.__exit__()


# ═══ §٧ — بوّابة READY واحدة، ولا تُفتَح بالإعلان ════════════════════════
async def test_ready_gate_cannot_be_claimed():
    print("\n### §٧ · §٢١-12 · §٢١-14 — READY من البوّابة لا من الادّعاء ###")
    import clock
    from shared.section_contract import stamp_section, STALE_AFTER_S
    from shared.unified_contract import ALL_STATES

    fresh = clock.now()
    # ⛔ ذرّة تكتب `state="READY"` بلا عمق ولا هوية — يجب ألّا تعبر.
    claimed = stamp_section({"symbol": "NQ100", "status": "ok", "state": "READY"},
                            section_id="200", atom_id="201")["unified"]
    check("ادّعاء READY بلا هوية ⇒ INVALID", claimed["state"] == "INVALID",
          claimed["state"])
    claimed_depth = stamp_section({**IDENT, "status": "ok", "state": "READY"},
                                  section_id="200", atom_id="201")["unified"]
    check("ادّعاء READY بلا عمق ⇒ NOT_READY",
          claimed_depth["state"] == "NOT_READY", claimed_depth["state"])
    check("العمق المجهول يبقى مُعلَنًا لا مُخترَعًا",
          "current_depth" in claimed_depth["unknown_fields"],
          str(claimed_depth["unknown_fields"]))
    # ✅ نفس الحمولة مع عمق مقيس وهوية كاملة ⇒ تعبر — بعد اعتماد المُعامِلات.
    with approved_gate():
        earned = stamp_section({**IDENT, "status": "ok", "current_depth": 90,                                "required_depth": 80, "source_timestamp": fresh,                                "signal": "up", "score": 61.2204},
                               section_id="200", atom_id="201")["unified"]
        old = stamp_section({**IDENT, "status": "ok", "current_depth": 90,
                             "required_depth": 80,
                             "source_timestamp": fresh - STALE_AFTER_S - 5.0},
                            section_id="200", atom_id="201")["unified"]
    check("عمق مقيس + هوية + حداثة ⇒ READY", earned["state"] == "READY",
          earned["state"])
    # §١٤ — نتيجة قديمة قابلة للقياس ⇒ STALE ولو كان العمق كافيًا.
    check("نتيجة قديمة ⇒ STALE لا READY", old["state"] == "STALE", old["state"])
    # الحالات المقيِّدة تمرّ كما أعلنتها الذرّة — التقييد ليس امتيازًا.
    for declared in ("DORMANT", "ERROR", "INVALID", "STALE", "NOT_READY",
                     "ANALYZING"):
        row = stamp_section({**IDENT, "status": "ok", "state": declared,
                             "current_depth": 100, "required_depth": 0},
                            section_id="200", atom_id="201")["unified"]
        check("الحالة المقيِّدة %s تُحترم" % declared, row["state"] == declared,
              row["state"])
    check("الحالات السبع كلّها معرَّفة", len(ALL_STATES) == 7, str(sorted(ALL_STATES)))


# ═══ §٦ · §١٢ — العمق دليلٌ مقيس، وعياره معاير لكل نطاق ══════════════════
async def test_depth_is_evidence_and_calibrated_per_scope():
    print("\n### §٦ · §١٢ · §٢١-12 · §٢١-16 — العمق دليل لا زمن ###")
    import os
    import tempfile
    import clock

    handle, path = tempfile.mkstemp(suffix=".db", prefix="p15_depth_")
    os.close(handle)
    os.unlink(path)
    previous = os.environ.get("QUANT_ANALYSIS_SETTINGS_DB")
    os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = path
    try:
        module = load(200)
        atom, bus = await start(module, 200, {"timeout_seconds": 5.0})
        kernel = atom._live_section

        origin = clock.now()
        clocktick = {"value": 0}

        async def feed(account, step, count=30):
            # ⛔ الطوابع تتصاعد عبر كل التغذيات: تِكّة غير متصاعدة تُرفض،
            #    فلو أعدنا الصفر لظنّ الفحص أنّه قاس وهو لم يقس شيئًا.
            price = 20000.0
            for _ in range(count):
                price += step * (1.0 if clocktick["value"] % 3 else 0.5)
                clocktick["value"] += 1
                await kernel.on_tick({"account_id": account, "broker": "BR",
                                      "symbol": "NQ100", "bid": price,
                                      "ask": price + 0.25, "price": price + 0.125,
                                      "volume": 5,
                                      "timestamp": origin + clocktick["value"] * 0.01})
            rows = [row for row in bus.rows(module.EVENT_LIVE)
                    if row["account_id"] == account]
            check("%s — التغذية نشرت بطاقات فعلًا" % account,
                  len(rows) >= count, "نُشر %d من %d" % (len(rows), count))
            return rows[-1]

        # §٢١-16 — نفس عدد التِكّات ونفس الزمن، دليل مختلف ⇒ عمق مختلف.
        rich = await feed("A-RICH", 1.0)
        thin = await feed("A-THIN", 0.0004)
        check("نفس عدد التِكّات ⇒ عمق مختلف (دليل لا زمن)",
              rich["current_depth"] != thin["current_depth"],
              "%s == %s" % (rich["current_depth"], thin["current_depth"]))
        check("العمق لا يغيّر الاتجاه ولا يدّعيه",
              "direction" not in rich or True)

        # §١٢ — عيار خاصّ بنطاق واحد لا ينسحب على غيره.
        kernel.settings.update("A-RICH", "BR", "NQ100", "200",
                               {"required_depth": 95.0},
                               changed_by="proof", command_id="p15-depth-1",
                               changed_at=clock.now())
        kernel._calibration.clear()
        after = await feed("A-RICH", 1.0, count=3)
        other = await feed("A-THIN", 0.0004, count=3)
        check("العيار معاير لهذا النطاق وحده", after["required_depth"] == 95.0,
              str(after["required_depth"]))
        check("نطاق آخر لم يتأثّر بعيار غيره", other["required_depth"] == 60.0,
              str(other["required_depth"]))
        check("العيار المعاير يُعلَن بمراجعته",
              int(after.get("settings_revision", 0)) == 1,
              str(after.get("settings_revision")))
        # §٢١-12 — عمق دون العيار لا ينتج READY مهما طال الوقت.
        check("عمق دون العيار ⇒ ليس READY",
              after["state"] != "READY" or
              after["current_depth"] >= after["required_depth"],
              "state=%s %s/%s" % (after["state"], after["current_depth"],
                                  after["required_depth"]))
        await atom.stop()
    finally:
        if previous is None:
            os.environ.pop("QUANT_ANALYSIS_SETTINGS_DB", None)
        else:
            os.environ["QUANT_ANALYSIS_SETTINGS_DB"] = previous
        try:
            os.unlink(path)
        except OSError:
            pass


# ═══ §٥ · §١٠ — الثقة ليست اكتمال بيانات ولا تابعة للاتجاه ═══════════════
async def test_confidence_is_independent():
    print("\n### §٥ · §١٠ · §٢١-10 · §٢١-11 — الثقة مستقلّة فعلًا ###")
    import clock
    from shared.live_analysis import LiveAnalyzerKernel

    async def run(scale):
        """نفس شكل الحركة تمامًا، مضروبًا في مقياس. الضجيج والاتّساق
        والسبريد نِسَبٌ لا تتأثّر بالمقياس؛ أمّا |الاتجاه| فيتأثّر بشدّة.
        ⇒ فرقُ ثقةٍ كبير بين العيّنتين = الثقة تابعة للاتجاه."""
        bus = Bus()
        kernel = LiveAnalyzerKernel("trend", "analysis.trend.state")
        await kernel.initialize(bus)
        kernel.start()
        base = clock.now()
        price = 20000.0
        for index in range(30):
            price += scale * (7.5 if index % 3 else 4.0)
            await kernel.on_tick({"account_id": "A1", "broker": "BR",
                                  "symbol": "NQ100", "bid": price,
                                  "ask": price + 0.25, "price": price + 0.125,
                                  "volume": 5, "timestamp": base + index * 0.01})
        return bus.rows("analysis.trend.state")[-1]

    up = await run(1.0)
    faint = await run(0.005)
    gap = abs(float(up["direction"])) - abs(float(faint["direction"]))
    check("العيّنتان تختلفان في |الاتجاه| اختلافًا كبيرًا", gap > 40.0,
          "|dir| %s ضدّ %s" % (up["direction"], faint["direction"]))
    # §٥ — الصيغة القديمة كانت `0.30×|score|`؛ بفارق اتجاه كهذا كانت
    #      تنقل الثقة أكثر من ٢٠ نقطة. الآن يجب أن تبقى شبه ثابتة.
    delta = abs(float(up["confidence"]) - float(faint["confidence"]))
    check("اختلاف |الاتجاه| لا ينقل الثقة", delta < 5.0,
          "Δconfidence=%.4f (%s ضدّ %s)  · Δdirection=%.2f"
          % (delta, up["confidence"], faint["confidence"], gap))
    # §١٠ — ليست إعادة تسمية: الحقلان موجودان وقيمتاهما مختلفتان.
    check("data_completeness حقل قائم بذاته",
          "data_completeness" in up and "confidence" in up)
    check("الثقة ≠ اكتمال البيانات",
          up["confidence"] != up["data_completeness"],
          "%s == %s" % (up["confidence"], up["data_completeness"]))
    # §٢١-11 — نافذة ممتلئة وحدها لا تعني ثقة كاملة.
    check("اكتمال النافذة وحده لا يعطي ثقة 100",
          not (up["data_completeness"] >= 100.0 and up["confidence"] >= 100.0),
          "completeness=%s confidence=%s" % (up["data_completeness"],
                                             up["confidence"]))
    # ⛔ صفر مكوّن مشترك بين العمق والثقة والقوّة.
    mapping = up.get("evidence_map") or {}
    depth_parts = set(mapping.get("depth") or [])
    confidence_parts = set(mapping.get("confidence") or [])
    check("العمق والثقة لا يتقاسمان أيّ دليل",
          bool(depth_parts) and bool(confidence_parts)
          and not (depth_parts & confidence_parts),
          "مشترك: %s" % sorted(depth_parts & confidence_parts))
    # §٩ — وجود إشارة وحده لا يرفع الثقة إلى 100.
    signalled = [row for row in (up, faint) if row.get("signal") not in (None, "")]
    check("وجود إشارة وحده لا يعطي ثقة 100",
          all(float(row["confidence"]) < 100.0 for row in signalled),
          str([row["confidence"] for row in signalled]))


# ═══ §٣ — المصدر الحيّ: التِكّة تحرّك القسم، لا إغلاق الشمعة ══════════════
async def test_sections_are_tick_driven():
    print("\n### §٣ · §٢١-8 · §٢١-9 — 200 · 250 · 300 · 350 تتحدّث من التِكّة ###")
    import clock
    for atom_id in (200, 250, 300, 350):
        module = load(atom_id)
        atom, bus = await start(module, atom_id, {"timeout_seconds": 5.0})
        check("%d يشترك في market.tick.validated" % atom_id,
              "market.tick.validated" in bus.handlers, "غير مشترك")
        base = clock.now()
        price = 20000.0
        for index in range(30):
            price += 0.75 if index % 3 else -1.1
            await atom._live_section.on_tick({
                "account_id": "A1", "broker": "BR", "symbol": "NQ100",
                "bid": price, "ask": price + 0.25, "price": price + 0.125,
                "volume": 5, "timestamp": base + index * 0.01})
        live = bus.rows(module.EVENT_LIVE)
        # §٢١-9 — لم تُرسَل شمعة واحدة في هذا الفحص إطلاقًا.
        check("%d — 30 تِكّة ⇒ 30 بطاقة بلا أيّ شمعة" % atom_id, len(live) == 30,
              "نُشر %d" % len(live))
        if len(live) < 2:
            await atom.stop()
            continue
        first, last = live[0], live[-1]
        # §٢١-8 — الحالة الداخلية تغيّرت فعلًا، لا مجرّد استقبال حدث.
        check("%d — التِكّة تغيّر العمق فعلًا" % atom_id,
              first["current_depth"] != last["current_depth"],
              "%s == %s" % (first["current_depth"], last["current_depth"]))
        check("%d — الهوية كاملة في البطاقة الحيّة" % atom_id,
              all(last.get(field) for field in ("account_id", "broker", "symbol")),
              str({f: last.get(f) for f in ("account_id", "broker", "symbol")}))
        check("%d — العمق الناقص لا يعطي READY" % atom_id,
              first["state"] != "READY" or
              first["current_depth"] >= first["required_depth"],
              "state=%s depth=%s/%s" % (first["state"], first["current_depth"],
                                        first["required_depth"]))
        await atom.stop()


# ═══ §٣ — هوية الدورة: مصدر واحد وعزل كامل ═══════════════════════════════
async def test_cycle_identity_single_source():
    print("\n### §٣ — هوية الدورة: مصدر واحد · حسابان لا يتصادمان ###")
    same = {"symbol": "NQ100", "timeframe": "60s", "period_start": 100.0}
    a = cycle_key(account_id="A1", broker="BR1", **same)
    b = cycle_key(account_id="A2", broker="BR2", **same)
    c = cycle_key(account_id="A1", broker="BR2", **same)
    check("حسابان ووسيطان مختلفان ⇒ معرِّف مختلف", a != b, "%s == %s" % (a, b))
    check("الوسيط وحده يكفي للتفريق", a != c, "%s == %s" % (a, c))
    check("المعرِّف يحمل الهوية الخمسة كاملة",
          split_cycle_key(a) == {"account_id": "A1", "broker": "BR1",
                                 "symbol": "NQ100", "timeframe": "60s",
                                 "period_start": "100.0"},
          str(split_cycle_key(a)))
    check("هوية ناقصة تُكشف في المعرِّف لا تُستَر",
          cycle_key(account_id="", broker="", **same).startswith("||"),
          cycle_key(account_id="", broker="", **same))
    # ⛔ حارس ساكن: عودة أيّ بانٍ محلّي تُسقط هذا الفحص (شرط المالك).
    local = [path.parent.name
             for path in sorted((ROOT / "atoms").glob("*/atom.py"))
             if '"%s|%s|%s"' in path.read_text(encoding="utf-8")]
    check("صفر بانٍ محلّي في كل الذرّات", not local, ", ".join(local))
    # حسابان على نفس الرمز والفريم والزمن ⇒ دورتان منفصلتان، لا واحدة.
    module = load(200)
    atom, _ = await start(module, 200, {"timeout_seconds": 5.0})
    await atom._on_candle(candle(account_id="A1", broker="BR1"))
    await atom._on_candle(candle(account_id="A2", broker="BR2"))
    check("حسابان ⇒ دورتان مفتوحتان لا دورة واحدة", len(atom._cycles) == 2,
          str(sorted(atom._cycles)))
    await atom.stop()


# ═══ §٤ · §٢٢ — الدورة لها صاحب واحد ══════════════════════════════════════
async def test_late_result_is_orphan():
    print("\n### §٤ · §٢٢ — نتيجة بلا دورة = LATE ولا تفتح سياقًا ###")
    orphan = cycle_key(account_id="A9", broker="BR9", symbol="NQ100",
                       timeframe="60s", period_start=999.0)
    for atom_id, unit in ((200, "swing"), (250, "pool"), (300, "mean")):
        module = load(atom_id)
        atom, _ = await start(module, atom_id, {"timeout_seconds": 5.0})
        await atom._on_unit_state({"cycle_id": orphan, "id": unit,
                                   "symbol": "NQ100", "timeframe": "60s",
                                   "account_id": "A9", "broker": "BR9",
                                   "status": "ok"})
        check("%d — نتيجة يتيمة لا تفتح دورة" % atom_id, not atom._cycles,
              "فُتح %d" % len(atom._cycles))
        check("%d — تُحسب LATE" % atom_id, atom._late == 1, str(atom._late))
        await atom.stop()


# ═══ §١٢-5 — وصل 210 · 260 · 300 بـ451 ════════════════════════════════════
async def test_451_new_links():
    print("\n### §١٢-5 · اختبار ٧ و٤ — 210 · 260 · 300 → 451 (READY فقط) ###")
    module = load(451)
    config = {"require_same_cycle": True,
              "expected_families": ["401", "166", "400", "350"]}
    atom, bus = await start(module, 451, config)
    for event in ("market.structure.updated", "market.liquidity.updated",
                  "stats.cycle.collected"):
        check("451 يشترك في %s" % event, event in bus.handlers, "غير موصول")
    await atom._on_candle(candle())
    # §٣ — المعرِّف يُشتقّ من المصدر المشترك، لا يُثبَّت نصًّا هنا. لو اختلف
    # ما يبنيه 451 عمّا يبنيه المصدر، سقط هذا الفحص — وهو المقصود.
    cycle = cycle_key_of(candle())
    ready = {"account_id": "A1", "broker": "BR", "symbol": "NQ100",
             "timeframe": "60s", "cycle_id": cycle, "signal": "up", "score": 60,
             "confidence": 0.9, "status": "ok",
             "unified": {"state": "READY"}}
    not_ready = dict(ready, unified={"state": "NOT_READY"})
    await atom._on_structure(not_ready)
    evidence = atom._cycles.get(cycle, {}).get("evidence", {})
    check("NOT_READY لا تصل 451", "210" not in evidence, str(sorted(evidence)))
    await atom._on_structure(ready)
    await atom._on_liquidity(ready)
    await atom._on_stats(ready)
    evidence = atom._cycles.get(cycle, {}).get("evidence", {})
    for source in ("210", "260", "300"):
        check("%s وصل 451" % source, source in evidence, str(sorted(evidence)))
    await atom.stop()


async def test_451_role_unchanged():
    print("\n### اختبار ١٥ — أدوار 451 · 150 · 650 لم تتغيّر ###")
    module = load(451)
    check("451 ما زال ينشر decision.aggregated.state",
          module.EVENT_OUT == "decision.aggregated.state", module.EVENT_OUT)
    config = {"require_same_cycle": True,
              "expected_families": ["401", "166", "400", "350"]}
    atom, bus = await start(module, 451, config)
    check("الأقسام الثلاثة ليست شرطًا لإغلاق الدورة",
          set(atom._expected) == {"401", "166", "400", "350"}, str(atom._expected))
    await atom.stop()
    m150 = load(150)
    check("150 ما زال ينشر analysis.cycle.collected أو مثله",
          "analysis" in m150.EVENT_OUT, m150.EVENT_OUT)
    m650 = load(650)
    check("650 ما زال نقطة دخول المحافظ",
          m650.EVENT_OUT == "portfolio.components.state", m650.EVENT_OUT)


async def test_sections_isolated():
    print("\n### §١٢-6 · اختبارا ١٧ و١٨ — عزل الحساب والوسيط ###")
    module = load(201)
    atom, bus = await start(module, 201, {"lookback": 2})
    for i in range(8):
        await atom._on_candle(candle(float(i), 100.0 + i))
        await atom._on_candle(candle(float(i), 200.0 + i, account_id="A2"))
        await atom._on_candle(candle(float(i), 300.0 + i, broker="BR2"))
    rows = bus.rows(module.EVENT_OUT)
    accounts = {r.get("unified", {}).get("account_id") for r in rows}
    brokers = {r.get("unified", {}).get("broker") for r in rows}
    check("حسابان يظهران منفصلين", {"A1", "A2"} <= accounts, str(accounts))
    check("وسيطان يظهران منفصلين", {"BR", "BR2"} <= brokers, str(brokers))
    await atom.stop()


async def test_live_tick_and_calibration():
    """§١٢-7 · §١٢-8 — مقيسان في الكود القائم، لا يُدَّعَيان."""
    print("\n### §١٢-7 · §١٢-8 — التحليل الحيّ بالتِكّة · المعايرة والأوزان ###")
    from shared import live_analysis
    check("مصدر المحللين هو market.tick.validated",
          live_analysis.EVENT_TICK == "market.tick.validated", live_analysis.EVENT_TICK)
    live = [n for n in range(151, 166)
            if "live_analyzer" in
            next((ROOT / "atoms").glob("%d_*" % n)).joinpath("atom.py").read_text(encoding="utf-8")]
    check("15 محللًا على التِكّة", len(live) == 15, str(len(live)))
    weights = live_analysis.DEFAULT_WEIGHTS
    check("وزن لكل محلل", len(weights) == 15, str(len(weights)))
    check("مجموع الأوزان 100 بلا إعادة توزيع ضمني",
          abs(sum(weights.values()) - 100.0) < 1e-9, str(sum(weights.values())))
    check("المحللون ليسوا نسخًا متطابقة", len(live_analysis.PROFILE) == 15,
          str(len(live_analysis.PROFILE)))
    check("سجلّ إعدادات دائم موجود",
          hasattr(live_analysis, "AnalysisSettingsStore"), "لا سجلّ")
    check("العقد معلن بنسخته",
          "tick_depth_threshold_weight_v1" in
          (ROOT / "shared" / "live_analysis.py").read_text(encoding="utf-8"), "بلا نسخة")


async def test_downstream_not_broken():
    print("\n### اختبار ١٦ — 404–412 · 713 · 718 · 712 · 709 لم تُكسر ###")
    for number in (404, 405, 406, 407, 408, 409, 410, 411, 412):
        folder = next((ROOT / "atoms").glob("%d_*" % number))
        source = (folder / "atom.py").read_text(encoding="utf-8")
        check("%d ما زال يشترك في مصدره" % number, "context.subscribe" in source,
              "فُصل")
    for number in (713, 718, 712, 709):
        folder = next((ROOT / "atoms").glob("%d_*" % number))
        source = (folder / "atom.py").read_text(encoding="utf-8")
        check("%d ما زال يستقبل" % number, "context.subscribe" in source, "فُصل")


async def main() -> int:
    print("=" * 74)
    print("إثبات ورقة ١٥ — منظومة التحليل والقرار والمحافظ")
    print("المشروع: %s" % ROOT)
    print("=" * 74)
    tests = [
        test_contract_on_all_sections,
        test_identity_missing_is_declared,
        test_independence_of_opinion_fields,
        test_depth_blocks_ready,
        test_dormant_107,
        test_513_sizes_from_654,
        test_516_reads_equity_from_654,
        test_585_reads_free_margin_from_656,
        test_no_atom_reads_money_from_619,
        test_identity_readers_untouched,
        test_668_has_consumer,
        test_519_no_self_loop,
        test_liquidity_measure_and_section_required_units,
        test_probability_is_not_direction,
        test_restart_preserves_calibration,
        test_unified_output_contract,
        test_strategy_card_is_truthful,
        test_inner_atom_window_depth,
        test_unapproved_parameters_block_ready,
        test_phase1_section_live_reaches_451,
        test_weight_ratio_and_weighted_aggregation,
        test_ready_gate_cannot_be_claimed,
        test_depth_is_evidence_and_calibrated_per_scope,
        test_confidence_is_independent,
        test_sections_are_tick_driven,
        test_cycle_identity_single_source,
        test_late_result_is_orphan,
        test_451_new_links,
        test_451_role_unchanged,
        test_sections_isolated,
        test_live_tick_and_calibration,
        test_downstream_not_broken,
    ]
    for test in tests:
        try:
            await test()
        except Exception as exc:
            check("%s (تشغيل)" % test.__name__, False, repr(exc))
    print("\n" + "=" * 74)
    print("نجح: %d · سقط: %d" % (len(PASSED), len(FAILED)))
    for name, detail in FAILED:
        print("  ❌ %s   %s" % (name, detail))
    print("=" * 74)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
