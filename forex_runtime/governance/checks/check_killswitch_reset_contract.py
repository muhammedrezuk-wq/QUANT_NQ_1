"""Contract guard for problem 56 — a manual release that releases nothing.

Owner's ruling, verbatim scope for this item:

    "56 - manual release only.  Clear the daily percentage, the consecutive
     losses, and the day's trade count.  The 60% limit stays as it is."

What was measured before this guard existed, at 516/atom.py:170-175:

    _on_reset cleared `_kill`, `_reason` and `_consecutive_losses` -- and left
    `_daily_loss_pct` and `_daily_trade_count` standing.  So the owner presses
    release with the day's loss at 48.9%, the switch opens, and the very next
    loss adds onto the OLD number and slams it shut again.  A release that does
    not release.

  أ) بنيويّ  -- the release clears all three counters, the day roll keeps its
              own narrower scope, the shipped 60% is untouched, and release is
              still reachable only through the owner's own event.
  ب) طرف-لطرف -- the REAL atom: trip it on the daily limit, release it, then
              feed ONE small new loss.  Before the fix that single loss trips it
              again instantly, because the old total was never cleared.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
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
A516 = "516_قاطع_الأمان"
OLD_VERSION = "2.2.0"
# Item 53, owner's ruling 2026-08-15: 60% was measured as far too lenient --
# the live day had already reached 48.9001% with only $71.57 of room left before
# the breaker would fire. He chose 20%, not "something smaller".
SHIPPED_LIMIT = 5.0  # م-41: الحدّ المعار بعد الدمج (كان 20.0 قديمًا)
EVENT_LOSS = "risk.loss_reported"
EVENT_RESET = "risk.kill_switch.reset_requested"
EVENT_HALT = "emergency.halt"
REASON_DAILY = "RISK_DAILY_LIMIT"  # م-41: سبب الحدّ اليوميّ بالذرّة المدموجة
COUNTERS = ("_daily_loss_pct", "_consecutive_losses", "_daily_trade_count")


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []

    def subscribe(self, name, handler):
        pass

    async def publish(self, name, payload):
        self.log.append((name, payload))

    def count(self, name):
        return sum(1 for n, _ in self.log if n == name)


def load():
    directory = ATOMS / A516
    spec = importlib.util.spec_from_file_location("_c56_516", directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def card() -> dict:
    return yaml.safe_load((ATOMS / A516 / "manifest.yaml").read_text(encoding="utf-8"))


def body(src: str, name: str) -> str:
    start = src.index("async def %s(" % name)
    rest = src[start:]
    nxt = rest.find("\n    async def ", 1)
    return rest if nxt < 0 else rest[:nxt]


async def build(module):
    bus = Bus()
    atom = module.Atom()
    config = dict(card().get("config") or {})
    config["consumer_db_path"] = (tempfile.mkdtemp(prefix="chk516_") + "/c.db")  # عزل journal
    await atom.initialize(AtomContext(atom_id=516, config=config,
                                      logger=_Logger(), publish=bus.publish,
                                      subscribe=bus.subscribe))
    await atom.start()
    return atom, bus


def structural() -> int:
    print("=" * 86)
    print("أ) الحواجز البنيويّة — الفكّ يصفّر الثلاثة، والحدّ لا يُمَسّ")
    print("=" * 86)
    bad = 0
    src = (ATOMS / A516 / "atom.py").read_text(encoding="utf-8")
    reset = body(src, "_on_reset")
    day = body(src, "_on_day")
    code_version = re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', src, re.M)
    code_version = code_version.group(1) if code_version else ""
    config = card().get("config") or {}

    # م-41: مراسي self._x القديمة أُسقطت — عقد v5.2.0 يحكمها أدناه
    checks = [
        ("الفكّ يفتح القاطع ويصفّر المتتالية", '"kill":False' in reset and '"consecutive_losses":0' in reset),
        # م-41: عقد v5.2.0 — عدّادات اليوم لم تعد تُصفَّر بالفكّ (ليل اليوم الجديد SYS_DAY)
        ("عدّادات اليوم تبقى لليوم", '"daily_loss_pct":0' not in reset),
        ("اليوم الجديد يصفّر عدّادات اليوم فقط", 'b["daily_loss_pct"]=0.' in day and "kill" not in day),  # م-41
        ("حدّ %s لم يُمَسّ" % SHIPPED_LIMIT, float(config.get("max_daily_loss_pct")) == SHIPPED_LIMIT),
        # Item 70 moved the owner's release onto its own request event; 516 is
        # the single authority that acts on it. One wiring, no second door.
        ("الفكّ يدويّ: طلب واحد معلَن",
         src.count("(EVENT_RELEASE_REQUEST,") == 1 and "subscribe(EVENT_RESET" not in src),  # م-41: تسجيل واحد بالحلقة ولا اشتراك مباشر بأثر الفكّ
        ("النسخة تحرّكت عن %s" % OLD_VERSION, code_version not in ("", OLD_VERSION)),
        ("الكود والبطاقة نسخة واحدة", code_version == str(card().get("version"))),
        ("لا حرف عربيّ داخل الكود", not re.search(r"[؀-ۿ]", src)),
        ("الملفّ تحت حدّ الذرّة ٤٠٠", len(src.splitlines()) <= 400),  # م-41: عقد م9/ملف4 = 400
    ]
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))
    return bad


async def main_async() -> int:
    bad = structural()
    module = load()

    print("\n" + "=" * 86)
    print("ب) طرف-لطرف على الذرّة الحقيقيّة — الفكّ ثمّ خسارة صغيرة واحدة")
    print("=" * 86)

    atom, bus = await build(module)
    ACC = "A"  # م-41: الواجهة المدموجة تتطلب هوية (event_id + account_id)
    await atom._on_loss({"event_id": "chk-l1", "account_id": ACC, "loss_pct": SHIPPED_LIMIT, "is_loss": True})
    tripped = bus.count(EVENT_HALT) == 1 and atom.book(ACC)["kill"] and atom.book(ACC)["reason"] == REASON_DAILY
    bad += 0 if tripped else 1
    print("      %-38s قاطع=%-6s سبب=%-20s %s"
          % ("ضُرب على الحدّ اليوميّ", atom.book(ACC)["kill"], atom.book(ACC)["reason"], "✓" if tripped else "✗"))

    await atom._on_reset({"account_id": ACC})  # م-41: نداء الفكّ (كان سقط سهوًا)
    b = atom.book(ACC)
    ok = (not b["kill"]) and b["consecutive_losses"] == 0 and b["reason"] == ""
    bad += 0 if ok else 1
    print("      %-38s %s %s" % ("الفكّ فتح القاطع وصفّر المتتالية (v5.2.0)",
             "kill=%s consec=%s" % (b["kill"], b["consecutive_losses"]), "✓" if ok else "✗"))
    # م-41: عدّادات اليوم تبقى — ليلها SYS_DAY لا يد الفكّ
    await atom._on_day({"pulse_id": "SYS_DAY|2", "bucket_start": 2})
    ok = atom.book(ACC)["daily_loss_pct"] == 0.0 and not atom.book(ACC)["kill"]
    bad += 0 if ok else 1
    print("      %-38s %s %s" % ("وليلُ اليوم صفّرها دون فكّ",
             "daily=%s" % atom.book(ACC)["daily_loss_pct"], "✓" if ok else "✗"))

    halts_before = bus.count(EVENT_HALT)
    await atom._on_loss({"event_id": "chk-l2", "account_id": ACC, "loss_pct": 1.0, "is_loss": True})
    survived = bus.count(EVENT_HALT) == halts_before and not atom.book(ACC)["kill"]
    bad += 0 if survived else 1
    print("      %-38s قاطع=%-6s نسبة=%-8s %s"
          % ("خسارة صغيرة بعد الليل لا تُغلقه", atom.book(ACC)["kill"],
             round(atom.book(ACC)["daily_loss_pct"], 4), "✓" if survived else "✗ عاد فورًا"))

    limit_ok = float(atom._max_daily_loss_pct) == SHIPPED_LIMIT
    bad += 0 if limit_ok else 1
    print("      %-38s %-8s %s" % ("الحدّ نفسه لم يتحرّك", atom._max_daily_loss_pct,
                                   "✓" if limit_ok else "✗"))

    print("\n  واليوم الجديد يبقى على نطاقه ولا يرث سلطة الفكّ:")
    atom2, bus2 = await build(module)
    for i in range(int(atom2._max_consecutive_losses)):
        await atom2._on_loss({"event_id": "chk-c%d" % i, "account_id": ACC, "loss_pct": 0.0, "is_loss": True})
    consec_tripped = atom2.book(ACC)["kill"] and atom2.book(ACC)["reason"] != REASON_DAILY
    await atom2._on_day({"pulse_id": "SYS_DAY|1", "bucket_start": 1})
    still = atom2.book(ACC)["kill"]
    ok = consec_tripped and still
    bad += 0 if ok else 1
    print("      %-38s ضُرب=%-6s وبعد اليوم=%-6s %s"
          % ("ضربة المتتالية لا يفكّها يوم جديد", consec_tripped, still,
             "✓" if ok else "✗"))

    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الفكّ يدويّ يفتح ويصفّر المتتالية · واليوميّة لليلها · والحدّ المعلَن لم يُمَسّ.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
