# -*- coding: utf-8 -*-
"""
مولّد بطاقات الشرح — إعادة توليد «البطاقة المقيسة» من manifest.yaml الحالي
=========================================================================
المشروع: QUANT_NQ
الغرض:   البطاقة داخل كل `الشرح.md` (بين وسمي BITAQA_START/BITAQA_END) تقول عن
         نفسها: «مقروءة من manifest.yaml و atom.py — لا تُكتب باليد». بعد تحويل
         الأقسام للتكة تغيّرت المانيفستات ولم تُعدَّل البطاقات، فصار التوثيق يخالف
         المعلَن. هذه الأداة تعيد توليد البطاقات من المانيفستات الحالية فقط.

ما تفعله:
  * تستبدل ما بين وسمي البطاقة (بما فيهما) ببطاقة جديدة مولّدة من manifest.yaml.
  * لا تلمس أي شيء خارج الوسمين — السرد المكتوب بخط اليد يبقى حرفيًّا كما هو.
  * تحافظ على أسطر «لا شيء — <سبب>» التفسيرية إن وُجدت في البطاقة القديمة
    (تُستخرج قبل الاستبدال وتُعاد كما هي — لا يُخترع نص).
  * تُدرج بطاقة جديدة (بعد سطر العنوان) للذرّات التي ليس لها بطاقة أصلًا.
  * (اختياري ‎--history) تُلحق سطرًا توثيقيًّا صادقًا بملف التاريخ.md للذرّات
    التي لا تذكر نسختها الحالية — يسجّل فعل تحديث التوثيق نفسه، لا تاريخًا مفبركًا.

ما لا تفعله:
  * لا تلمس atom.py ولا manifest.yaml ولا أي كود إطلاقًا.
  * لا تلمس النص اليدوي خارج البطاقة (حتى لو ذكر أحداثًا قديمة — قرار المالك).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import re
import sys

import yaml

TODAY_ASCII = "2026-08-23"
AR_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def ar_num(value: object) -> str:
    return str(value).translate(AR_DIGITS)


def build_card(m: dict, folder: str) -> str:
    interval_ms = int((m.get("health") or {}).get("interval_ms", 0))
    if interval_ms and interval_ms % 1000 == 0:
        health_txt = f"كل `{ar_num(interval_ms // 1000)}` ثانية"
    elif interval_ms:
        health_txt = f"كل `{ar_num(interval_ms)}`ms"
    else:
        health_txt = "غير معرّف"

    lines = [
        "<!-- BITAQA_START -->",
        "",
        f"> **بطاقة مقيسة — مقروءة من `manifest.yaml` و `atom.py` يوم {ar_num(TODAY_ASCII[:4])}-{ar_num(TODAY_ASCII[5:7])}-{ar_num(TODAY_ASCII[8:])}. لا تُكتب باليد.**",
        "",
        "| | |",
        "|---|---|",
        f"| الرقم | `{ar_num(m['id'])}` |",
        f"| الاسم | {m.get('name', '')} |",
        f"| المجلّد | `{folder}` |",
        f"| الإصدار | `{m.get('version', '')}` |",
        f"| حرجة؟ | `{str(m.get('critical', False)).lower()}` |",
        f"| الإقلاع | `{m.get('startup_mode', '')}` |",
        f"| فحص الصحّة | {health_txt} |",
        "",
    ]
    lines.append("**تسمع (من `manifest.yaml`):**")
    subs = list(m.get("subscribes") or [])
    if subs:
        lines += [f"- `{e}`" for e in subs]
    else:
        note = m.get("_empty_sub_note") or "لا شيء"
        lines.append(f"- {note}")
    lines.append("")
    lines.append("**تُرسِل (من `manifest.yaml`):**")
    pubs = list(m.get("publishes") or [])
    if pubs:
        lines += [f"- `{e}`" for e in pubs]
    else:
        note = m.get("_empty_pub_note") or "لا شيء"
        lines.append(f"- {note}")
    lines.append("")
    keys = list(((m.get("config_schema") or {}).get("properties") or {}).keys())
    if keys:
        lines.append("**إعداداتها:** " + " · ".join(f"`{k}`" for k in keys))
        lines.append("")
    lines.append("<!-- BITAQA_END -->")
    return "\n".join(lines)


CARD_RE = re.compile(r"<!-- BITAQA_START -->.*?<!-- BITAQA_END -->", re.DOTALL)
EMPTY_LINE_RE = re.compile(r"^\- (لا شيء.*)$")


def extract_empty_notes(text: str) -> dict[str, str]:
    """استخراج أسطر «لا شيء — السبب» من البطاقة القديمة حتى لا يضيع نصّ تفسيري."""
    notes: dict[str, str] = {}
    block = CARD_RE.search(text)
    if not block:
        return notes
    section = None
    for line in block.group(0).splitlines():
        if line.startswith("**تسمع"):
            section = "sub"; continue
        if line.startswith("**تُرسِل"):
            section = "pub"; continue
        if section:
            mm = EMPTY_LINE_RE.match(line.strip())
            if mm:
                notes[section] = mm.group(1)
    return notes


def regen(atoms_root: pathlib.Path, *, with_history: bool) -> dict[str, int]:
    stats = {"cards_replaced": 0, "cards_inserted": 0,
             "history_appended": 0, "skipped_no_manifest": 0}
    for atom_dir in sorted(atoms_root.iterdir()):
        mf = atom_dir / "manifest.yaml"
        sharh = atom_dir / "الشرح.md"
        if not mf.exists() or not sharh.exists():
            stats["skipped_no_manifest"] += 1
            continue
        m = yaml.safe_load(mf.read_text(encoding="utf-8"))
        text = sharh.read_text(encoding="utf-8")

        notes = extract_empty_notes(text)
        if not (m.get("subscribes") or []):
            m["_empty_sub_note"] = notes.get("sub")
        if not (m.get("publishes") or []):
            m["_empty_pub_note"] = notes.get("pub")

        card = build_card(m, atom_dir.name) + "\n"
        if CARD_RE.search(text):
            new_text = CARD_RE.sub(lambda _: card, text, count=1)
            stats["cards_replaced"] += 1
        else:
            head, sep, tail = text.partition("\n")
            new_text = (head + "\n\n" + card + ("\n" if not tail.startswith("\n") else "") + tail.lstrip("\n")).rstrip("\n") + "\n"
            stats["cards_inserted"] += 1
        if new_text != text:
            sharh.write_text(new_text, encoding="utf-8")

        if with_history:
            tarikh = atom_dir / "التاريخ.md"
            version = str(m.get("version", ""))
            if tarikh.exists() and version and version not in tarikh.read_text(encoding="utf-8"):
                entry = (f"\n## {version} — {TODAY_ASCII}\n"
                         f"- تحديث توثيقي فقط: أُعيد توليد بطاقة الشرح من `manifest.yaml` "
                         f"الحالي (أحداث النشر والاشتراك والإصدار). لا تغيير في الكود ولا في السلوك.\n")
                with tarikh.open("a", encoding="utf-8") as fh:
                    fh.write(entry)
                stats["history_appended"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="إعادة توليد بطاقات الشرح من المانيفستات الحالية")
    parser.add_argument("--history", action="store_true",
                        help="إلحاق سطر توثيقي في التاريخ.md للذرّات التي لا تذكر نسختها الحالية")
    parser.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parents[1] / "atoms"))
    args = parser.parse_args(argv)
    stats = regen(pathlib.Path(args.root), with_history=args.history)
    print(f"بطاقات استُبدلت: {stats['cards_replaced']}")
    print(f"بطاقات أُدرجت (لم تكن موجودة): {stats['cards_inserted']}")
    print(f"أسطر تاريخ أُلحقت: {stats['history_appended']}")
    print(f"متجاوزة (بلا مانيفست/شرح): {stats['skipped_no_manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
