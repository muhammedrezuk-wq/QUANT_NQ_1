"""Contract guard for problem 25 — a declared setting must actually be read.

Owner's ruling 2026-08-15, verbatim:

    "25: 512 reads `price_decimals` for real, from the declared source."
    "For each item: a guard that falls -> the change -> the breaks -> the full
     check.  And no fix may create a new violation."

What was measured before this guard existed:

    512's card declares `price_decimals` under `required`, integer 0..12, with
    the value 8 -- while the code rounds with a hardcoded module constant
    `PRICE_DP = 8`.  The setting was therefore decoration: the owner could edit
    it from the panel, the card would accept it, the version would bump, the
    atom would hot reload, and NOTHING would change.  A card that lies.

  أ) بنيويّ  -- the value comes from the config, not from a constant, the
              declaration stays required, and the shipped value stays 8.
  ب) سلوكيّ  -- the SAME ledger through two atoms configured 2 and 6 produces
              differently rounded prices, and each matches its own setting.
              A decorative setting cannot pass this.
  ج) الجوار  -- 581, the only other consumer of `risk.asset_stop.state`, is
              untouched, and the published field names do not move.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
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
A512 = "512_الوقف_الهيكلي"
A581 = "581_محرك_فرق_المركز"
FROZEN_581 = "2.9.0"
SHIPPED_DECIMALS = 8
ROUNDED_FIELDS = ("room", "delta_price", "average_entry", "stop_price")
BAD_MARKER = "BAD_PRICE_DECIMALS"
ACC, SYM = "A", "GOLD"


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

    def last(self, name):
        rows = [p for n, p in self.log if n == name]
        return rows[-1] if rows else None


def load():
    directory = ATOMS / A512
    spec = importlib.util.spec_from_file_location("_c25_512", directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def card(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


async def publish_with(module, decimals, drop: bool = False):
    """نفس الدفتر بالضبط، وإعداد مختلف — الفرق يجب أن يظهر بالمخرَج."""
    bus = Bus()
    atom = module.Atom()
    config = dict(card(A512).get("config") or {})
    if drop:
        config.pop("price_decimals", None)
    else:
        config["price_decimals"] = decimals
    await atom.initialize(AtomContext(atom_id=512, config=config, logger=_Logger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    await atom.start()
    # `READY` تحتاج: الميزانيّة و`K` و`cost` و`v_net` و**`w`** (الوزن) و`vpu`.
    # أوّل محاولة أعطت INCOMPLETE_LEDGER لأنّي أغفلتُ `w`، فكان الحاجز أجوف
    # يسقط للسبب الخطأ. الأرقام مختارة لتنتج كسورًا طويلة يظهر فيها التقريب.
    await atom._on_ledger({"ledgers": [{
        "account_id": ACC, "symbol": SYM, "asset_canonical": SYM,
        "risk_budget": 100.0, "R": 100.0, "budgeted": True,
        "v_net": 0.3, "w": 600.0369369369, "vpu": 1.0,
        "K": 0.0, "cost": 0.0, "X": 0.0, "open_legs": 1,
    }], "count": 1})
    return bus.last("risk.asset_stop.state")


def decimals_of(value) -> int | None:
    if not isinstance(value, float):
        return None
    text = repr(value)
    return len(text.split(".")[1]) if "." in text and "e" not in text else 0


def structural() -> int:
    print("=" * 86)
    print("أ) الحواجز البنيويّة — الإعداد يُقرأ، والإعلان يبقى إلزاميًّا")
    print("=" * 86)
    bad = 0
    src = (ATOMS / A512 / "atom.py").read_text(encoding="utf-8")
    schema = card(A512).get("config_schema") or {}
    config = card(A512).get("config") or {}
    checks = (
        ("القيمة تُقرأ من الإعداد", 'config["price_decimals"]' in src or
         'config.get("price_decimals"' in src or 'cfg["price_decimals"]' in src or
         'cfg.get("price_decimals"' in src),
        ("لا ثابت مكتوب يقرّر التدوير", not re.search(r"^PRICE_DP\s*=", src, re.M)),
        ("الرفض بإعلان حالة لا باستثناء", "raise" not in src.split("async def initialize")[1].split("async def start")[0]),
        ("الإعلان ما زال إلزاميًّا", "price_decimals" in (schema.get("required") or [])),
        ("والقيمة المشحونة لم تتغيّر", config.get("price_decimals") == SHIPPED_DECIMALS),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))

    got = str(card(A581).get("version"))
    ok = got == FROZEN_581
    bad += 0 if ok else 1
    print("      %-38s %-8s %s" % ("581 (المستهلك الآخر) لم تُمَسّ", got, "✓" if ok else "✗"))
    return bad


async def main_async() -> int:
    bad = structural()
    module = load()

    print("\n" + "=" * 86)
    print("ب) سلوكيّ — نفس الدفتر بإعدادين، والمخرَج يتبع الإعداد فعلًا")
    print("=" * 86)
    # ثلاثة إعدادات كما أمر — لا اثنان.
    outputs = {}
    for decimals in (2, 5, 8):
        outputs[decimals] = await publish_with(module, decimals)
        if not outputs[decimals]:
            print("      ✗ لم يُنشَر شيء عند %d" % decimals)
            return 1

    for field in ROUNDED_FIELDS:
        seen = {d: outputs[d].get(field) for d in (2, 5, 8)}
        respected = all(decimals_of(seen[d]) is not None and decimals_of(seen[d]) <= d
                        for d in (2, 5, 8))
        bad += 0 if respected else 1
        print("      %-14s ٢=%-10s ٥=%-14s ٨=%-16s %s"
              % (field, seen[2], seen[5], seen[8], "✓" if respected else "✗ لا يتبع الإعداد"))

    distinct = sum(1 for f in ROUNDED_FIELDS
                   if len({outputs[d].get(f) for d in (2, 5, 8)}) == 3)
    bad += 0 if distinct else 1
    print("      %-38s %d %s" % ("حقول أعطت ثلاث قيم مختلفة", distinct,
                                 "✓" if distinct else "✗ الإعداد ما زال زينة"))

    same_fields = set(outputs[2]) == set(outputs[8])
    bad += 0 if same_fields else 1
    print("      %-38s %s" % ("أسماء الحقول لم تتحرّك", "✓" if same_fields else "✗"))

    print("\n  والقيمة الفاسدة fail-closed — **بإعلان حالة** لا برمي استثناء (المادّة ٨):")
    print("      ولا ارتداد صامت إلى ٨: لا سعر وقفٍ يخرج أصلًا.")
    for label, value in (("مفقودة", None), ("سالبة", -1),
                         ("نصّ", "eight"), ("عشريّة", 2.5), ("منطقيّة", True)):
        crashed = False
        state = None
        try:
            state = await publish_with(module, value, drop=(value is None))
        except Exception:                                        # noqa: BLE001
            crashed = True
        if label == "مفقودة":
            # الإعداد إلزاميّ بالبطاقة، فغيابه خطأ عقد لا خطأ قيمة.
            ok = crashed
            print("      %-12s ⇒ %s" % (label, "✓ عقد ناقص يُرفض" if ok else "✗ قُبل!"))
        else:
            no_stop = bool(state) and state.get("stop_price") is None \
                and state.get("status") != "READY" and BAD_MARKER in (state.get("warnings") or [])
            ok = (not crashed) and no_stop
            print("      %-12s ⇒ %s" % (label, "✓ لا وقف + تحذير معلَن" if ok
                                        else "✗ %s" % ("انهارت" if crashed else state)))
        bad += 0 if ok else 1

    print("\n  والصحّة تقول الحقيقة — لا امتناع صامت:")
    bus = Bus()
    atom = module.Atom()
    broken = dict(card(A512).get("config") or {})
    broken["price_decimals"] = -1
    await atom.initialize(AtomContext(atom_id=512, config=broken, logger=_Logger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    await atom.start()
    health = await atom.health_check()
    ok = str(getattr(health.state, "name", health.state)).upper() == "UNHEALTHY" \
        and BAD_MARKER in str(health.message)
    bad += 0 if ok else 1
    print("      حالة=%-10s رسالة=%-24s %s"
          % (getattr(health.state, "name", health.state), health.message, "✓" if ok else "✗"))

    print("\n  ولا مستهلك يعيد إدخال ثابت التقريب من الباب الخلفيّ:")
    leaked = []
    for folder in (A512, A581):
        for path in (ATOMS / folder).glob("*.py"):
            if re.search(r"\bPRICE_DP\b", path.read_text(encoding="utf-8")):
                leaked.append("%s/%s" % (folder.split("_")[0], path.name))
    bad += len(leaked)
    print("      %-38s %s" % ("PRICE_DP اختفى من 512 و581",
                              "✓" if not leaked else "✗ " + " · ".join(leaked)))

    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الإعداد المعلَن يُقرأ ويُطيَع — لا بطاقة تكذب.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
