"""Contract guard for problem 55 — an account state that ages in silence.

What was measured live (2026-08-15, payload captured off /ws/events):

    measured_at = 1786570801   vs   publish time = 1786791092
    => 220,291 s = 61.2 HOURS old, and published ONCE in a 180-second window.

619 returned early whenever `updated_at` had not moved (atom.py:138), so once
the EA stopped writing, the account state simply went quiet -- and every risk
number downstream kept using a two-and-a-half-day-old equity with no way to
know. Silence read exactly like health.

The fix measures and declares; it does not invent. The row's age is published
as a number, `stale` is decided against a DECLARED threshold, and while the
state is stale it is re-published so the age stays live instead of frozen at
the last value anyone happened to see.

  أ) بنيويّ  -- the age and the staleness flag exist, the threshold is declared
              config, and the clock comes from the official pulse (item 10),
              never from a wall clock of its own.
  ب) طرف-لطرف -- the REAL atom against a REAL bridge row: a fresh row publishes
              with stale=false; a frozen row still publishes, with a rising age
              and stale=true. A silent atom cannot pass the second case.

Exit 1 on any divergence.
"""
from __future__ import annotations

import asyncio
import importlib.util
import re
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
A619 = "619_حالة_الحساب"
OLD = "2.1.0"
EVENT_OUT = "platform.account.state"
PULSE = "SYS_SECOND"
NOW = 1_000_000.0
MAX_AGE = 300.0


class _Logger:
    def __getattr__(self, name):
        return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.log = []
        self.wired = {}

    def subscribe(self, name, handler):
        self.wired[name] = handler

    async def publish(self, name, payload):
        self.log.append((name, payload))

    def rows(self, name):
        return [p for n, p in self.log if n == name]


def card() -> dict:
    return yaml.safe_load((ATOMS / A619 / "manifest.yaml").read_text(encoding="utf-8"))


def code() -> str:
    src = (ATOMS / A619 / "atom.py").read_text(encoding="utf-8")
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


def load():
    directory = ATOMS / A619
    spec = importlib.util.spec_from_file_location("_c55_619", directory / "atom.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(directory))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(directory))
    return module


def make_bridge(path: str, updated_at: float) -> None:
    conn = sqlite3.connect(path)
    # The atom reads `WHERE id = 1`; a table without that column simply returned
    # no row, and the harness read the resulting silence as the defect itself.
    conn.execute("CREATE TABLE account (id INTEGER PRIMARY KEY, "
                 "account_id TEXT, balance REAL, equity REAL, "
                 "margin REAL, free_margin REAL, margin_level REAL, currency TEXT, "
                 "leverage INTEGER, open_count INTEGER, broker TEXT, account_server TEXT, "
                 "margin_mode TEXT, trade_allowed INTEGER, connected INTEGER, updated_at REAL)")
    conn.execute("INSERT INTO account VALUES (1,'52992818',644.84,644.84,0.0,644.84,0.0,"
                 "'USD',5000,0,'Raw Trading Ltd','ICMarketsSC-Demo','HEDGING',1,1,?)",
                 (updated_at,))
    conn.commit()
    conn.close()


def structural() -> int:
    print("=" * 86)
    print("أ) بنيويّ — العمر رقم معلَن، والعتبة إعداد، والساعة رسميّة")
    print("=" * 86)
    bad = 0
    src = code()
    schema = (card().get("config_schema") or {}).get("properties") or {}
    version = re.search(r'^ATOM_VERSION\s*=\s*"([^"]+)"', src, re.M)
    version = version.group(1) if version else ""
    checks = (
        ("العمر يُنشَر رقمًا", '"age_s"' in src),
        ("والتقادم علَم صريح", '"stale"' in src),
        ("والعتبة إعداد معلَن", "max_age_s" in schema),
        ("والساعة من النبضة الرسميّة", PULSE in (card().get("subscribes") or [])),
        ("ولا ساعة خاصّة بالذرّة",
         not re.search(r"time\s*\.\s*time\s*\(|__import__|datetime\s*\.\s*(now|utcnow)", src)),
        # Quiet is allowed for exactly one case: unchanged AND fresh. Anything
        # broader is the old silence coming back.
        ("ولا صمت إلّا لثابتٍ طازج", "if not changed and not stale:" in src),
        ("النسخة تحرّكت عن %s" % OLD, version not in ("", OLD)),
        ("الكود والبطاقة نسخة واحدة", version == str(card().get("version"))),
    )
    for label, ok in checks:
        bad += 0 if ok else 1
        print("      %-38s %s" % (label, "✓" if ok else "✗"))
    return bad


async def drive(module, db: str, now: float):
    bus = Bus()
    atom = module.Atom()
    config = dict(card().get("config") or {})
    config["db_path"] = db
    config["max_age_s"] = MAX_AGE
    await atom.initialize(AtomContext(atom_id=619, config=config, logger=_Logger(),
                                      publish=bus.publish, subscribe=bus.subscribe))
    pulse = bus.wired.get(PULSE)
    if pulse is not None:
        await pulse({"official_time": now})
    await atom._read_once()
    first = bus.rows(EVENT_OUT)
    bus.log.clear()
    if pulse is not None:
        await pulse({"official_time": now})
    await atom._read_once()          # the SECOND read: the row has not moved
    return first, bus.rows(EVENT_OUT)


async def main_async() -> int:
    bad = structural()
    module = load()

    print("\n" + "=" * 86)
    print("ب) طرف-لطرف — صفّ حقيقيّ لا يتحرّك: هل يبقى مرئيًّا؟")
    print("=" * 86)

    with tempfile.TemporaryDirectory() as tmp:
        fresh_db = str(Path(tmp) / "fresh.db")
        stale_db = str(Path(tmp) / "stale.db")
        make_bridge(fresh_db, NOW - 10.0)
        make_bridge(stale_db, NOW - 220_291.0)      # the measured 61.2 hours

        rows_fresh, quiet_fresh = await drive(module, fresh_db, NOW)
        _, rows_stale = await drive(module, stale_db, NOW)

        ok = len(rows_stale) >= 1
        bad += 0 if ok else 1
        print("      %-38s نشرات=%-4s %s"
              % ("الصفّ الجامد ما زال يُنشَر", len(rows_stale),
                 "✓" if ok else "✗ صمت"))

        last = rows_stale[-1] if rows_stale else {}
        ok = bool(last) and last.get("stale") is True and abs(
            float(last.get("age_s") or 0) - 220_291.0) < 1.0
        bad += 0 if ok else 1
        print("      %-38s عمر=%-12s متقادم=%-6s %s"
              % ("وعمره الحقيقيّ ظاهر", last.get("age_s"), last.get("stale"),
                 "✓" if ok else "✗"))

        first = rows_fresh[-1] if rows_fresh else {}
        ok = bool(first) and first.get("stale") is False and float(first.get("age_s") or -1) == 10.0
        bad += 0 if ok else 1
        print("      %-38s عمر=%-12s متقادم=%-6s %s"
              % ("والطازج يقول إنّه طازج", first.get("age_s"), first.get("stale"),
                 "✓" if ok else "✗"))

        ok = bool(first) and first.get("equity") == 644.84
        bad += 0 if ok else 1
        print("      %-38s %-12s %s" % ("والأرقام لم تتغيّر", (first or {}).get("equity"),
                                        "✓" if ok else "✗"))

        # Quiet is still allowed where it is honest: unchanged AND fresh.
        ok = len(quiet_fresh) == 0
        bad += 0 if ok else 1
        print("      %-38s نشرات=%-4s %s"
              % ("والثابت الطازج يبقى صامتًا", len(quiet_fresh), "✓" if ok else "✗"))

    print("\n" + "=" * 86)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: الحساب يقول عمره · والتقادم معلَن بعتبة · ولا صمت يُقرأ صحّة.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main_async()))
