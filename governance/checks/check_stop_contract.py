"""Contract guard for problem 2 — the physical stop at the broker.

Owner's ruling 2026-08-13, option (c), verbatim:

    "The physical stop comes back at the OPEN as a WIDE LAST RESORT, not as the
     management tool for the position.  The budget stays the primary risk
     manager through 518 -> 519 -> 581.  Handling v_net = 0 stays inside the
     execution of 2, so the position is never left without a stop in the case
     where 512/525 cannot derive a price.  Do not change 581, the weights, the
     gates or the budget logic."

And his three conditions, each checked here:

    1. The 512/525 collision on `risk.hard_stop.price` must not produce a
       non-deterministic value.
    2. 578 must send the REAL stop in the open order, not just
       `asset_stop_distance`.
    3. There must be a declared, explicit fallback when v_net = 0 -- never a
       silent None.

  A) STRUCTURAL -- 578 fills stop_loss and never a take profit; the last resort
     is strictly wider than the budget's working distance; the fallback is
     declared; 571 names its hard-stop source; 581, the weights and the gate
     switches are untouched; 512 and 525 still agree mathematically.

  B) END TO END -- the real 581 target -> 578 -> 584 -> 552, and the order that
     reaches the gate carries a stop on the correct side.  Then the EA's own
     rule (`InpRequireStop && sl <= 0`) is applied to what actually came out:
     NO_STOP must be UNREACHABLE on the normal open path -- which is the proof
     that closes problem 3.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.contracts.atom import AtomContext  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
A512 = "512_الوقف_الهيكلي"
A525 = "525_سعر_الستوب_الصلب"
A552 = "552_مدقق_الأمر"
A571 = "571_مخطط_الإدارة_الدائمة"
A578 = "578_منفذ_التحوط"
A581 = "581_محرك_فرق_المركز"
A584 = "584_شرعية_الستوب"
A585 = "585_حارس_الهامش"
A586 = "586_بوابة_الرموز"
A516 = "516_قاطع_الأمان"
A551 = "551_باني_الأمر"

ACCOUNT = "52992818"
SYMBOL = "BTCUSD"
PRICE = 63478.22
STOP_FRAC = 0.0055
BUDGET = 100.0

# Not to be touched by this ruling.
FROZEN = {A581: "2.9.0", "518_دفتر_مخاطر_الأصل": "2.2.0", "453_حساب_الدرجة": "3.4.0"}

# البوّابة مغلقة **داخل المحكّ** لا على القرص (حكمه ٢٠٢٦-٠٨-١٥، الخيار أ).
GATE_OVERRIDE = {"enabled": False}


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []
        self.handlers = {}

    def subscribe(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.log.append((name, payload))
        for handler in list(self.handlers.get(name, [])):
            result = handler(payload)
            if inspect.isawaitable(result):
                await result

    def events(self, name):
        return [p for n, p in self.log if n == name]


def load_atom(folder: str):
    directory = ATOMS / folder
    spec = importlib.util.spec_from_file_location("_cstop_" + folder.split("_")[0],
                                                  directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def manifest(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


def ea_would_reject(order: dict) -> bool:
    """The Expert Advisor's own rule, mt5/QUANT_NQ.mq5:837, applied literally.

        if(InpRequireStop && sl <= 0 && !neutral_hedge) -> "NO_STOP"

    A NULL stop_loss reads as 0 in MQL5, so `None` counts as <= 0.
    """
    stop = order.get("stop_loss")
    stop = 0.0 if stop is None else float(stop)
    params = str(order.get("protection_mode") or "")
    neutral = params == "NEUTRAL_HEDGE" and order.get("pair_required") is True
    return stop <= 0.0 and not neutral


async def structural() -> int:
    print("=" * 78)
    print("أ) الحواجز البنيويّة — الستوب يعود، والملاذ الأخير أوسع من الميزانيّة")
    print("=" * 78)
    bad = 0
    s578 = (ATOMS / A578 / "atom.py").read_text(encoding="utf-8")
    sup = (ATOMS / A578 / "stop_support.py").read_text(encoding="utf-8")
    s571 = (ATOMS / A571 / "atom.py").read_text(encoding="utf-8")
    s552 = (ATOMS / A552 / "atom.py").read_text(encoding="utf-8")
    s584 = (ATOMS / A584 / "atom.py").read_text(encoding="utf-8")

    print("  شرطه ٢ — 578 يرسل الستوب الفعليّ لا المسافة وحدها:")
    for label, ok in (
            ('"stop_loss": round(stop, 8)', '"stop_loss": round(stop, 8)' in s578),
            ('"take_profit": None (لا هدف أبدًا)', '"take_profit": None' in s578),
            ("لم يبقَ stop_loss: None", '"stop_loss": None, "take_profit": None, "protection_mode"' not in s578),
            ("المسافة العاملة تبقى معلومة", '"asset_stop_distance": round(working, 8)' in s578)):
        bad += 0 if ok else 1
        print("      %-40s %s" % (label, "✓" if ok else "✗"))

    print("  شرطه ٣ — بديل معلَن عند v_net = 0، لا None صامتة:")
    for label, ok in (
            ("مصدر المسافة معلَن بالأمر", '"stop_source": source' in s578),
            ("بديل مصرَّح بالمنيفست", "fallback_stop_frac" in str(manifest(A578)["config"])),
            ("الكسر لا يمرّ بلا ستوب", "self._no_stop_skipped += 1; return" in s578),
            ("الدالّة ترجع None لا ستوبًا وهميًّا", "return None" in sup)):
        bad += 0 if ok else 1
        print("      %-40s %s" % (label, "✓" if ok else "✗"))

    print("  شرطه ١ — الحتميّة على risk.hard_stop.price:")
    ok = 'HARD_STOP_SOURCE = "525"' in s571 and 'str(payload.get("source") or "") != HARD_STOP_SOURCE' in s571
    bad += 0 if ok else 1
    print("      %-40s %s" % ("571 يسمّي مصدره (525)", "✓" if ok else "✗"))
    publishers = [f for f in (A512, A525) if "risk.hard_stop.price" in (manifest(f).get("publishes") or [])]
    print("      %-40s %s" % ("الناشران المعلَنان", publishers))

    print("  المستثنى من اللمس بأمره:")
    for folder, version in FROZEN.items():
        got = str(manifest(folder).get("version"))
        ok = got == version
        bad += 0 if ok else 1
        print("      %-40s %-8s %s" % (folder.split("_")[0], got, "✓" if ok else "✗ تغيّرت!"))
    # حكم المالك ٢٠٢٦-٠٨-١٥، الخيار (أ): «الحارس يعزل حالة البوّابة داخل محكّه،
    # ولا يعتمد على حالة التشغيل الحيّة». كان هنا شرطٌ على البطاقة الحيّة
    # (`enabled is False`) — فلمّا فتح المالك البوّابة بأمره سقط الحارس بلا ذنب
    # للعقد. الآن المحكّ يفرض الإغلاق على نسخته من الإعداد، والبطاقة الحقيقيّة
    # تُقاس قبل وبعد لتثبت أنّ الفحص **لم يغيّرها**.
    for folder, name in ((A552, "552"), ("575_مرسل_الإدارة", "575")):
        enabled = manifest(folder)["config"].get("enabled")
        print("      %-40s enabled=%-6s (تُسجَّل ولا تُشترَط)" % ("حالة بوّابة " + name, enabled))
    ok = GATE_OVERRIDE.get("enabled") is False
    bad += 0 if ok else 1
    print("      %-40s %s" % ("المحكّ يفرض الإغلاق على نسخته", "✓" if ok else "✗"))

    print("  والعقد الدائم صار «بلا هدف» لا «بلا ستوب» — بالذرّتين معًا:")
    # اقتصر على جسد دالّة العقد الدائم وحدها: نصّ مطابق يوجد أيضًا بالمسار
    # العاديّ، فلو قارنّا الملفّ كلّه لَمرّ كسرُ العقد الدائم بلا أن يُلحظ.
    perpetual_body = s552.split("def _perpetual_budget_contract", 1)[-1].split("\ndef ", 1)[0]
    for label, ok in (
            ('552 يشترط ستوبًا موجبًا', 'if stop is None or stop <= 0.0:\n        return "NO_STOP"' in perpetual_body),
            ("552 يمنع الهدف لا الستوب", 'if order.get("take_profit") not in (None, 0, 0.0):' in perpetual_body
             and 'if order.get("stop_loss") not in (None, 0, 0.0):' not in perpetual_body),
            ("552 يتحقّق من جهة الستوب", 'return "BUY_LEVELS"' in perpetual_body and "PERPETUAL_NO_TP" in s552),
            ("584 يشترط ستوبًا موجبًا", '(num(o.get("stop_loss")) or 0.0)>0.0' in s584),
            ("584 يبقي الهدف فارغًا", 'o.get("take_profit") in (None,0,0.0)' in s584)):
        bad += 0 if ok else 1
        print("      %-40s %s" % (label, "✓" if ok else "✗"))

    print("\n  المسطرة: 512 و525 يجب أن يتّفقا رقمًا برقم على نفس الدفتر")
    m512, m525 = load_atom(A512), load_atom(A525)
    for v_net, w in ((0.5, 0.5 * PRICE), (-0.5, -0.5 * PRICE), (2.0, 2.0 * PRICE)):
        led = {"account_id": ACCOUNT, "symbol": SYMBOL, "asset_canonical": SYMBOL,
               "risk_budget": BUDGET, "R": BUDGET, "budget": BUDGET, "budgeted": True,
               "K": 0.0, "buffer_k": 0.0, "cost": 0.0, "commission_est": 0.0,
               "v_net": v_net, "w": w, "vpu": 1.0}
        a512, a525 = m512.Atom(), m525.Atom()
        bus = Bus()
        await _two_stops(a512, a525, bus, led)
        got512 = [p for p in bus.events("risk.asset_stop.state")][-1]["stop_price"]
        rows = bus.events("risk.hard_stop.price")
        got525 = [r for r in rows if r.get("count") is not None][-1]["stops"][0]["p_stop"]
        same = got512 is not None and got525 is not None and abs(got512 - got525) < 1e-6
        bad += 0 if same else 1
        print("      v_net=%-6.1f  512=%-14.4f  525=%-14.4f  %s" % (
            v_net, got512 or 0.0, got525 or 0.0, "✓ متطابقان" if same else "✗ اختلفا!"))
    return bad


async def _two_stops(a512, a525, bus, led) -> None:
    for atom, folder in ((a512, A512), (a525, A525)):
        await atom.initialize(AtomContext(atom_id=int(folder.split("_")[0]),
                                          config=manifest(folder).get("config") or {},
                                          logger=_Logger(), publish=bus.publish,
                                          subscribe=bus.subscribe))
        await atom.start()
    await bus.publish("market.symbol_specs", {"symbols": [
        {"account_id": ACCOUNT, "symbol": SYMBOL, "tick_size": 1.0, "tick_value": 1.0}]})
    await bus.publish("risk.asset_ledger.state", {"ledgers": [led], "count": 1})


async def drive(stop_frac) -> Bus:
    """The REAL chain, no shortcut:

        581-shaped target -> 578 -> 586 -> 585 -> 516 -> 551 -> 584 -> 552

    Skipping any link would make a green result meaningless -- the same lesson
    the owner set after the 405 guard passed trivially.
    """
    bus = Bus()
    for folder in (A578, A586, A585, A516, A551, A584, A552):
        config = dict(manifest(folder).get("config") or {})
        # العزل: نسخة الإعداد داخل المحكّ وحده. البطاقة على القرص لا تُكتب أبدًا،
        # فحالة البوّابة الحقيقيّة لا تتأثّر ولو سقط الفحص في منتصفه.
        if "enabled" in config:
            config.update(GATE_OVERRIDE)
        module = load_atom(folder)
        atom = module.Atom()
        await atom.initialize(AtomContext(atom_id=int(folder.split("_")[0]),
                                          config=config,
                                          logger=_Logger(), publish=bus.publish,
                                          subscribe=bus.subscribe))
        await atom.start()
    spec = {"symbol": SYMBOL, "tick_size": 0.01, "point": 0.01, "stops_level": 0.0,
            "volume_step": 0.01, "volume_min": 0.01, "tick_value": 0.01,
            "contract_size": 1.0}
    await bus.publish("market.symbol_specs", {"symbols": [spec]})
    await bus.publish("platform.account.state", {
        "account_id": ACCOUNT, "trade_allowed": True, "equity": 644.84,
        "free_margin": 644.84, "leverage": 5000})
    await bus.publish("SYS_SECOND", {"official_time": 1000.0})
    # Item 4-10 contract: the snapshot must prove it was produced after the
    # consumer resumed, and carry its identity -- otherwise 578 refuses it as
    # history, fail-closed, and this guard would read the refusal as "no stop".
    target = {"account_id": ACCOUNT, "symbol": SYMBOL, "status": "READY", "action": "ADD",
              "target_net": 0.2, "current_net": 0.0, "delta_net": 0.2,
              "snapshot_id": "snapshot-%s-%s-1" % (ACCOUNT, SYMBOL),
              "produced_at": 9_000_000_000.0, "producer_epoch": 9_000_000_000.0,
              "sequence": 1,
              "reference_price": PRICE, "risk_budget": BUDGET}
    if stop_frac is not None:
        target["stop_distance_frac"] = stop_frac
    await bus.publish("execution.snapshot.state", target)
    # 586 asks the symbol store to resolve; nothing else answers on this bus, so
    # the guard plays that one external role and nothing more.
    for ask in bus.events("symbol.resolve.requested"):
        await bus.publish("symbol.resolve.result", {
            "request_id": ask.get("request_id"), "approved": True, "status": "RESOLVED",
            "logical_symbol": SYMBOL, "asset_canonical": SYMBOL, "broker_symbol": SYMBOL,
            "spec": spec})
    return bus


def report(bus: Bus, label: str, want_source: str) -> int:
    bad = 0
    requests = bus.events("execution.order.requested")
    if not requests:
        print("  %s — 578 لم يرسل شيئًا ✗" % label)
        return 1
    order = requests[0]
    working = order.get("asset_stop_distance") or 0.0
    catastrophe = order.get("catastrophe_distance") or 0.0
    stop_value = order.get("stop_loss")
    if not isinstance(stop_value, (int, float)) or stop_value <= 0:
        print("  %s\n      578 أرسل فتحًا بلا ستوب: %r  ✗" % (label, stop_value))
        return 1
    print("  %s" % label)
    print("      578  side=%-5s stop_loss=%-14s tp=%-5s source=%s" % (
        order.get("side"), order.get("stop_loss"), order.get("take_profit"),
        order.get("stop_source")))
    print("      المسافة: ميزانيّة=%-10.4f ملاذ أخير=%-10.4f (×%s)" % (
        working, catastrophe, order.get("catastrophe_multiple")))

    checks = (
        ("ستوب موجب فعليّ", isinstance(order.get("stop_loss"), (int, float)) and order["stop_loss"] > 0),
        ("لا هدف", order.get("take_profit") is None),
        ("الملاذ أوسع من الميزانيّة", catastrophe > working),
        ("مصدر المسافة", order.get("stop_source") == want_source),
        ("الستوب تحت السعر (شراء)", order.get("side") != "BUY" or order["stop_loss"] < PRICE),
        ("الستوب فوق السعر (بيع)", order.get("side") != "SELL" or order["stop_loss"] > PRICE),
        ("الميزانيّة تضرب أوّلًا", abs(PRICE - order["stop_loss"]) > working),
    )
    for name, ok in checks:
        bad += 0 if ok else 1
        print("      %-32s %s" % (name, "✓" if ok else "✗"))

    rejected = bus.events("execution.order.rejected")
    stages = (("586", "execution.order.resolved"), ("585", "risk.margin.validation.completed"),
              ("516", "risk.validation.completed"), ("551", "execution.order.built"),
              ("584", "execution.order.legal"), ("552", "execution.order.rejected"))
    for atom, event in stages:
        seen = bus.events(event)
        ok = bool(seen)
        bad += 0 if ok else 1
        print("      %-4s → %-34s %d %s" % (atom, event, len(seen), "✓" if ok else "✗"))
    # ٥٥٢ نفسها تنشر كل رفض (بما فيه البوّابة المقفولة) على نفس الحدث
    # execution.order.rejected بحقل reason — «execution.order.preview» اللي
    # كان هالفحص يقرأه قبل اليوم غير موجود إطلاقًا بكود ٥٥٢ الحالي (راجع
    # سياق/٩٠). البوّابة هون مقفلة افتراضيًّا (GATE_OVERRIDE) فرفض disabled
    # هو المتوقَّع بالضبط — مو خرقًا لسلسلة تانية.
    gate_closed = [r for r in rejected if r.get("reason") == "disabled"]
    other_rejects = [r for r in rejected if r.get("reason") != "disabled"]
    ok = not other_rejects
    bad += 0 if ok else 1
    print("      %-4s   %-34s %d %s" % ("", "مرفوض لسبب غير البوّابة", len(other_rejects),
                                        "✓" if ok else "✗ %s" % [r.get("reason") for r in other_rejects]))
    ok = bool(gate_closed)
    bad += 0 if ok else 1
    print("      البوّابة بقيت مقفلة ⇒ الأمر بيضلّ مقفول، ولا أمر للسوق %s" % ("✓" if ok else "✗"))

    final = (gate_closed or bus.events("execution.order.legal") or [order])[-1]
    would = ea_would_reject(final)
    bad += 0 if not would else 1
    print("      الإكسبرت (mq5:837) على ما خرج فعلًا: %s" % (
        "🔴 NO_STOP!" if would else "✓ NO_STOP غير قابل للوصول — وهذا ما يغلق ٣"))
    return bad


async def main_async() -> int:
    # شرطاه ٤ و٥: حالة البوّابة الحقيقيّة لا تتغيّر بسبب الاختبار، ولا يتركها
    # الفشل مفتوحة. نقيس بايتات البطاقتين قبل وبعد — لا ندّعي، نقارن.
    cards = {folder: (ATOMS / folder / "manifest.yaml").read_bytes()
             for folder in (A552, "575_مرسل_الإدارة")}

    bad = await structural()
    print("\n" + "-" * 78)
    print("ب) طرف-لطرف حقيقيّ — 581(هدف) ← 578 ← 584 ← 552، ثم قاعدة الإكسبرت نفسها")
    print("-" * 78)
    try:
        bad += report(await drive(STOP_FRAC), "١· الحالة العاديّة (581 يعطي كسر المسافة)",
                      "CATASTROPHE_FROM_CAPACITY")
        print()
        bad += report(await drive(None), "٢· v_net = 0 — لا 512 ولا 525 يشتقّان سعرًا",
                      "CATASTROPHE_FALLBACK_FRACTION")

        print("\n  ٣· البوّابة المفتوحة داخل المحكّ ⇒ العقد يعمل والأمر يمرّ:")
        GATE_OVERRIDE["enabled"] = True
        try:
            opened = await drive(STOP_FRAC)
        finally:
            GATE_OVERRIDE["enabled"] = False        # لا يترك الفشل بوّابة مفتوحة
        # مقفلة ⇒ `execution.order.rejected` بـ`reason=disabled` (لا حدث
        # منفصل «execution.order.preview» — غير موجود بكود ٥٥٢ الحالي إطلاقًا).
        # مفتوحة ⇒ `trading.final_decision` — الأمر يخرج فعلًا، مو مقفول.
        final = opened.events("trading.final_decision")
        blocked = [p for p in opened.events("execution.order.rejected")
                   if p.get("reason") == "disabled"]
        ok = bool(final) and not blocked
        bad += 0 if ok else 1
        print("      مفتوحة ⇒ قرار نهائيّ=%d · أوامر مقفولة=%d %s"
              % (len(final), len(blocked), "✓" if ok else "✗"))
        stop = (final or [{}])[-1].get("stop_loss")
        ok = isinstance(stop, (int, float)) and stop > 0
        bad += 0 if ok else 1
        print("      والعقد نفسه يعمل مفتوحًا: stop_loss=%s %s" % (stop, "✓" if ok else "✗"))
        ok = GATE_OVERRIDE.get("enabled") is False
        bad += 0 if ok else 1
        print("      وعادت مغلقة داخل المحكّ بعدها %s" % ("✓" if ok else "✗"))
    finally:
        unchanged = all((ATOMS / folder / "manifest.yaml").read_bytes() == raw
                        for folder, raw in cards.items())
        bad += 0 if unchanged else 1
        print("\n      البطاقتان الحقيقيّتان لم تُكتبا بايتًا واحدًا %s"
              % ("✓" if unchanged else "🔴 تغيّرت!"))

    print("\n" + "=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الستوب يعود ملاذًا أخيرًا، الميزانيّة تضرب أوّلًا، وNO_STOP غير قابل للوصول.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
