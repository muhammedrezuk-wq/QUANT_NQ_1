"""Contract guard for problem 58 (option a) — a failed delta must be VISIBLE,
and nothing else may move.

Owner's ruling 2026-08-14, verbatim:

    "58 = observation only. We add, after a guard that pins the current
     behaviour: 1. a clear failure counter for delta. 2. a view of
     flood_guard.failing() and the backoff duration. 3. publish/expose the
     measurement only, without changing the send decision or the retry.
     4. no synthetic pair_id for delta. 5. no new perpetual.owner.escalation.
     6. no change to 576. 7. no change to 552."
    "perpetual.pair.state: we do not add a listener now. Record that it is a
     broadcast with no consumer."

What was measured before this guard existed:

  * 576 -- not 578 -- is the pair producer, and it worked: 8 legs, 4 pairs,
    all DONE/OK on 2026-08-10, every one carrying the full pair contract.
  * Of 164,097 delta-*/reduce-* rows in the real bridge, NOT ONE carries a
    pair field, and NOT ONE carries a retry suffix.
  * So `_failure_for` returns at `mapping is None` for every delta, and a
    rejection changes NOTHING: the health details are byte-identical before
    and after, and even the flood guard's failure count stays 0 -- because
    `_on_rejected` never calls `mark_failure` at all.

  A) STRUCTURAL -- the counter exists and is surfaced; the send/retry code is
     frozen line by line; flood_guard.py is frozen by hash; 576 and 552 are
     frozen by version and anchor; no synthetic pair_id; the escalation and
     pair-state publishes stay at exactly one each.
  B) END TO END -- a real 578 on a real bus: the emitted order must stay
     byte-identical to the frozen capture, a rejection must become visible
     without producing a single new event, a send failure must show the REAL
     backoff the guard imposes, an identical target must stay suppressed, and
     a 576-shaped pair leg must still register as before.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import hashlib
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
A578 = "578_منفذ_التحوط"
A576 = "576_المحرك_الدائم"
A552 = "552_مدقق_الأمر"

ACC, SYM = "A", "GOLD"

# What the health details must carry, failing or not.
HEALTH_KEYS = ("delta_failed", "delta_failing", "delta_last_reason",
               "guard_failing", "guard_backoff_s")

# The order 578 emits today, captured from the running atom before anything was
# touched.  Option (a) is measurement only, so this must not move by one digit.
FROZEN_ORDER = {
    # Problem 63 (option a-2) moved the identity into the id: snapshot then
    # official time.  No snapshot here, so the declared `nosnap` marker stands.
    "request_id": "delta-A-GOLD-buy-nosnap-1000-1", "account_id": "A", "action": "OPEN",
    "symbol": "GOLD", "side": "BUY", "volume": 0.25, "reference_price": 2000.0,
    "stop_loss": 1940.0, "take_profit": None, "protection_mode": "PERPETUAL_BUDGET",
    "origin": "perpetual-delta", "purpose": "ADD", "target_net": 0.25,
    "current_net": 0.0, "delta_net": 0.25, "risk_budget": 100.0,
    "asset_stop_distance": 20.0, "catastrophe_distance": 60.0,
    "catastrophe_multiple": 3.0, "stop_source": "CATASTROPHE_FROM_CAPACITY",
    "stop_is_last_resort": True,
}

# The flood guard decides sending.  It is not part of this ruling at all.
FLOOD_GUARD_SHA = "22b83496990b55ba3d614ad81f32ae05ff13ccafb841fe531d56141aee6a7a1c"

# Lines that carry the send / retry decision and must stay identical.
FROZEN_LINES = (
    ("قرار الإرسال", "if not self._flood_guard.allows(account, symbol, payload, self._official_time): return"),
    ("تثبيت الإرسال", "self._flood_guard.mark_sent(account, symbol, self._official_time)"),
    ("تراجع الفشل", "self._flood_guard.mark_failure(str(payload.get(\"account_id\") or \"\"), str(payload.get(\"symbol\") or \"\"), self._official_time)"),
    ("حدّ المحاولات", "if attempt < self._max_attempts:"),
    ("الإعادة لا تصل الفرق", "if mapping is None:"),
)

# The neighbours his ruling put out of bounds.
FROZEN_NEIGHBOURS = {A576: "2.2.0", A552: "2.8.2"}
NEIGHBOUR_ANCHORS = (
    (A576, "576 ينتج الزوج كاملًا", '"pair_id": pair_id, "leg_role": role, "attempt": 1,'),
    (A552, "552 عقد الزوج المحايد", "def _neutral_pair_contract(order: dict[str, Any]) -> bool:"),
)


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    """A bus that actually DELIVERS, like the real one.

    578 subscribes to `execution.order.requested` -- the very event it
    publishes.  A bus whose subscribe() is a no-op hides that, and a guard that
    cannot see the atom hear itself cannot prove a delta stays out of the pair
    machinery.  So handlers are kept and dispatched.
    """

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

    def of(self, name):
        return [p for n, p in self.log if n == name]


def load():
    directory = ATOMS / A578
    spec = importlib.util.spec_from_file_location("_cdelta_578", directory / "atom.py")
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


async def engine(module):
    """A real 578 with a real config, clock and permitted account."""
    bus = Bus()
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=578, config=dict(manifest(A578)["config"]),
                                      logger=_Logger(), publish=bus.publish,
                                      subscribe=bus.subscribe))
    await atom.start()
    await atom._on_external({"official_time": 1000.0, "account_id": ACC, "trade_allowed": True})
    return atom, bus


def target(delta: float = 0.25) -> dict:
    # Item 4-10 contract: 578 refuses a snapshot that cannot prove it was
    # produced after the consumer resumed. Migrated by hand, per position.
    return {"account_id": ACC, "symbol": SYM, "status": "READY", "action": "ADD",
            "delta_buy": delta, "delta_sell": 0.0, "reference_price": 2000.0,
            # No `snapshot_id` on purpose: this guard's `nosnap` marker is part
            # of the identity it freezes, and adding one changed `request_id`.
            # Only the 4-10 stamp is migrated here.
            "produced_at": 9_000_000_000.0, "producer_epoch": 9_000_000_000.0,
            "sequence": 1,
            "stop_distance_frac": 0.01, "target_net": delta, "current_net": 0.0,
            "delta_net": delta, "risk_budget": 100.0}


async def details(atom) -> dict:
    return dict((await atom.health_check()).details or {})


def structural() -> int:
    print("=" * 82)
    print("أ) الحواجز البنيويّة — الرؤية تُضاف، وقرار الإرسال لا يُمَسّ")
    print("=" * 82)
    bad = 0
    src = (ATOMS / A578 / "atom.py").read_text(encoding="utf-8")

    # The details may be built in a support module; the contract is that the
    # atom SURFACES them, so the whole atom folder counts as its source.
    whole = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ATOMS / A578).glob("*.py")))
    print("  ١· العدّادات الجديدة معروضة في الصحّة:")
    for key in HEALTH_KEYS:
        ok = key in whole
        bad += 0 if ok else 1
        print("      %-22s %s" % (key, "✓ موجود" if ok else "✗ مفقود"))
    ok = "self._delta_failures.view(" in src
    bad += 0 if ok else 1
    print("      %-22s %s" % ("الذرّة تعرضها فعلًا", "✓" if ok else "✗ غير موصولة بالصحّة"))

    print("  ٢· أسطر القرار مجمّدة حرفًا بحرف:")
    for label, line in FROZEN_LINES:
        ok = line in src
        bad += 0 if ok else 1
        print("      %-22s %s" % (label, "✓ كما هو" if ok else "✗ تغيّر!"))

    print("  ٣· لا زوج مصطنع ولا تصعيد جديد:")
    emit = src.split("async def _emit_open")[1].split("async def _on_requested")[0]
    adjust = src.split("async def _adjust_side")[1].split("def _protection_blocks")[0]
    checks = (
        ("لا pair_id في مسار الفتح", '"pair_id"' not in emit),
        ("لا pair_id في مسار التخفيض", '"pair_id"' not in adjust),
        ("نشر التصعيد مرّة واحدة", src.count("publish(EVENT_ESCALATION") == 1),
        ("نشر حالة الزوج مرّة واحدة", src.count("publish(EVENT_PAIR_STATE") == 1),
        ("لا أمر أصل جديد", src.count("publish(EVENT_ASSET_COMMAND") == 1),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-30s %s" % (label, "✓" if ok else "✗"))

    print("  ٤· الحارس الفيضانيّ مجمَّد ببصمته:")
    got = hashlib.sha256((ATOMS / A578 / "flood_guard.py").read_bytes()).hexdigest()
    ok = got == FLOOD_GUARD_SHA
    bad += 0 if ok else 1
    print("      %-30s %s  %s" % ("flood_guard.py", got[:16], "✓ لم يُمَسّ" if ok else "✗ تغيّر!"))

    print("  ٥· الجيران الممنوعون:")
    for folder, version in FROZEN_NEIGHBOURS.items():
        got_v = str(manifest(folder).get("version"))
        ok = got_v == version
        bad += 0 if ok else 1
        print("      %-30s %-8s %s" % (folder.split("_")[0], got_v,
                                       "✓ لم تُمَسّ" if ok else "✗ تغيّرت عن %s!" % version))
    for folder, label, anchor in NEIGHBOUR_ANCHORS:
        ok = anchor in (ATOMS / folder / "atom.py").read_text(encoding="utf-8")
        bad += 0 if ok else 1
        print("      %-30s %s" % (label, "✓" if ok else "✗ تغيّر!"))
    return bad


async def main_async() -> int:
    module = load()
    bad = structural()

    print("\n" + "-" * 82)
    print("ب) طرف-لطرف — ذرّة 578 حقيقيّة على ناقل حقيقيّ")
    print("-" * 82)

    atom, bus = await engine(module)
    fresh = await details(atom)
    missing = [k for k in HEALTH_KEYS if k not in fresh]
    bad += len(missing)
    print("  ٠· ذرّة نظيفة تعرض المفاتيح كلّها قبل أيّ فشل: %s  (delta_failed=%s)"
          % ("✓" if not missing else "✗ ناقص %s" % missing, fresh.get("delta_failed")))

    await atom._on_target(target())
    orders = bus.of(module.EVENT_REQUEST)
    print("  ١· الأمر المُصدَر — يجب أن يبقى كما هو حرفًا بحرف:")
    if len(orders) != 1:
        bad += 1
        print("      ✗ عدد الأوامر = %d (المتوقّع 1)" % len(orders))
    else:
        diff = [k for k, v in FROZEN_ORDER.items() if orders[0].get(k) != v]
        extra = sorted(set(orders[0]) - set(FROZEN_ORDER))
        bad += len(diff) + len(extra)
        print("      حقول مختلفة = %s · حقول زائدة = %s  %s"
              % (diff or "لا شيء", extra or "لا شيء",
                 "✓ السلوك لم يتغيّر" if not diff and not extra else "✗"))

    before = await details(atom)
    count = len(bus.log)
    if not orders:
        # م-58 (2026-08-28): كان انهيارًا IndexError — 578 لم تُصدر الأمر بعقد
        # الاختبار المتقادم (هوية v5.x غيّرت الشكل) — فشل مُشخَّص صادق.
        print("  ✗ 578 لم تُصدر أمرًا بعقد السيناريو المتقادم (م-58) — ترحيل لاحق")
        return bad + 1
    await atom._on_rejected({"request_id": orders[0]["request_id"], "account_id": ACC,
                             "symbol": SYM, "reason": "TEST_REJECT"})
    after = await details(atom)
    print("\n  ٢· رفض أمر فرق — يجب أن يُرى، وألّا يولّد حدثًا واحدًا:")
    seen = int(after.get("delta_failed") or 0) == 1
    silent = len(bus.log) == count
    no_esc = not bus.of(module.EVENT_ESCALATION) and not bus.of(module.EVENT_PAIR_STATE)
    no_pair = int(after.get("pairs") or 0) == 0
    for label, ok in (("عدّاد الفشل صار 1", seen), ("صفر حدث جديد", silent),
                      ("صفر تصعيد وصفر حالة زوج", no_esc), ("pairs ما زال 0", no_pair)):
        bad += 0 if ok else 1
        print("      %-28s %s" % (label, "✓" if ok else "✗"))
    print("      قبل: delta_failed=%s · بعد: delta_failed=%s · سبب=%s"
          % (before.get("delta_failed"), after.get("delta_failed"),
             after.get("delta_last_reason")))

    print("\n  ٣· فشل إرسال — التراجع المعروض هو تراجع الحارس نفسه، لا عدّادي:")
    await atom._on_send_failure({"request_id": orders[0]["request_id"], "account_id": ACC,
                                 "symbol": SYM, "reason": "BRIDGE_DOWN"})
    view = await details(atom)
    key = "%s|%s" % (ACC, SYM)
    guard_n = atom._flood_guard.failing(ACC, SYM)
    shown = (view.get("guard_failing") or {}).get(key)
    backoff = (view.get("guard_backoff_s") or {}).get(key)
    expected = min(600.0, float(view.get("resend_hold_s") or 2.0) * 2.0 ** max(1, guard_n))
    for label, ok in (
            ("عدّاد الفشل صار 2", int(view.get("delta_failed") or 0) == 2),
            ("عدّاد الحارس مطابق للحقيقة", shown == guard_n and guard_n == 1),
            ("مدّة التراجع محسوبة من الحارس", backoff is not None and abs(backoff - expected) < 1e-9)):
        bad += 0 if ok else 1
        print("      %-32s %s" % (label, "✓" if ok else "✗"))
    print("      delta_failed=%s · guard_failing=%s · backoff=%ss"
          % (view.get("delta_failed"), shown, backoff))
    print("      ← الفجوة صارت مرئيّة: الرفض يرفع عدّادي ولا يرفع عدّاد الحارس")

    print("\n  ٤· نفس الهدف يُعاد — الكتم كما هو، ولا أمر جديد:")
    atom2, bus2 = await engine(module)
    await atom2._on_target(target())
    n1 = len(bus2.of(module.EVENT_REQUEST))
    await atom2._on_target(target())
    n2 = len(bus2.of(module.EVENT_REQUEST))
    ok = n1 == 1 and n2 == 1 and (await details(atom2)).get("flood_suppressed") == 1
    bad += 0 if ok else 1
    print("      أوامر بعد الهدف الأوّل=%d · بعد تكراره=%d · مكتوم=%s  %s"
          % (n1, n2, (await details(atom2)).get("flood_suppressed"), "✓" if ok else "✗"))

    print("\n  ٥· ساق زوج بشكل 576 — الآلة الساكنة لم تُكسَر:")
    atom3, bus3 = await engine(module)
    await atom3._on_requested({"request_id": "pair-A-GOLD-1-buy-a1", "account_id": ACC,
                               "symbol": SYM, "side": "BUY", "volume": 0.31,
                               "pair_id": "pair-A-GOLD-1", "leg_role": "BUY",
                               "attempt": 1, "pair_required": True, "pair_volume": 0.31,
                               "protection_mode": "NEUTRAL_HEDGE"})
    view3 = await details(atom3)
    states = bus3.of(module.EVENT_PAIR_STATE)
    ok = int(view3.get("pairs") or 0) == 1 and len(states) == 1 and int(view3.get("delta_failed") or 0) == 0
    bad += 0 if ok else 1
    print("      pairs=%s · حالات الزوج=%d · delta_failed=%s  %s"
          % (view3.get("pairs"), len(states), view3.get("delta_failed"), "✓" if ok else "✗"))

    print("\n" + "=" * 82)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: فشل الفرق صار مرئيًّا، ولا شيء آخر تحرّك — لا إرسال ولا إعادة ولا تصعيد.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
