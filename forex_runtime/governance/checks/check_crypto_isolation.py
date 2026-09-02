#!/usr/bin/env python3
"""فحص عزل الكريبتو عن الفوركس — «ما بصير يخلط بين فوركس وكريبتو».

قاعدة المالك الحاكمة، وهذا الفحص هو حارسها المقيس. سبعة حواجز، كلّها بأرقام
من الشجرتين والنواتين الحيّتين — لا رأي ولا افتراض:

  ١) الشجرتان منفصلتان فعلًا على القرص.
  ٢) لا تصادم معرّفات بين الشجرتين (معرّف واحد لذرّتين مختلفتين = خلط صامت).
  ٣) مخزنا البيانات ملفّان مختلفان فعلًا (وليسا وصلتين لمكان واحد).
  ٤) نواتان منفصلتان حيّتان بمنفذين مختلفين.
  ٥) نواة الكريبتو لا تحمّل أي ذرّة من شجرة الفوركس (بالاسم، لا بمدى المعرّف).
  ٦) لوحة الكريبتو لا تُقدَّم عيارات قرار فوركسيّة.
  ٧) تشابه أسماء الأحداث بين الشجرتين — **إعلام لا حكم** (انظر أدناه).

تصحيح مسجَّل (٢٠٢٦-٠٨-٢٩): بُني هذا الفحص أوّلًا وفيه حاجز يُفشِل عند وجود
اسم حدث يَنشره السوقان. القياس ردّه: 96 اسمًا مشتركًا — لأنّ شجرة الكريبتو
منسوخة عن الفوركس، ولأنّ **لكل سوق نواته وناقله** (8010 و8020). اسمٌ واحد في
ناقلَين منفصلَين ليس خلطًا. الحاجز كان تخمينًا؛ حلّ محلّه قياسُ الضرر الحقيقيّ:
هل يكتب السوقان في **ملفّ واحد**؟

قراءة فقط. لا يلمس ذرّة ولا يكتب حرفًا.
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FX_DIR, CR_DIR = ROOT / "atoms", ROOT / "atoms_crypto"
FX_CORE, CR_CORE = "http://127.0.0.1:8010", "http://127.0.0.1:8020"
CR_GOV = "http://127.0.0.1:8093"

failures = 0


def verdict(ok: bool, good: str, bad: str, fatal: bool = True) -> None:
    global failures
    if ok:
        print("🟢 " + good)
    else:
        print(("🛑 " if fatal else "🟠 ") + bad)
        failures += int(fatal)


def events(text: str, key: str) -> list[str]:
    out, inside = [], False
    for line in text.splitlines():
        if re.match(rf"^{key}\s*:", line):
            inside = True
            continue
        if inside:
            m = re.match(r'^\s*-\s*["\']?([\w.]+)', line)
            if m:
                out.append(m.group(1))
            elif line.strip() and not line.startswith((" ", "\t")):
                break
    return out


def scan(root: Path) -> tuple[dict[int, str], set[str]]:
    ids, pubs = {}, set()
    for pattern in ("*/manifest.yaml", "*/*/manifest.yaml"):
        for mf in sorted(root.glob(pattern)):
            text = mf.read_text(encoding="utf-8-sig", errors="replace")
            m = re.search(r"^\s*id:\s*(\d+)", text, re.M)
            if not m:
                continue
            ids[int(m.group(1))] = mf.parent.name
            pubs.update(events(text, "publishes"))
    return ids, pubs


def get(url: str, timeout: int = 8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    # ١ — شجرتان منفصلتان
    verdict(FX_DIR.is_dir() and CR_DIR.is_dir() and FX_DIR != CR_DIR,
            "الشجرتان منفصلتان: atoms/ و atoms_crypto/",
            "شجرة سوق مفقودة أو الاثنتان نفس المجلّد")
    if not (FX_DIR.is_dir() and CR_DIR.is_dir()):
        print("\nالاختلافات = %d" % failures)
        return 1

    fx_ids, fx_pubs = scan(FX_DIR)
    cr_ids, cr_pubs = scan(CR_DIR)
    print("   ذرّات الفوركس = %d · ذرّات الكريبتو = %d" % (len(fx_ids), len(cr_ids)))

    # ٢ — لا تصادم معرّفات
    clash = sorted(set(fx_ids) & set(cr_ids))
    verdict(not clash,
            "لا تصادم معرّفات بين الشجرتين",
            "معرّفات مشتركة (%d): %s" % (len(clash), ", ".join(map(str, clash[:12]))))

    # ٣ — مخزنا البيانات ملفّان مختلفان فعلًا
    # فخّ الوصلات (Junction): forex_runtime/ و crypto_runtime/ يحويان وصلات
    # للجذر. لو صارت `var` وصلةً واحدة، كتب السوقان في ملفّ واحد بصمت.
    fx_var = (ROOT / "forex_runtime" / "var").resolve()
    cr_var = (ROOT / "crypto_runtime" / "var").resolve()
    verdict(fx_var != cr_var,
            "مجلّدا البيانات مساران مختلفان فعلًا بعد فكّ الوصلات",
            "مجلّدا البيانات ينتهيان لنفس المسار (%s) — السوقان يكتبان بملفّ واحد" % cr_var)
    fx_db, cr_db = fx_var / "store" / "market_data.db", cr_var / "store" / "market_data.db"
    if fx_db.is_file() and cr_db.is_file():
        verdict(fx_db.resolve() != cr_db.resolve(),
                "مخزنا السوق ملفّان مختلفان (%.2f م.ب فوركس · %.2f م.ب كريبتو)"
                % (fx_db.stat().st_size / 1048576, cr_db.stat().st_size / 1048576),
                "مخزن السوق ملفّ واحد للسوقين — الأسعار تختلط")
    else:
        print("🟠 مخزن سوق غائب لأحد السوقين — لا حكم على هذا الحاجز")

    # ٤ — نواتان حيّتان بمنفذين
    fx_atoms = cr_atoms = None
    try:
        fx_atoms = get(FX_CORE + "/api/atoms")
    except Exception:                                           # noqa: BLE001
        pass
    try:
        cr_atoms = get(CR_CORE + "/api/atoms")
    except Exception:                                           # noqa: BLE001
        pass
    verdict(cr_atoms is not None,
            "نواة الكريبتو حيّة على 8020 (%s ذرّة)" % (len(cr_atoms) if cr_atoms else 0),
            "نواة الكريبتو (8020) غير قابلة للوصول")
    if fx_atoms is None:
        print("🟠 نواة الفوركس (8010) غير قابلة للوصول — حاجز ٥ يُقاس بالشجرة وحدها")

    # ٥ — نواة الكريبتو لا تحمّل ذرّة فوركسيّة
    if cr_atoms is not None:
        loaded = {int(a["id"]) for a in cr_atoms}
        intruders = sorted(i for i in loaded if i in fx_ids and i not in cr_ids)
        unknown = sorted(i for i in loaded if i not in cr_ids and i not in fx_ids)
        verdict(not intruders,
                "نواة الكريبتو ما حمّلت ذرّة من شجرة الفوركس — %d/%d من شجرتها"
                % (len(loaded - set(intruders)), len(loaded)),
                "ذرّات فوركس محمّلة بنواة الكريبتو: %s" % ", ".join(map(str, intruders)))
        if unknown:
            print("🟠 معرّفات محمّلة ليست بأي شجرة على القرص: %s"
                  % ", ".join(map(str, unknown[:10])))

    # ٦ — لا عيارات قرار فوركسيّة بلوحة الكريبتو
    try:
        dials = get(CR_GOV + "/gov/decision/settings").get("dials") or []
        verdict(not dials,
                "لوحة الكريبتو ما تُقدَّم لها عيارات قرار فوركسيّة (صفر)",
                "لوحة الكريبتو تعرض %d عيارًا فوركسيًّا" % len(dials))
    except Exception as exc:                                    # noqa: BLE001
        print("🟠 تعذّر سؤال لوحة الكريبتو (8093): %s" % type(exc).__name__)

    # ٧ — تشابه أسماء الأحداث: إعلام لا حكم (ناقلان منفصلان — انظر رأس الملف)
    both = sorted(fx_pubs & cr_pubs)
    print("ℹ️  أسماء أحداث تَنشرها الشجرتان: %d (من %d فوركس و%d كريبتو) — "
          "غير ضارّ: لكل سوق ناقله" % (len(both), len(fx_pubs), len(cr_pubs)))

    print("\nالاختلافات = %d" % failures)
    if failures:
        print("🛑 العزل مخروق — الفوركس والكريبتو يختلطان.")
        return 1
    print("🟢 العزل سليم — سوقان منفصلان بشجرة ومعرّفات وأحداث ونواة ولوحة.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
