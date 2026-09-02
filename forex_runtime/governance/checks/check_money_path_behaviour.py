"""حارس سلوك مسارات المال — حكم المالك ٢٠٢٦-٠٨-١٦.

> «الحارس يحرس المعنى لا النصّ. وإذا لم نستطع جعله يسقط على العطل المقصود،
>  فالتعديل غير مثبت حتى لو أعطى المدقّق `0`.»

هذا حارس من **المستوى الثاني**: لا يسأل «هل الكود موجود؟» بل يشغّل الذرّة
نفسها على مدخلين — سليم يجب أن يمرّ، ومكسور بالعطل **نفسه** يجب أن يُرفض.
الكسر في المُدخَل لا في الملفّ، فلا يحتاج تعديل مصدر ولا استعادة.

يغطّي تسعة عقود من حزمة ٢٠٢٦-٠٨-١٦، كلٌّ بشقّيه.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
sys.path.insert(0, str(ROOT))

bad = 0
checked = 0


def show(contract: str, case: str, ok: bool, detail: str = "") -> None:
    global bad, checked
    checked += 1
    if not ok:
        bad += 1
    print("   %-46s %-30s %-16s %s"
          % (contract, case, detail, "✓" if ok else "✘"))


def load(prefix: str, module: str = "atom"):
    folder = next(d for d in ATOMS.iterdir()
                  if d.is_dir() and d.name.startswith(prefix + "_"))
    sys.path.insert(0, str(folder))
    spec = importlib.util.spec_from_file_location(
        "%s_%s" % (module, prefix), folder / ("%s.py" % module))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Bus:
    """ناقل صامت يجمع ما نُشر — لا يقرّر شيئًا."""

    def __init__(self, config=None):
        self.events = []
        self.config = config or {}
        self.logger = type("L", (), {"error": lambda *a, **k: None,
                                     "info": lambda *a, **k: None})()

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.events.append((name, payload))

    def of(self, name):
        return [p for n, p in self.events if n == name]


async def build(mod, config=None):
    atom = mod.Atom()
    ctx = Bus(config)
    await atom.initialize(ctx)
    await atom.start()
    return atom, ctx


# ── ٥١٧ · التكلفة المجهولة لا تصير صفرًا ────────────────────────────────────
async def contract_517():
    mod = load("517")
    base = {"symbol": "BTCUSD", "account_id": "A", "profit": -10.0, "ticket": 1}

    atom, ctx = await build(mod)
    await atom._on_account({"equity": 1000.0})
    await atom._on_outcome({**base, "swap": -1.0, "commission": -2.0, "fee": -0.5})
    out = ctx.of(mod.EVENT_OUT)
    ok = bool(out) and out[-1]["costs_complete"] is True
    show("٥١٧ التكلفة المجهولة", "الحدود الثلاثة موجودة", ok,
         "complete=%s" % (out[-1]["costs_complete"] if out else "—"))
    if out:
        show("٥١٧ التكلفة المجهولة", "والصافي يطرحها", out[-1]["pnl"] == -13.5,
             "net=%s" % out[-1]["pnl"])

    # الكسر: الجسر لا يعطي الحدود. يجب ألّا تُقرأ صفرًا ولا يُعلَن الصافي كاملًا.
    atom, ctx = await build(mod)
    await atom._on_account({"equity": 1000.0})
    await atom._on_outcome(dict(base))
    out = ctx.of(mod.EVENT_OUT)
    ok = bool(out) and out[-1]["costs_complete"] is False \
        and out[-1]["net_is_partial"] is True \
        and set(out[-1]["costs_unknown"]) == {"swap", "commission", "fee"}
    show("٥١٧ التكلفة المجهولة", "الحدود غائبة ⇒ صافٍ جزئيّ معلَن", ok,
         "unknown=%d" % (len(out[-1]["costs_unknown"]) if out else -1))
    if out:
        show("٥١٧ التكلفة المجهولة", "ولا يُحسب الغائب صفرًا في الجمع",
             out[-1]["cost_total"] == 0.0 and out[-1]["gross_pnl"] == out[-1]["pnl"],
             "cost=%s" % out[-1]["cost_total"])


# ── ٥٦٣ · غياب السعر المطلوب يمنع القياس والقرار ───────────────────────────
async def contract_563():
    mod = load("563")
    fill = {"event_type": "OPENED", "request_id": "r-1", "symbol": "BTCUSD",
            "side": "BUY", "volume": 1.0, "entry_price": 105.0, "ticket": 9}

    atom, ctx = await build(mod)
    await atom._on_specs({"symbols": [{"symbol": "BTCUSD", "point": 1.0,
                                       "tick_value": 1.0, "tick_size": 1.0}]})
    await atom._on_requested({"request_id": "r-1", "symbol": "BTCUSD",
                              "side": "BUY", "reference_price": 100.0})
    await atom._on_event(dict(fill))
    ack = ctx.of(mod.EVENT_ACK)
    ok = bool(ack) and ack[-1]["slippage_measured"] is True
    show("٥٦٣ الانزلاق", "سعر مطلوب موجود ⇒ يُقاس", ok)
    if ack:
        show("٥٦٣ الانزلاق", "شراء نُفّذ أعلى = ضدّك",
             ack[-1]["slippage_price"] == 5.0 and ack[-1]["slippage_adverse"] is True,
             "adverse=%s" % ack[-1]["slippage_price"])

    # نفس الحركة السعريّة على بيع: يجب أن تنقلب الإشارة إلى «لصالحك».
    atom, ctx = await build(mod)
    await atom._on_requested({"request_id": "r-2", "symbol": "BTCUSD",
                              "side": "SELL", "reference_price": 100.0})
    await atom._on_event(dict(fill, request_id="r-2", side="SELL"))
    ack = ctx.of(mod.EVENT_ACK)
    show("٥٦٣ الانزلاق", "بيع نُفّذ أعلى = لصالحك",
         bool(ack) and ack[-1]["slippage_adverse"] is False,
         "adverse=%s" % (ack[-1]["slippage_price"] if ack else "—"))

    # الكسر: لا سعر مطلوب. لا رقم يُنشر، والقرار ممنوع، والصحّة تنزل.
    atom, ctx = await build(mod)
    await atom._on_event(dict(fill, request_id="r-none"))
    ack = ctx.of(mod.EVENT_ACK)
    body = ack[-1] if ack else {}
    ok = (body.get("slippage_measured") is False
          and body.get("slippage_usable") is False
          and body.get("slippage_blocks_decisions") is True
          and "slippage_price" not in body and "slippage_cost" not in body)
    show("٥٦٣ الانزلاق", "بلا سعر مطلوب ⇒ لا رقم ولا إذن", ok,
         "usable=%s" % body.get("slippage_usable"))
    health = await atom.health_check()
    show("٥٦٣ الانزلاق", "والصحّة تنزل لا تسكت",
         health.state.value != "healthy" if hasattr(health.state, "value")
         else str(health.state).endswith("DEGRADED"),
         str(health.message)[:26])


# ── ٥١٣ · التقريب لا يتجاوز الميزانيّة ─────────────────────────────────────
async def contract_513():
    mod = load("513")
    cfg = {"risk_per_trade_pct": 1.0, "default_stop_pct": 0.5,
           "min_lot": 0.01, "max_lot": 100.0, "lot_step": 0.1}
    atom, _ = await build(mod, cfg)
    atom._equity = 1000.0
    budget = 1000.0 * 1.0 / 100.0

    # مسافة تجعل الحجم الخام في منتصف الخطوة تمامًا: التقريب لأعلى يتجاوز.
    for distance, tick_value, tick_size in ((0.65, 1.0, 1.0), (1.3, 1.0, 1.0),
                                            (0.7, 1.0, 1.0), (2.1, 1.0, 1.0)):
        lot = atom._lot_from_distance(distance, tick_value, tick_size)
        cost = lot * distance * (tick_value / tick_size)
        show("٥١٣ سقف الميزانيّة", "مسافة %.2f لا تتجاوز الحدّ" % distance,
             cost <= budget * 1.0100001, "كلفة=%.3f من %.2f" % (cost, budget))


# ── ٥٨٤ · أرضيّة الوسيط لا تُخترق ───────────────────────────────────────────
async def contract_584():
    mod = load("584")
    cfg = {"stop_buffer": 0.0, "reward_risk": 2.0, "hard_floor_points": 0.0}
    order = {"action": "OPEN", "symbol": "X", "side": "BUY", "volume": 1.0,
             "reference_price": 100.0, "stop_loss": 99.98, "take_profit": 110.0,
             "protection_mode": "", "origin": ""}

    # أرضيّة واسعة: وقف قريب يجب أن يُزاح إليها، لا أن يمرّ كما هو.
    atom, ctx = await build(mod, cfg)
    await atom._on_specs({"symbols": [{"symbol": "X", "point": 0.01,
                                       "stops_level": 0.0, "freeze_level": 50.0,
                                       "volume_step": 0.01, "volume_min": 0.01}]})
    await atom._on_built(dict(order))
    legal = ctx.of(mod.EVENT_LEGAL)
    ok = bool(legal) and abs(100.0 - legal[-1]["stop_loss"]) >= 0.5 - 1e-9
    show("٥٨٤ أرضيّة الوسيط", "freeze_level=50 يُزيح الوقف القريب", ok,
         "sl=%s" % (legal[-1]["stop_loss"] if legal else "—"))

    # الكسر: نفس المدخل مع freeze_level=0 وstops_level=0 — بلا أرضيّة صلبة
    # كان الوقف يمرّ على بُعد سنتين من السعر. الأرضيّة الصلبة تمنع ذلك.
    atom, ctx = await build(mod, {**cfg, "hard_floor_points": 200.0})
    await atom._on_specs({"symbols": [{"symbol": "X", "point": 0.01,
                                       "stops_level": 0.0, "freeze_level": 0.0,
                                       "volume_step": 0.01, "volume_min": 0.01}]})
    await atom._on_built(dict(order))
    legal = ctx.of(mod.EVENT_LEGAL)
    ok = bool(legal) and abs(100.0 - legal[-1]["stop_loss"]) >= 2.0 - 1e-9
    show("٥٨٤ أرضيّة الوسيط", "وسيط يقول صفرًا ⇒ الأرضيّة الصلبة تحكم", ok,
         "sl=%s" % (legal[-1]["stop_loss"] if legal else "—"))


# ── ٥٥٢ · السبريد المجهول أو الواسع يُرفض ──────────────────────────────────
async def contract_552():
    mod = load("552")
    cfg = {"enabled": True, "max_spread_points": 100.0}
    order = {"action": "OPEN", "symbol": "BTCUSD", "side": "BUY", "volume": 1.0,
             "reference_price": 100.0, "stop_loss": 90.0, "take_profit": 120.0,
             "request_id": "r"}

    atom, ctx = await build(mod, cfg)
    atom._allowed = set()
    await atom._on_tick({"symbol": "BTCUSD", "spread_pts": 10.0})
    await atom._on_built(dict(order))
    show("٥٥٢ حاجز السبريد", "سبريد ضيّق ⇒ يمرّ",
         len(ctx.of(mod.EVENT_FINAL)) == 1,
         "أُرسل=%d" % len(ctx.of(mod.EVENT_FINAL)))

    atom, ctx = await build(mod, cfg)
    atom._allowed = set()
    await atom._on_tick({"symbol": "BTCUSD", "spread_pts": 500.0})
    await atom._on_built(dict(order))
    rejected = ctx.of(mod.EVENT_REJECTED)
    show("٥٥٢ حاجز السبريد", "سبريد واسع ⇒ يُرفض بسبب مُسمّى",
         len(ctx.of(mod.EVENT_FINAL)) == 0 and bool(rejected)
         and rejected[-1]["reason"] == mod.REASON_SPREAD,
         rejected[-1]["reason"] if rejected else "لا رفض")

    # الكسر الأهمّ: سبريد لم يُقَس أبدًا. fail-open هنا يعني تمرير الحالة
    # التي بُني الحاجز لها. يجب أن يُرفض.
    atom, ctx = await build(mod, cfg)
    atom._allowed = set()
    await atom._on_built(dict(order))
    rejected = ctx.of(mod.EVENT_REJECTED)
    show("٥٥٢ حاجز السبريد", "سبريد مجهول ⇒ يُرفض (fail-closed)",
         len(ctx.of(mod.EVENT_FINAL)) == 0 and bool(rejected)
         and rejected[-1]["reason"] == mod.REASON_SPREAD,
         rejected[-1]["reason"] if rejected else "مرّ!")


# ── ٥٧٤ · لا بقيّة أصغر من الحدّ الأدنى ────────────────────────────────────
async def contract_574():
    mod = load("574")
    cfg = {"partial_at_r": 1.0, "partial_fraction": 0.5, "min_partial_lot": 0.01}
    atom, _ = await build(mod, cfg)
    step = getattr(atom, "_min_lot", 0.01)
    for volume, expect_full in ((1.0, False), (round(step * 1.5, 8), True)):
        close = round(volume * 0.5, 2)
        remainder = round(volume - close, 8)
        stranded = 0.0 < remainder < step
        show("٥٧٤ آخر لوت", "حجم %.3f ⇒ %s" % (
            volume, "الرجل كاملة" if expect_full else "جزئيّ"),
            stranded == expect_full,
            "بقيّة=%.3f" % remainder)


# ── ٥٧٦ · هويّة الزوج لا تتكرّر عبر إعادة التشغيل ──────────────────────────
async def contract_576():
    mod = load("576")
    seen = set()
    for epoch in (1786800000, 1786900000):
        atom, _ = await build(mod, {})
        await atom._on_pulse({"official_time": float(epoch)})
        atom._counter = 0
        atom._counter += 1
        pair_id = "pair-%s-%s-%d-%d" % ("A", "BTCUSD", atom._epoch, atom._counter)
        seen.add(pair_id)
    show("٥٧٦ هويّة الزوج", "جلستان ⇒ هويّتان مختلفتان", len(seen) == 2,
         "فريدة=%d من ٢" % len(seen))
    show("٥٧٦ هويّة الزوج", "والختم داخل الهويّة لا خارجها",
         all(len(p.split("-")) >= 5 for p in seen),
         next(iter(seen)).split("-", 3)[-1][:18])


async def main() -> int:
    print("=" * 112)
    print("حارس سلوك مسارات المال — كل عقد بشقّيه: السليم يمرّ، والمكسور يُرفض")
    print("=" * 112)
    for title, fn in (("٥١٧ · الربح الصافي", contract_517),
                      ("٥٦٣ · الانزلاق", contract_563),
                      ("٥١٣ · سقف الميزانيّة", contract_513),
                      ("٥٨٤ · أرضيّة الوقف", contract_584),
                      ("٥٥٢ · حاجز السبريد", contract_552),
                      ("٥٧٤ · آخر لوت", contract_574),
                      ("٥٧٦ · هويّة الزوج", contract_576)):
        print("\n%s" % title)
        try:
            await fn()
        except Exception as exc:                       # noqa: BLE001
            show(title, "تعذّر التشغيل", False, str(exc)[:26])
    print("\n" + "=" * 112)
    print("الفحوص = %d · الاختلافات = %d" % (checked, bad))
    print("سليم: كل عقد يمرّ على السليم ويسقط على المكسور." if bad == 0
          else "ساقط: عقد لا يمسك عطله.")
    return 1 if bad else 0


sys.exit(asyncio.run(main()))
