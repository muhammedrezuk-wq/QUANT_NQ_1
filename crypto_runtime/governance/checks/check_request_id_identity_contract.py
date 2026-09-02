"""Contract guard for problem 63 — a request id must never be reborn while a
consumer still holds the old one.

Owner's ruling 2026-08-14, verbatim:

    "I do not adopt (a) in its current wording yet. The evidence proves the
     problem is real, but changing the request_id format needs a sharper guard
     first, because we do not want to fix 578's collision and create a conflict
     with any consumer that assumes the current format."
    "Before implementing I want a guard that proves 4 things:
       1. the current id is still literally accepted by 601, 585 and every
          consumer.
       2. the new format breaks no parsing, lookup or logging that depends on
          the number of parts of the id.
       3. after a 578 upgrade, a new order gets an identity CERTAINLY different
          from any previously live request_id.
       4. if official_time=None there is a deterministic and safe fallback; we
          do not want to fall back to _counter alone."
    "I do not approve (b) now.  If the collision can be killed at the source in
     578 without changing 585's contract, that is the cleaner path."

This guard asserts the PROPERTY, not any particular string shape, so whatever
format is chosen must satisfy it.

What was already measured, before this guard:
  * 578 has no snapshot/restore at all, so `_counter` restarts at 0 on a plain
    HOT RELOAD -- not only on boot.
  * A full core restart is the SAFE case: 578 and 585 reset together.  The only
    dangerous window is 578 reloaded ALONE while 585 still holds the id.
  * Reproduced on the real atoms: reserved 500 -> 1000 under ONE remembered
    hold; one release leaves 500 reserved with no key that can ever free it.
  * The mitigating layer is not a barrier: 585 zeroes `_reserved` on every
    `platform.account.state`, but 619 only publishes when the EA moves
    `updated_at`, and the EA has not written for 43 hours.

  ١ المستهلكون   -- nobody parses the id anywhere in the project or the EA.
  ٢ الصيغة الحاليّة -- crosses the real chain byte for byte.
  ٣ صيغة أطول    -- crosses it identically; part count is not load bearing.
  ٤ بعد الترقية   -- a reborn 578 must not reissue a LIVE id. (fails today)
  ٥ البديل        -- no official_time still yields a unique, deterministic id
                     that is not counter-only. (fails today)

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import re
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
A578, A585, A586 = "578_منفذ_التحوط", "585_حارس_الهامش", "586_بوابة_الرموز"
A516, A551, A584, A552 = ("516_قاطع_الأمان", "551_باني_الأمر",
                          "584_شرعية_الستوب", "552_مدقق_الأمر")
CHAIN = (A578, A586, A585, A516, A551, A584, A552)

ACC, SYM, PRICE = "52992818", "GOLD", 2000.0

# Names the id travels under -- including the short local aliases, because a
# consumer that renames it to `rid` and then splits it is exactly the failure
# this part exists to catch.  A name-only scan missed that on the first try.
ID_NAMES = ("request_id", "command_id", "req_id", "rid", "new_id", "order_id")

# Anything that reads STRUCTURE out of the id rather than treating it as a key.
PARSERS = (".split(", ".rsplit(", ".partition(", ".rpartition(",
           ".startswith(", ".endswith(", "re.match", "re.search",
           "re.findall", "re.fullmatch", "re.sub", "StringSplit",
           "StringSubstr", "StringFind")

# A blunt "[-" or "[:" flagged `ev(bus, EVENT)[-1]` on the same dense line as an
# id literal -- six false positives.  Slicing only counts when it is applied to
# the id ITSELF, so it is matched precisely.
_ID_ALT = "|".join(ID_NAMES)
SLICE_RE = re.compile(
    r'(?:\b(?:%s)\b|\[["\'](?:%s)["\']\]|\.get\(["\'](?:%s)["\']\)\s*)\s*\['
    % (_ID_ALT, _ID_ALT, _ID_ALT))

# 578 is the only atom this ruling allows to move.
FROZEN_578 = "3.0.0"
# Rebased 2026-08-15: item 4-6 moved 585 by the owner's order (a stale account
# may no longer certify margin). The barrier's meaning is unchanged -- item 63
# still does not touch 585 -- only its reference point moves, with a reason.
UNTOUCHED = {A585: "1.2.0", "583_لقطة_التنفيذ": "1.1.0",
             "601_كاتب_جسر_الدماغ": None}

# A control character inside 583's snapshot_id must never reach the bridge.
UNSAFE = "".join(chr(code) for code in range(32)) + "\x7f'\"\\;"

# Files that legitimately parse ids because they MEASURE them: the guards and
# the throwaway analysis scripts are not consumers of the contract.
SKIP_DIRS = ("backups", "venv", "node_modules", "var", "built", "سياق",
             "governance\\checks", "governance/checks", "governance\\docs",
             "governance/docs")


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

    def of(self, name):
        return [p for n, p in self.log if n == name]


def load_atom(folder: str):
    directory = ATOMS / folder
    tag = "_crid_" + folder.split("_")[0]
    spec = importlib.util.spec_from_file_location(tag, directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[tag] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def manifest(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


def sources():
    for path in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.mq5")) + list(ROOT.rglob("*.tsx")):
        text = str(path)
        if any(part in text for part in SKIP_DIRS):
            continue
        yield path


def opaque() -> tuple[int, int]:
    """مطلبه ١ و٢ — لا أحد في المشروع كلّه يفسّر بنية المعرّف."""
    print("=" * 88)
    print("١· المعرّف مبهم عند كل مستهلك — لا تقسيم ولا قصّ ولا افتراض أجزاء")
    print("=" * 88)
    bad = 0
    consumers = 0
    for path in sources():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        hits = []
        for number, line in enumerate(lines, 1):
            if not any(re.search(r"\b%s\b" % name, line) for name in ID_NAMES):
                continue
            hits.append(number)
            for token in PARSERS:
                if token in line:
                    bad += 1
                    print("      ✗ %s:%d يفسّر المعرّف بـ%s"
                          % (path.relative_to(ROOT), number, token))
            if SLICE_RE.search(line):
                bad += 1
                print("      ✗ %s:%d يقصّ المعرّف نفسه" % (path.relative_to(ROOT), number))
        if hits:
            consumers += 1
    print("      ملفّات تلمس المعرّف: %d · تفسّر بنيته: %d" % (consumers, bad))
    got = str(manifest(A578).get("version"))
    if got != FROZEN_578:
        bad += 1
    print("      نسخة 578 المعلَنة: %-8s %s" % (got, "✓" if got == FROZEN_578 else "✗"))
    for folder, version in UNTOUCHED.items():
        current = str(manifest(folder).get("version"))
        ok = version is None or current == version
        bad += 0 if ok else 1
        print("      %-26s %-8s %s" % ("ممنوع مسّها: " + folder.split("_")[0], current,
                                       "✓ لم تُمَسّ" if ok else "✗ تغيّرت عن %s!" % version))
    print("      %s" % ("✓ المعرّف مفتاح مبهم في كل موضع" if not bad
                        else "✗ مستهلك يعتمد على الشكل"))
    return bad, consumers


async def chain(request_id: str | None = None, official_time: float | None = 1000.0):
    """السلسلة الحقيقيّة كاملة: 578 → 586 → 585 → 516 → 551 → 584 → 552."""
    bus = Bus()
    atoms = {}
    for folder in CHAIN:
        module = load_atom(folder)
        atom = module.Atom()
        await atom.initialize(AtomContext(atom_id=int(folder.split("_")[0]),
                                          config=manifest(folder).get("config") or {},
                                          logger=_Logger(), publish=bus.publish,
                                          subscribe=bus.subscribe))
        await atom.start()
        atoms[folder] = atom
    spec = {"symbol": SYM, "tick_size": 0.01, "point": 0.01, "stops_level": 0.0,
            "volume_step": 0.01, "volume_min": 0.01, "tick_value": 0.01,
            "contract_size": 1.0}
    await bus.publish("market.symbol_specs", {"symbols": [spec]})
    await bus.publish("platform.account.state", {
        "account_id": ACC, "trade_allowed": True, "equity": 10000.0,
        "free_margin": 10000.0, "leverage": 100})
    if official_time is not None:
        await bus.publish("SYS_SECOND", {"official_time": official_time})

    if request_id is None:
        await bus.publish("execution.snapshot.state", {
            "account_id": ACC, "symbol": SYM, "status": "READY", "action": "ADD",
            "target_net": 0.2, "current_net": 0.0, "delta_net": 0.2,
            "reference_price": PRICE, "stop_distance_frac": 0.01,
            "produced_at": 9_000_000_000.0, "producer_epoch": 9_000_000_000.0,
            "sequence": 1,
            "risk_budget": 100.0, "snapshot_id": "snapshot-%s|%s-1" % (ACC, SYM)})
    else:
        # ساق زوج بشكل 576 بالضبط، لكن بمعرّف نختاره نحن.
        await bus.publish("execution.order.requested", {
            "request_id": request_id, "account_id": ACC, "action": "OPEN",
            "symbol": SYM, "side": "BUY", "volume": 0.2,
            "reference_price": PRICE, "stop_loss": None, "take_profit": None,
            "origin": "perpetual", "pair_id": "pair-x", "leg_role": "BUY",
            "attempt": 1, "pair_required": True, "pair_volume": 0.2,
            "protection_mode": "NEUTRAL_HEDGE", "purpose": "INITIAL_NEUTRAL"})
    for ask in bus.of("symbol.resolve.requested"):
        await bus.publish("symbol.resolve.result", {
            "request_id": ask.get("request_id"), "approved": True,
            "status": "RESOLVED", "logical_symbol": SYM, "asset_canonical": SYM,
            "broker_symbol": SYM, "spec": spec})
    return bus, atoms


def trail(bus) -> dict:
    """المعرّف كما وصل كل محطّة."""
    stages = (("578 طلب", "execution.order.requested"),
              ("586 حُلّ", "execution.order.resolved"),
              ("585 هامش", "risk.margin.validation.completed"),
              ("516 مخاطر", "risk.validation.completed"),
              ("551 بُني", "execution.order.built"),
              ("584 شرعيّ", "execution.order.legal"),
              ("552 مقفول", "execution.order.rejected"))
    out = {}
    for label, event in stages:
        rows = bus.of(event)
        out[label] = str(rows[-1].get("request_id")) if rows else None
    return out


async def crosses(label: str, request_id: str | None) -> tuple[int, dict]:
    bus, _ = await chain(request_id=request_id)
    seen = trail(bus)
    expected = request_id or seen["578 طلب"]
    bad = 0
    print("\n  %s" % label)
    for stage, got in seen.items():
        if got is None:
            print("      %-12s —" % stage)
            continue
        ok = got == expected
        bad += 0 if ok else 1
        print("      %-12s %s  %s" % (stage, got, "✓" if ok else "✗ تغيّر!"))
    return bad, seen


async def live_ids(official_time, snapshot_id) -> tuple[str, object]:
    """ذرّة 578 تُصدر أمرًا، وحارس الهامش يحتفظ بحجزه حيًّا."""
    module = load_atom(A578)
    bus = Bus()
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id=578, config=manifest(A578)["config"],
                                      logger=_Logger(), publish=bus.publish,
                                      subscribe=bus.subscribe))
    await atom.start()
    await atom._on_external({"official_time": official_time, "account_id": ACC,
                             "trade_allowed": True} if official_time is not None
                            else {"account_id": ACC, "trade_allowed": True})
    await atom._on_target({
        "account_id": ACC, "symbol": SYM, "status": "READY", "action": "ADD",
        "delta_buy": 0.2, "delta_sell": 0.0, "reference_price": PRICE,
        "stop_distance_frac": 0.01, "target_net": 0.2, "current_net": 0.0,
        # Item 4-10 contract: a snapshot proves it was produced after the
        # consumer resumed, or it is refused as history. Migrated here by hand,
        # per position -- a blind text replace corrupted this very file once.
        "produced_at": 9_000_000_000.0, "producer_epoch": 9_000_000_000.0,
        "sequence": 1,
        "delta_net": 0.2, "risk_budget": 100.0, "snapshot_id": snapshot_id})
    orders = bus.of(module.EVENT_REQUEST)
    return (str(orders[-1]["request_id"]) if orders else ""), atom


def snap(version: int) -> str:
    """583's real shape, control separator included."""
    return "snapshot-%s\x1f%s-%d" % (ACC, SYM, version)


async def main_async() -> int:
    bad, consumers = opaque()

    print("\n" + "=" * 88)
    print("٢+٣· الصيغة تعبر السلسلة الحقيقيّة حرفيًّا — والأجزاء ليست حمولة")
    print("=" * 88)
    n, base = await crosses("أ) الصيغة الحاليّة (٥ أجزاء)", None)
    bad += n
    n, _ = await crosses("ب) صيغة أطول (٧ أجزاء)",
                         "delta-%s-%s-buy-1786742401-7-1" % (ACC, SYM))
    bad += n
    n, _ = await crosses("ج) صيغة بلا شرطات أصلًا", "abcdef0123456789")
    bad += n

    print("\n" + "=" * 88)
    print("٤· بعد ترقية 578 وحدها — هل تولد هويّة مختلفة قطعًا؟")
    print("=" * 88)
    first, _ = await live_ids(1000.0, snap(1))
    print("  النسخة الأولى أصدرت: %s   (وحجزها ما زال حيًّا عند 585)" % first)
    cases = (
        ("ترقية بعد ثانية", 1001.0, snap(2)),
        ("ترقية داخل الثانية نفسها", 1000.0, snap(2)),
        # طبقة الزمن التي اختارها (أ-٢) فوق (أ-١): نفس اللقطة بالضبط.
        ("نفس اللقطة وثانية لاحقة", 1001.0, snap(1)),
    )
    for label, stamp, snapshot in cases:
        got, _ = await live_ids(stamp, snapshot)
        ok = bool(got) and got != first
        bad += 0 if ok else 1
        print("      %-42s %s  %s" % (label, got or "—",
                                      "✓ مختلف" if ok else "🔴 نفس المعرّف الحيّ"))
    # حكم المالك 2026-08-15: البند ٤-١٠ يعلو على ٦٣. قبل أوّل نبضة رسميّة لا
    # إرسال إطلاقًا، فلا هويّة تُختبر أصلًا — والحالة انتقلت من «هل تولد هويّة
    # مختلفة؟» إلى «هل يُمنع الأثر؟» أدناه.
    before_clock, _ = await live_ids(None, snap(2))
    bad += 0 if before_clock == "" else 1
    print("      %-42s %s  %s" % ("قبل أوّل نبضة (official_time=None)",
                                  before_clock or "لا أمر",
                                  "✓ لا إرسال (٤-١٠)" if before_clock == ""
                                  else "🔴 خرج أمر قبل الساعة"))

    print("\n  والمعرّف يخرج نظيفًا مهما كان شكل اللقطة:")
    dirty, _ = await live_ids(1000.0, snap(9))
    unsafe = [character for character in dirty if character in UNSAFE]
    bad += 0 if not unsafe else 1
    print("      لقطة فيها \\x1f ⇒ %s  %s"
          % (dirty, "✓ نظيف" if not unsafe else "🔴 مرّر %r إلى الجسر" % unsafe))

    print("\n" + "=" * 88)
    print("٥· بلا ساعة حيّة: لا إرسال إطلاقًا — والعقد عُدّل بحكمه لا بترقيع")
    print("=" * 88)
    # حكم المالك 2026-08-15 بنصّه، بعد أن أثبت القياس تعارض البندين:
    #   «٤-١٠ يعلو. لكن ليس بإلغاء ٦٣ فضفاضًا — نعدّل العقد ٦٣ نفسه:
    #    `official_time=None` ⇒ لا إرسال · لا يُنشأ request_id تنفيذيّ بديل ·
    #    ولا يُحتسب ذلك تخطّي هويّة في مسار التنفيذ · وبعد وصول الساعة الرسميّة
    #    تبدأ قابليّة إنشاء الأثر بهويّة مرتبطة بالحقبة.»
    # الشرط القديم كان يطلب «بديلًا حتميًّا» لأمر صار ممنوعًا من الوجود أصلًا،
    # فكان يقيس بندًا أُلغي عمليًّا. ولم يُضَف أيّ استثناء في 578 — بأمره الصريح.
    for label, stamp, snapshot in (("بلا لقطة وبلا زمن", None, None),
                                   ("بلقطة وبلا زمن", None, snap(5)),
                                   ("وبلقطة أخرى وبلا زمن", None, snap(6))):
        issued, _ = await live_ids(stamp, snapshot)
        ok = issued == ""
        bad += 0 if ok else 1
        print("      %-30s ⇒ %-22s %s"
              % (label, issued or "لا أمر",
                 "✓ لا إرسال" if ok else "🔴 خرج أمر قبل الساعة"))

    print("\n  وبعد وصول الساعة الرسميّة يستأنف بهويّة مرتبطة بالحقبة:")
    after, _ = await live_ids(2000.0, snap(7))
    ok = bool(after) and "2000" in after
    bad += 0 if ok else 1
    print("      %-30s ⇒ %-22s %s"
          % ("أوّل نبضة ثمّ لقطة", after or "لا أمر",
             "✓ أثر قابل للتتبّع" if ok else "🔴 لا هويّة مرتبطة بالزمن"))

    print("\n" + "=" * 88)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: المعرّف مبهم عند الجميع، ولا يُولَد من جديد ما دام حيًّا عند مستهلك.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
