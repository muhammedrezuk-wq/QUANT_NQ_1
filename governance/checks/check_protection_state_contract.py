"""Contract guard for problem 5-3 (option a) — the owner's protection state
must survive a restart.

Owner's ruling 2026-08-14, verbatim:

    "552._halted · 550._halted · 519._paused · 519._states, including the
     manual freeze/release state that is NOT rebuilt from the event stream."
    "Do not let a failed or absent snapshot be read as False / UNPAUSED /
     RELEASED. In this layer the UNKNOWN is not safe; the asset must stay
     protected."
    "fail-closed applies to a FAILED restore, not to changing the meaning of a
     state that restored successfully: a restored MANUAL_RELEASE must come back
     as MANUAL_RELEASE, not be turned into PAUSED because that looks safer."
    "Do not go near 581 or 578 at this stage."

Why these three and not the four first proposed: the live restart measured on
2026-08-14 healed 584 and 571 from the event stream alone (618 re-announced the
specs, 519 rebuilt from the ledger), so they carry no state worth saving. What
NO event can rebuild is the owner's own intent -- a halt he ordered, an asset he
paused, an asset he released by hand.

  A) STRUCTURAL -- the three atoms carry the contract; 581 and 578 are untouched.
  B) الحالات الأربع -- HALTED · PAUSED · MANUAL_RELEASE · the plain not-halted
     default are each captured and each restored to the SAME meaning.
  C) موت العمليّة -- a second, separate process restores from the files the
     first wrote; the value compared is the semantic state, not json success.
  D) fail-closed -- a restore that fails must leave the asset PROTECTED, never
     open.

Exit 1 on any divergence.
"""
from __future__ import annotations

import json
import shutil
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
A519 = "519_محفظة_الأصل"
A550 = "550_مدير_التنفيذ"
A552 = "552_مدقق_الأمر"
# not to be approached at this stage, by his order.  578 later moved to 2.7.0
# for problem 58 (measurement only) and 2.8.0 for problem 63 (the request id
# carries the snapshot and the official time).  Its send and retry lines stay
# frozen line by line by check_delta_visibility_contract.
# م-43 (ورقة ٤١، 2026-08-28): أُعيد التثبيت على إصدارات ما بعد الدمج الموثّق (RC4/RC5)
UNTOUCHED = {"581_محرك_فرق_المركز": "3.4.1", "578_منفذ_التحوط": "5.4.0"}

CHILD = r'''
import asyncio, json, sys
from pathlib import Path
ROOT = Path(sys.argv[1]); STORAGE = Path(sys.argv[2]); OUT = Path(sys.argv[3]); MODE = sys.argv[4]
sys.path.insert(0, str(ROOT))
from core.bootloader import Bootloader
from core.contracts.atom import AtomContext
from core.manifest_loader import scan
from core.registry import Registry
from core.snapshot_engine import SnapshotEngine

PROBE = (519, 550, 552)
ACC = "52992818"


class L:
    def __getattr__(self, n): return lambda *a, **k: None


async def _pub(name, payload): pass
def _sub(name, handler): pass


def out(**kw):
    body = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    body.update(kw); OUT.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


async def build():
    reg = Registry(); found = {a.manifest.id: a for a in scan(ATOM_ROOT).atoms}
    made = {}
    for i in PROBE:
        d = found[i]; inst = Bootloader.instantiate(d); reg.register(d.manifest, inst)
        await inst.initialize(AtomContext(atom_id=i, config=dict(d.manifest.config),
                                          logger=L(), publish=_pub, subscribe=_sub))
        await inst.start(); made[i] = inst
    return reg, made


def semantics(made):
    g, m, p = made[552], made[550], made[519]
    return {"552_halted": bool(g._halted), "550_halted": bool(m._halted),
            "519_halted": bool(p._halted), "519_paused": sorted(p._paused),
            "519_states": {k: v for k, v in sorted(p._states.items())}}


async def main():
    reg, made = await build()
    engine = SnapshotEngine(reg, STORAGE)

    if MODE == "capture":
        await made[552]._on_halt({"reason": "OWNER"})
        await made[550]._on_halt({"reason": "OWNER"})
        await made[519]._on_command({"account_id": ACC, "symbol": "PAUSED_ONE", "command": "pause"})
        await made[519]._on_command({"account_id": ACC, "symbol": "RELEASED_ONE", "command": "release"})
        await made[519]._on_command({"account_id": ACC, "symbol": "NORMAL_ONE", "command": "resume"})
        out(state=semantics(made))
        rep = await engine.snapshot_all()
        out(captured=rep.captured, skipped=rep.skipped, failed=rep.failed,
            files=sorted(q.name for q in STORAGE.glob("*.json")))
        return 0

    out(fresh=semantics(made))
    rep = await engine.restore_all()
    out(restored=rep.restored, skipped=rep.skipped, failed=rep.failed, state=semantics(made))
    return 0


sys.exit(asyncio.run(main()))
'''


def manifest(folder: str) -> dict:
    return yaml.safe_load((ATOMS / folder / "manifest.yaml").read_text(encoding="utf-8"))


def structural() -> int:
    print("=" * 78)
    print("أ) الحواجز البنيويّة — العقد الثلاثيّ موجود، و581/578 لم تُمَسّا")
    print("=" * 78)
    bad = 0
    for folder, label in ((A552, "552"), (A550, "550"), (A519, "519")):
        src = (ATOMS / folder / "atom.py").read_text(encoding="utf-8")
        # م-43: الذرّات المدموجة متعدّدة الملفات — snapshot/restore قد تسكن
        # وحدة شقيقة؛ يُفحص كود الذرّة كاملًا لا atom.py وحده.
        src_all = "\n".join(f.read_text(encoding="utf-8") for f in (ATOMS / folder).glob("*.py"))
        has_snap = "async def snapshot" in src_all
        has_rest = "async def restore" in src_all
        fail_closed = "FAIL_CLOSED" in src_all
        for name, ok in ((f"{label} يحفظ", has_snap), (f"{label} يستعيد", has_rest),
                         (f"{label} مغلق عند فشل الاستعادة", fail_closed)):
            bad += 0 if ok else 1
            print("  %-38s %s" % (name, "✓" if ok else "✗"))
    print("  المستثنى من اللمس بأمره:")
    for folder, version in UNTOUCHED.items():
        got = str(manifest(folder).get("version"))
        ok = got == version
        bad += 0 if ok else 1
        print("      %-30s %-8s %s" % (folder.split("_")[0], got, "✓" if ok else "✗ تغيّرت!"))
    return bad


def run(child: Path, storage: Path, out: Path, mode: str) -> dict:
    out.write_text("{}", encoding="utf-8")
    r = subprocess.run([PY, str(child), str(ROOT), str(storage), str(out), mode],
                       capture_output=True, text=True, encoding="utf-8", timeout=180)
    body = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    body["exit"] = r.returncode
    body["stderr"] = (r.stderr or "").strip().splitlines()[-1:]
    return body


def main() -> int:
    bad = structural()
    tmp = Path(tempfile.mkdtemp(prefix="protect_guard_"))
    child = tmp / "child.py"
    child.write_text(CHILD, encoding="utf-8")
    try:
        print("\n" + "-" * 78)
        print("ب+ج) الحالات الأربع عبر موت عمليّة واستعادة بعمليّة أخرى")
        print("-" * 78)
        storage = tmp / "snap"
        storage.mkdir()
        cap = run(child, storage, tmp / "a.json", "capture")
        want = cap.get("state")
        print("  عمليّة (أ) — الحالة المصنوعة: %s" % json.dumps(want, ensure_ascii=False))
        print("  التُقطت=%s · تُخطّيت=%s · فشلت=%s · ملفّات=%s" % (
            cap.get("captured"), cap.get("skipped"), cap.get("failed"), cap.get("files")))
        for label, key, expect in (("HALTED (552)", "552_halted", True),
                                   ("HALTED (550)", "550_halted", True),
                                   ("PAUSED", "519_paused", None),
                                   ("MANUAL_RELEASE", "519_states", None),
                                   ("الافتراضيّ غير الموقوف", "519_states", None)):
            pass
        for i in (519, 550, 552):
            ok = i in (cap.get("captured") or [])
            bad += 0 if ok else 1
            print("  %-38s %s" % ("الذرّة %d التُقطت" % i, "✓" if ok else "✗ تُخطّيت (لا تدعم اللقطة)"))
        if not (cap.get("files")):
            print("  ⇒ لا ملفّات ⇒ لا استعادة ممكنة")
            return finish(bad, tmp)

        res = run(child, storage, tmp / "b.json", "restore")
        fresh, got = res.get("fresh"), res.get("state")
        print("\n  عمليّة (ب) — قبل الاستعادة (يجب أن تكون نظيفة): %s"
              % json.dumps(fresh, ensure_ascii=False))
        clean = fresh != want
        bad += 0 if clean else 1
        print("  %-38s %s" % ("العمليّة الجديدة بدأت نظيفة", "✓" if clean else "✗"))
        print("  استُعيدت=%s · فشلت=%s" % (res.get("restored"), res.get("failed")))
        print("  بعد الاستعادة: %s" % json.dumps(got, ensure_ascii=False))

        checks = (
            ("HALTED (552) عاد كما هو", (got or {}).get("552_halted") is True),
            ("HALTED (550) عاد كما هو", (got or {}).get("550_halted") is True),
            ("PAUSED عاد بنفس الأصل",
             any("PAUSED_ONE" in k for k in ((got or {}).get("519_paused") or []))),
            ("MANUAL_RELEASE عاد MANUAL_RELEASE لا PAUSED",
             any(v == "MANUAL_RELEASE" for k, v in ((got or {}).get("519_states") or {}).items()
                 if "RELEASED_ONE" in k)),
            ("الافتراضيّ غير الموقوف بقي NORMAL",
             any(v == "NORMAL" for k, v in ((got or {}).get("519_states") or {}).items()
                 if "NORMAL_ONE" in k)),
            ("القيمة الدلاليّة كاملةً متطابقة", got == want),
        )
        for name, ok in checks:
            bad += 0 if ok else 1
            print("  %-38s %s" % (name, "✓" if ok else "✗"))

        print("\n" + "-" * 78)
        print("د) fail-closed — استعادة فاسدة يجب أن تُبقي الأصل محميًّا لا مفتوحًا")
        print("-" * 78)
        for atom_id in (519, 550, 552):
            broken = tmp / ("snap_bad_%d" % atom_id)
            shutil.copytree(storage, broken)
            (broken / ("%d.json" % atom_id)).write_text('{"__bad__": [1, 2, 3]}', encoding="utf-8")
            r = run(child, broken, tmp / ("c%d.json" % atom_id), "restore")
            st = r.get("state") or {}
            if atom_id == 552:
                safe = st.get("552_halted") is True
            elif atom_id == 550:
                safe = st.get("550_halted") is True
            else:
                # 519 يُغلق بـ_halted: `next_state` تُرجع FROZEN/FREEZE لكلّ أصل
                safe = st.get("519_halted") is True
            bad += 0 if safe else 1
            print("  %-38s %s  (فشلت=%s)" % (
                "لقطة %d فاسدة ⇒ يبقى محميًّا" % atom_id, "✓" if safe else "✗ انفتح!",
                r.get("failed")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return finish(bad, None)


def finish(bad: int, tmp) -> int:
    if tmp is not None:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 78)
    print("الاختلافات = %d" % bad)
    if bad == 0:
        print("سليم: إيقاف المالك وتجميده وتحريره تنجو من موت العمليّة، والفساد يُبقيها مغلقة.")
    return 1 if bad else 0



# ⏳ م-47 (ورقة ٤١، 2026-08-28): ما تبقّى من فروق هذا الفحص انحرافُ عقودٍ بعد
# الدمج (لاخطّاط لقطة الذرّات المدموجة/سلوك إشارات ويندوز) — يُرحَّل بنافذة
# خاصة ويبقى أحمرَ صادقًا. إصلاحات م-43 الميكانيكية (تثبيتات UNTOUCHED،
# فحص snapshot بكل ملفات الذرّة) مثبّة أعلاه وتعمل.

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
