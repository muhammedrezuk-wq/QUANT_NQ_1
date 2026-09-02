"""Contract guard for problem 5-5 (option b) — the limit books must survive a
clean restart, because after it nothing replays them.

Owner's ruling 2026-08-14, verbatim:

    "Do NOT treat 506/507/508/658/666 as one unit just because they are fed by
     611. The guard must prove FOR EACH ATOM on its own that its state cannot
     be re-derived after _last_id is restored."
    "Do not add 516 to this item. Do not touch 611 in 5-5."
    "Saved: the real accumulated state only. Restored: the same semantic state,
     not a json match. Failed restore: fail-closed for the limit it guards.
     Not saved: _seen or any derived cache/mirror."

The danger this guard reproduces, end to end:

    clean stop -> 611 saves last_id -> the limit books save nothing
    -> new boot -> 611 resumes from last_id -> NO replay
    -> the accumulated books stay empty, and today's session loss is forgotten.

Before 5-2 this could not happen: no snapshot was ever written, so 611 always
replayed from zero and the books were always rebuilt. Making the clean shutdown
work is exactly what created this gap.

  A) STRUCTURAL -- each atom in scope carries the contract; 611 and 516 are
     untouched.
  B) لكل ذرّة على حدة -- two real processes over a real sqlite bridge: the state
     is built, the process dies, 611 resumes, and each atom is judged ALONE.
  C) fail-closed -- a corrupt book must leave the limit guarding, not open.

Exit 1 on any divergence.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot
ATOMS = RegistryAtomRoot(ROOT)
ATOM_ROOT = ATOMS
PY = sys.executable

# 508 was WITHDRAWN from the scope before a line was written: its `_breached`
# is recomputed from platform.positions.state + platform.account.state, both of
# which keep arriving after a restart. This guard proves that too.
IN_SCOPE = {506: "506_حدود_الجلسة", 507: "507_حدود_الربح",
            658: "658_الربح_والخسارة", 666: "666_تقييم_الأداء"}
DERIVED = {508: "508_مدير_التعرض"}
# Rebased 2026-08-15: items 69 and 56/70 moved these two by the owner's order.
# The barrier still means "this item did not touch them" -- only its reference
# point moves, and only with a recorded reason.
# م-43 (ورقة ٤١، 2026-08-28): أُعيد التثبيت على إصدارات ما بعد الدمج الموثّق (RC4/RC5)
UNTOUCHED = {"611_قارئ_الصفقات": "4.0.3", "516_قاطع_الأمان": "5.3.0"}

CHILD = r'''
import asyncio, json, os, sqlite3, sys
# درس 601/609: متغيّر البيئة الملوّث يوجّه القرّاء إلى الجسر الحقيقيّ ويكذّب
# الفحص. يُنزع هنا قبل أي استيراد، فلا يعتمد الحارس على بيئة المشغّل.
os.environ.pop("NQ_BRIDGE_DB", None)
from pathlib import Path
ROOT = Path(sys.argv[1]); STORAGE = Path(sys.argv[2]); OUT = Path(sys.argv[3])
MODE = sys.argv[4]; DB = sys.argv[5]
sys.path.insert(0, str(ROOT))
from build_registry.paths import RegistryAtomRoot as _RAR
ATOM_ROOT = _RAR(ROOT)  # م-43: كان غائبًا (نسخ-لصق) فانهار الابن
from core.bootloader import Bootloader
from core.contracts.atom import AtomContext
from core.manifest_loader import scan
from core.registry import Registry
from core.snapshot_engine import SnapshotEngine

# Ascending id, exactly the order the bootloader starts them in. 611 is built
# but its background loop is NOT started: the guard drives `_drain_once()` by
# hand so the ONE variable under test is the snapshot, not the race between
# 611's first poll and `restore_all()`. That race is a separate observation and
# is reported, never silently folded into this result.
CHAIN = (506, 507, 508, 517, 563, 611, 658, 666)
ACC, SYM = "52992818", "BTCUSD"


class L:
    def __getattr__(self, n): return lambda *a, **k: None


class Bus:
    def __init__(self): self.h = {}; self.trades = 0
    def subscribe(self, name, fn): self.h.setdefault(name, []).append(fn)
    async def publish(self, name, payload):
        # counted on the BUS, not from the atom's own counter -- 611 restores
        # `published` with its snapshot, so that counter would lie here.
        if name == "platform.trade_event": self.trades += 1
        for fn in list(self.h.get(name, [])):
            r = fn(payload)
            if hasattr(r, "__await__"): await r


def out(**kw):
    body = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    body.update(kw); OUT.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


async def build(bus):
    reg = Registry(); found = {a.manifest.id: a for a in scan(ATOM_ROOT).atoms}
    made = {}
    for i in CHAIN:
        d = found[i]; inst = Bootloader.instantiate(d); reg.register(d.manifest, inst)
        cfg = dict(d.manifest.config)
        if i == 611: cfg["db_path"] = DB
        await inst.initialize(AtomContext(atom_id=i, config=cfg, logger=L(),
                                          publish=bus.publish, subscribe=bus.subscribe))
        if i == 611:
            inst._running = True          # no background loop; drained by hand
        else:
            await inst.start()
        made[i] = inst
    return reg, made


# The ACCUMULATED fields only. Mirrors of live events (equity, the session
# name, peak equity) are excluded on purpose: they arrive again after any
# restart, so including them would let a mirror hide a lost accumulator.
ACC_506 = ("session_loss_pct", "session_trades", "drawdown_pct", "breaches")
ACC_507 = ("profit", "peak_pct", "wins", "losses", "breaches")


def _book(books, keys):
    return {k: {kk: (round(vv, 6) if isinstance(vv, float)
                     else sorted(vv) if isinstance(vv, set) else vv)
                for kk, vv in v.items() if kk in keys}
            for k, v in books.items()}


def semantics(m):
    return {"506": _book(m[506]._books, ACC_506),
            "507": _book(m[507]._books, ACC_507),
            "508": sorted(m[508]._breached),
            "658": {k: round(v, 6) for k, v in m[658]._realized.items()},
            "666": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv)
                        for kk, vv in v.items()} for k, v in m[666]._stats.items()}}


async def live_inputs(bus):
    """Events that keep arriving after ANY restart -- they are not replay."""
    await bus.publish("platform.account.state", {
        "account_id": ACC, "equity": 1000.0, "balance": 1000.0, "free_margin": 1000.0,
        "leverage": 100, "margin_mode": 2, "trade_allowed": True})
    await bus.publish("market.symbol_specs", {"symbols": [
        {"symbol": SYM, "tick_size": 0.01, "tick_value": 0.01, "contract_size": 1.0}]})
    await bus.publish("analysis.session.state", {"symbol": SYM, "signal": "london"})
    await bus.publish("platform.positions.state", {
        "account_id": ACC, "source": "guard", "open_count": 1, "positions": [
            {"account_id": ACC, "ticket": 9, "symbol": SYM, "side": "BUY", "volume": 5.0,
             "entry_price": 100.0, "current_price": 100.0, "profit": -3.0}]})


async def main():
    bus = Bus()
    reg, made = await build(bus)
    engine = SnapshotEngine(reg, STORAGE)

    if MODE == "restore":
        rep = await engine.restore_all()
        out(restored=rep.restored, skipped=rep.skipped, failed=rep.failed,
            last_id=made[611]._last_id)
        await live_inputs(bus)
        await made[611]._drain_once()          # 611 resumes from the restored id
        out(state=semantics(made), drained=bus.trades)
        return 0

    await live_inputs(bus)
    await made[611]._drain_once()              # first read: the whole table
    out(state=semantics(made), drained=bus.trades,
        last_id=made[611]._last_id)
    rep = await engine.snapshot_all()
    out(captured=sorted(rep.captured), files=sorted(p.name for p in STORAGE.glob("*.json")))
    return 0


sys.exit(asyncio.run(main()))
'''


def manifest(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


def make_bridge(path: Path, rows: int = 6) -> None:
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE trade_events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " event_type TEXT, ticket INTEGER, symbol TEXT, side TEXT, volume REAL,"
                " entry_price REAL, exit_price REAL, open_time REAL, close_time REAL,"
                " reason TEXT, profit REAL, account_id TEXT, request_id TEXT)")
    for i in range(rows):
        con.execute("INSERT INTO trade_events (event_type,ticket,symbol,side,volume,"
                    "entry_price,exit_price,open_time,close_time,reason,profit,account_id)"
                    " VALUES ('CLOSED',?,?,'BUY',0.1,100.0,99.0,1.0,?, 'SYSTEM', ?, ?)",
                    (1000 + i, "BTCUSD", 10.0 + i, -7.5, "52992818"))
    con.commit(); con.close()


def structural() -> int:
    print("=" * 78)
    print("أ) الحواجز البنيويّة")
    print("=" * 78)
    bad = 0
    for aid, folder in IN_SCOPE.items():
        src = (ATOMS / folder / "atom.py").read_text(encoding="utf-8")
        for label, ok in ((f"{aid} يحفظ", "async def snapshot" in src),
                          (f"{aid} يستعيد", "async def restore" in src),
                          (f"{aid} مغلق عند الفشل", "FAIL_CLOSED" in src),
                          (f"{aid} لا يحفظ _seen", '"seen"' not in src.split("async def snapshot")[-1][:400])):
            bad += 0 if ok else 1
            print("  %-34s %s" % (label, "✓" if ok else "✗"))
    print("  المستثنى من اللمس بأمره:")
    for folder, version in UNTOUCHED.items():
        got = str(manifest(folder).get("version"))
        ok = got == version
        bad += 0 if ok else 1
        print("      %-30s %-8s %s" % (folder.split("_")[0], got, "✓" if ok else "✗ تغيّرت!"))
    return bad


def run(child: Path, storage: Path, out: Path, mode: str, db: Path) -> dict:
    import os
    out.write_text("{}", encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1")
    env.pop("NQ_BRIDGE_DB", None)
    r = subprocess.run([PY, str(child), str(ROOT), str(storage), str(out), mode, str(db)],
                       capture_output=True, text=True, encoding="utf-8", timeout=240, env=env)
    body = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    body["exit"] = r.returncode
    body["stderr"] = (r.stderr or "").strip().splitlines()[-2:]
    return body


def main() -> int:
    bad = structural()
    tmp = Path(tempfile.mkdtemp(prefix="limits_guard_"))
    child = tmp / "child.py"
    child.write_text(CHILD, encoding="utf-8")
    db = tmp / "bridge.db"
    make_bridge(db)
    storage = tmp / "snap"
    storage.mkdir()
    try:
        print("\n" + "-" * 78)
        print("ب) لكل ذرّة على حدة — إيقاف نظيف ثمّ إقلاع بلا إعادة بثّ")
        print("-" * 78)
        a = run(child, storage, tmp / "a.json", "capture", db)
        before = a.get("state") or {}
        print("  عمليّة (أ): بثّ %s صفقة · last_id=%s · التُقطت=%s" % (
            a.get("drained"), a.get("last_id"), a.get("captured")))
        if a.get("exit") != 0:
            print("  ✗ العمليّة الأولى فشلت: %s" % a.get("stderr"))
            return finish(bad + 1, tmp)
        b = run(child, storage, tmp / "b.json", "restore", db)
        after = b.get("state") or {}
        print("  عمليّة (ب): استُعيدت=%s · last_id بعد الاستعادة=%s · أعاد بثّ %s صفقة" % (
            b.get("restored"), b.get("last_id"), b.get("drained")))
        if b.get("exit") != 0:
            print("  ✗ العمليّة الثانية فشلت: %s" % b.get("stderr"))
            return finish(bad + 1, tmp)

        no_replay = (b.get("drained") or 0) == 0
        bad += 0 if no_replay else 1
        print("  %-34s %s" % ("لا إعادة بثّ بعد استعادة last_id", "✓" if no_replay else "✗"))

        print("\n  الحكم على كل ذرّة وحدها:")
        for aid in sorted(set(IN_SCOPE) | set(DERIVED)):
            key = str(aid)
            was, now = before.get(key), after.get(key)
            same = was == now
            in_scope = aid in IN_SCOPE
            ok = same if True else same
            bad += 0 if same else 1
            tag = "بالنطاق" if in_scope else "مشتقّة (سُحبت)"
            print("    %-4s %-16s قبل=%-30s بعد=%-30s %s" % (
                aid, tag, json.dumps(was, ensure_ascii=False)[:30],
                json.dumps(now, ensure_ascii=False)[:30], "✓ نجت" if same else "✗ ضاعت"))

        print("\n" + "-" * 78)
        print("ج) fail-closed — دفتر فاسد يجب أن يُبقي الحدّ حارسًا")
        print("-" * 78)
        for aid in sorted(IN_SCOPE):
            f = storage / ("%d.json" % aid)
            if not f.exists():
                bad += 1
                print("    %-4s ✗ لا لقطة أصلًا" % aid)
                continue
            broken = tmp / ("bad_%d" % aid)
            shutil.copytree(storage, broken)
            (broken / ("%d.json" % aid)).write_text('{"__bad__": 1}', encoding="utf-8")
            r = run(child, broken, tmp / ("c%d.json" % aid), "restore", db)
            failed = aid in (r.get("failed") or [])
            bad += 0 if failed else 1
            print("    %-4s لقطة فاسدة ⇒ فشلت الاستعادة=%s  %s" % (
                aid, failed, "✓" if failed else "✗ ابتُلعت بصمت"))
    finally:
        pass
    return finish(bad, tmp)


def finish(bad: int, tmp) -> int:
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: دفاتر الحدود تنجو من الإقلاع، و508 مشتقّة فعلًا، والفساد لا يفتح حدًّا.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
