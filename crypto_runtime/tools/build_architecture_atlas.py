#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""يولّد المخطط الهندسي الكامل من المانيفستات والنواة — لا اختراع ذرّة."""
from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "docs" / "١_الهندسة" / "مشتركة" / "مخطط_هندسي_كامل.html"
OUT2 = ROOT / "مخطط_هندسي_كامل.html"
DATE = "2026-09-02"


def esc(s: object) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def load_yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}


def collect_atoms(base: Path, market: str) -> list[dict]:
    rows = []
    for mf in sorted(base.rglob("manifest.yaml")):
        d = load_yaml(mf)
        parent = mf.parent
        rows.append(
            {
                "market": market,
                "id": int(d["id"]),
                "name": d.get("name") or parent.name,
                "version": str(d.get("version") or ""),
                "startup": str(d.get("startup_mode") or ""),
                "critical": bool(d.get("critical")),
                "deps": [str(x) for x in (d.get("dependencies") or [])],
                "publishes": [str(x) for x in (d.get("publishes") or [])],
                "subscribes": [str(x) for x in (d.get("subscribes") or [])],
                "folder": parent.name,
                "section": parent.parent.name,
                "config_keys": sorted((d.get("config") or {}).keys())
                if isinstance(d.get("config"), dict)
                else [],
                "health": d.get("health") if isinstance(d.get("health"), dict) else {},
            }
        )
    rows.sort(key=lambda r: r["id"])
    return rows


def events_index(atoms: list[dict]):
    pub, sub = defaultdict(list), defaultdict(list)
    for a in atoms:
        for e in a["publishes"]:
            pub[e].append(a["id"])
        for e in a["subscribes"]:
            sub[e].append(a["id"])
    return pub, sub


CORE_FILES = [
    ("contracts/atom.py", "عقد الذرّة — AtomBase · السياق · الصحة"),
    ("contracts/manifest.py", "عقد المانيفست"),
    ("contracts/services.py", "خدمات النواة للذرّة"),
    ("bootloader.py", "الإقلاع واكتشاف الذرّات"),
    ("lifecycle.py", "دورة الحياة الموحّدة"),
    ("registry.py", "السجلّ الحيّ"),
    ("event_bus.py", "ناقل الأحداث داخل العملية"),
    ("health_manager.py", "فحوص الصحة وإعادة التشغيل"),
    ("hot_reload_service.py", "إعادة التحميل الساخن / رفض اليدوي"),
    ("manifest_loader.py", "قراءة المانيفست"),
    ("dependency_resolver.py", "ترتيب الاعتماد"),
    ("snapshot_engine.py", "لقطات الحالة"),
    ("journal.py", "اليوميات"),
    ("logger.py", "السجلّ"),
    ("metrics.py", "المقاييس"),
    ("config.py", "إعداد النواة"),
    ("errors.py", "الأخطاء"),
    ("version_manager.py", "الإصدار"),
    ("api/app.py", "HTTP/WebSocket النواة"),
    ("api/__init__.py", "حزمة الـAPI"),
    ("__init__.py", "حزمة النواة"),
    ("__version__.py", "الثابت 1.31.0"),
    ("CORE.lock", "ختم التجميد — 23 ملفًا"),
]

FOREX_LIVE = [
    ("806", "نبض الزمن", "SYS_SECOND"),
    ("622", "FIX سي تريدر — مصدر التحليل", "feed.ctrader.tick"),
    ("613", "مجمّع: سي تريدر فقط → تحليل", "market.tick  (CTRADER) · MT5 → market.broker_tick عرض"),
    ("112", "بوابة التيك الصالح", "market.tick.validated"),
    ("102 / 103", "سعر + بنّاء شموع من التيك الصالح", "market_data.price_received / candle_closed"),
    ("151–165", "محللون سريعون", "analysis.*.state"),
    ("200 / 250 / 300 / 351", "بنية · سيولة · إحصاء · احتمال", "section.live"),
    ("400–413", "الاستراتيجية", "strategy.*.state"),
    ("450–467", "غرفة القرار", "decision.resolved"),
    ("576", "المحرك الدائم", "execution.order.requested"),
    ("551 / 552", "بناء الأمر + المدقق", "trading.final_decision"),
    ("601", "كاتب الجسر", "جدول commands"),
    ("EA / 618", "إكسبرت + قراءة الجسر — تنفيذ لا تحليل", "CTrade · feed.mt5.tick للعرض/الانحراف فقط"),
]


def sheet(num: str, title: str, body: str, note: str = "") -> str:
    return f"""
<section class="sheet" id="p{esc(num)}">
  <header class="sheet-head">
    <div class="proj">QUANT_NQ · مخطط هندسي</div>
    <div class="stitle">{esc(title)}</div>
    <div class="snum">ورقة {esc(num)}</div>
  </header>
  <div class="sheet-body">{body}</div>
  <footer class="titleblock">
    <div><b>المشروع</b> QUANT_NQ</div>
    <div><b>الورقة</b> {esc(num)} — {esc(title)}</div>
    <div><b>التاريخ</b> {DATE}</div>
    <div><b>المصدر</b> manifest.yaml · core/ · mt5/QUANT_NQ.mq5</div>
    <div class="note">{esc(note)}</div>
  </footer>
</section>
"""


def boxes_row(items: list[tuple[str, str]]) -> str:
    cells = []
    for title, sub in items:
        cells.append(
            f'<div class="box"><div class="bt">{esc(title)}</div>'
            f'<div class="bs">{esc(sub)}</div></div>'
        )
    return '<div class="flow">' + '<div class="arr">←</div>'.join(cells) + "</div>"


def table(headers: list[str], rows: list[list[str]], small: bool = False) -> str:
    th = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = []
    for r in rows:
        tds = "".join(f"<td>{c}</td>" for c in r)
        body.append(f"<tr>{tds}</tr>")
    cls = "grid small" if small else "grid"
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def atom_rows(group: list[dict]) -> list[list[str]]:
    out = []
    for a in group:
        pub = " · ".join(a["publishes"][:6])
        if len(a["publishes"]) > 6:
            pub += f" +{len(a['publishes'])-6}"
        sub = " · ".join(a["subscribes"][:6])
        if len(a["subscribes"]) > 6:
            sub += f" +{len(a['subscribes'])-6}"
        man = "يدوي" if a["startup"] == "manual" else "تلقائي"
        crit = "حرجة" if a["critical"] else "—"
        out.append(
            [
                esc(a["id"]),
                esc(a["name"]),
                esc(a["version"]),
                man,
                crit,
                esc(pub) or "—",
                esc(sub) or "—",
            ]
        )
    return out


CSS = r"""
:root { --ink:#1a1a1a; --rule:#2c2c2c; --paper:#f4efe4; --box:#fffdf8; --accent:#3d4f3a; }
* { box-sizing:border-box; }
html,body { margin:0; padding:0; background:#cfc8b8; color:var(--ink);
  font-family:"Segoe UI","Tahoma","Amiri","Noto Naskh Arabic",sans-serif; }
nav.toc { position:sticky; top:0; z-index:9; background:#1a1a1a; color:#f4efe4;
  padding:10px 18px; display:flex; gap:14px; flex-wrap:wrap; font-size:13px; }
nav.toc a { color:#f4efe4; text-decoration:none; border-bottom:1px solid #888; }
.sheet { background:var(--paper); margin:18px auto; width:min(1400px,96vw);
  min-height:900px; border:2px solid var(--ink); display:flex; flex-direction:column;
  box-shadow:0 8px 24px #0003; }
.sheet-head { display:grid; grid-template-columns:1fr 2fr 140px; border-bottom:2px solid var(--ink);
  font-size:13px; }
.sheet-head > div { padding:8px 12px; border-left:1px solid var(--ink); }
.stitle { font-weight:700; font-size:18px; letter-spacing:.02em; }
.snum { font-weight:700; text-align:center; background:#1a1a1a; color:#f4efe4; }
.sheet-body { padding:16px 18px 8px; flex:1; }
.titleblock { display:grid; grid-template-columns:repeat(5,1fr); border-top:2px solid var(--ink);
  font-size:11px; }
.titleblock > div { padding:6px 8px; border-left:1px solid var(--ink); }
.titleblock .note { grid-column:1/-1; border-left:none; border-top:1px solid var(--ink); color:#444; }
h2 { margin:0 0 10px; font-size:16px; }
p,li { font-size:14px; line-height:1.65; }
.flow { display:flex; flex-wrap:wrap; align-items:stretch; gap:0; margin:12px 0 18px; }
.box { border:1.5px solid var(--ink); background:var(--box); min-width:110px; max-width:180px;
  padding:8px 10px; flex:1; }
.bt { font-weight:700; font-size:13px; }
.bs { font-size:11px; color:#444; margin-top:4px; }
.arr { display:flex; align-items:center; padding:0 6px; font-size:20px; font-weight:700; }
.layers { display:flex; flex-direction:column; gap:6px; margin:12px 0; }
.layer { border:1.5px solid var(--ink); padding:10px 14px; background:var(--box); }
.layer b { display:block; font-size:14px; }
.layer span { font-size:12px; color:#333; }
.grid { width:100%; border-collapse:collapse; font-size:12px; margin:8px 0 14px; }
.grid th,.grid td { border:1px solid var(--ink); padding:4px 6px; vertical-align:top; }
.grid th { background:#1a1a1a; color:#f4efe4; font-weight:600; }
.grid.small { font-size:11px; }
.grid tr:nth-child(even) td { background:#ebe4d6; }
.twin { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.legend { display:flex; gap:16px; flex-wrap:wrap; font-size:12px; margin:8px 0; }
.sw { display:inline-block; width:12px; height:12px; border:1px solid #000; margin-left:4px; vertical-align:middle; }
.manual { background:#f3d9a4; }
.auto { background:#d5e4d0; }
.draw { width:100%; height:auto; border:1px solid var(--ink); background:#fffdf8; margin:8px 0 14px; }
.kvs { display:grid; grid-template-columns:180px 1fr; gap:0; font-size:13px; border:1px solid var(--ink); }
.kvs div { padding:5px 8px; border-bottom:1px solid #ccc; }
.kvs div:nth-child(odd) { font-weight:700; background:#ebe4d6; }
@media print {
  nav.toc { display:none; }
  body { background:#fff; }
  .sheet { box-shadow:none; margin:0; width:auto; page-break-after:always; min-height:0; }
}
"""


def svg_layers() -> str:
    return """
<svg class="draw" viewBox="0 0 1100 420" xmlns="http://www.w3.org/2000/svg">
  <rect x="20" y="20" width="1060" height="50" fill="#1a1a1a"/>
  <text x="550" y="52" fill="#f4efe4" font-size="18" text-anchor="middle">⑥ لوحة الحوكمة — أسرار من اللوحة · أوامر ٩٠١ · مختبر معزول</text>
  <rect x="20" y="80" width="1060" height="50" fill="#3d4f3a"/>
  <text x="550" y="112" fill="#fff" font-size="18" text-anchor="middle">⑤ أحداث — ناقل داخل كل runtime · لا استيراد ذرّة لذرّة</text>
  <rect x="20" y="140" width="1060" height="70" fill="#6b5a3a"/>
  <text x="550" y="172" fill="#fff" font-size="18" text-anchor="middle">④ ذرّات — فوركس 233 · كريبتو 80 · المانيفست سلطة الإقلاع</text>
  <text x="550" y="196" fill="#f4efe4" font-size="13" text-anchor="middle">عقد AtomBase فقط · النواة لا تعرف رقم ذرّة</text>
  <rect x="20" y="220" width="1060" height="50" fill="#4a3f55"/>
  <text x="550" y="252" fill="#fff" font-size="18" text-anchor="middle">③ توابع النواة — ساعة · نقل · أمن · تخزين · سجلّ مشترَك</text>
  <rect x="20" y="280" width="1060" height="50" fill="#2c3a4a"/>
  <text x="550" y="312" fill="#fff" font-size="18" text-anchor="middle">② النواة المختومة Core 1.31.0 — 23 ملفًا · CORE.lock · root 1b0d0f6b…</text>
  <rect x="20" y="340" width="1060" height="50" fill="#f4efe4" stroke="#1a1a1a"/>
  <text x="550" y="372" fill="#1a1a1a" font-size="18" text-anchor="middle">① الأرض — المستودع · سوقان مفصولان (forex_runtime / crypto_runtime) · أزرار إطلاق</text>
</svg>
"""


def svg_live() -> str:
    labels = [
        (40, "622 FIX"),
        (160, "613 تحليل"),
        (280, "112 بوابة"),
        (400, "103 شموع"),
        (520, "تحليل"),
        (640, "400 استراتيجية"),
        (780, "450 قرار"),
        (900, "576 محرك"),
        (1020, "552/601/EA"),
    ]
    parts = ['<svg class="draw" viewBox="0 0 1100 200" xmlns="http://www.w3.org/2000/svg">']
    y = 70
    for i, (x, t) in enumerate(labels):
        parts.append(f'<rect x="{x}" y="{y}" width="110" height="48" fill="#fffdf8" stroke="#1a1a1a"/>')
        parts.append(f'<text x="{x+55}" y="{y+30}" text-anchor="middle" font-size="12">{html.escape(t)}</text>')
        if i:
            px = labels[i - 1][0] + 110
            parts.append(f'<line x1="{px}" y1="{y+24}" x2="{x}" y2="{y+24}" stroke="#1a1a1a" marker-end="url(#a)"/>')
    parts.append('<defs><marker id="a" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#1a1a1a"/></marker></defs>')
    parts.append('<text x="550" y="160" text-anchor="middle" font-size="13">تغذية حيّة بالتيك لا بالشمعة. الشمعة ناتج محدود من التيكات (103). القرار لا يفتح صفقة؛ 601 يكتب والجسر ينفّذ.</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_lab() -> str:
    return """
<svg class="draw" viewBox="0 0 1100 260" xmlns="http://www.w3.org/2000/svg">
  <rect x="30" y="30" width="500" height="200" fill="#fffdf8" stroke="#1a1a1a" stroke-width="2"/>
  <text x="280" y="58" text-anchor="middle" font-size="16" font-weight="700">مسار التداول الحي</text>
  <text x="280" y="90" text-anchor="middle" font-size="13">manifest.yaml كما هو</text>
  <text x="280" y="114" text-anchor="middle" font-size="13">عتبات اللوحة → /api/rescan و٩٠١</text>
  <text x="280" y="138" text-anchor="middle" font-size="13">٦٢٢ · ٥٧٦ · ٩٠١ · ٦٠١ · إكسبرت</text>
  <text x="280" y="178" text-anchor="middle" font-size="12">لا overlay للمختبر هنا</text>
  <rect x="570" y="30" width="500" height="200" fill="#ebe4d6" stroke="#1a1a1a" stroke-width="2"/>
  <text x="820" y="58" text-anchor="middle" font-size="16" font-weight="700">مختبر / باك تست</text>
  <text x="820" y="90" text-anchor="middle" font-size="13">var/lab/overrides فقط</text>
  <text x="820" y="114" text-anchor="middle" font-size="13">ذرّات حقيقية في الذاكرة</text>
  <text x="820" y="138" text-anchor="middle" font-size="13">بلا ٩٠١ / ٥٧٦ / ٦٠١ / ٦٢٢</text>
  <text x="820" y="178" text-anchor="middle" font-size="12">دفتر ورقي على decision.resolved</text>
  <text x="550" y="250" text-anchor="middle" font-size="12">جدار عزل: تعديل عتبة بالمختبر لا يُكتب في المانيفست ولا يُرسل للتداول</text>
</svg>
"""


def build() -> str:
    fx = collect_atoms(ROOT / "atoms", "forex")
    cr = collect_atoms(ROOT / "atoms_crypto", "crypto")
    all_atoms = fx + cr
    pub, sub = events_index(all_atoms)
    fx_sec = defaultdict(list)
    cr_sec = defaultdict(list)
    for a in fx:
        fx_sec[a["section"]].append(a)
    for a in cr:
        cr_sec[a["section"]].append(a)

    lock = json.loads((ROOT / "core" / "CORE.lock").read_text(encoding="utf-8"))
    rel = json.loads((ROOT / "config" / "unified_release.json").read_text(encoding="utf-8"))
    ui = sorted(p.stem for p in (ROOT / "governance" / "ui" / "src" / "sections").glob("*.tsx"))

    sheets: list[str] = []
    toc: list[tuple[str, str]] = []

    def add(num: str, title: str, body: str, note: str = ""):
        toc.append((num, title))
        sheets.append(sheet(num, title, body, note))

    add(
        "00",
        "غلاف المجموعة",
        f"""
        <h2>مجموعة رسومات هندسية — QUANT_NQ</h2>
        <p>هذه ليست مقالة ولا دليل تسويق. هي <b>مخطط من الكود</b>: كل ذرّة في الجداول
        موجودة بـ <code>manifest.yaml</code>. النواة من <code>CORE.lock</code>.
        الجسر من <code>mt5/QUANT_NQ.mq5</code>.</p>
        <div class="kvs">
          <div>الاسم</div><div>QUANT_NQ — منصّة ذرّات، نواة لا تعرف أسماء الذرّات</div>
          <div>النواة</div><div>Core {esc(lock.get('core_version'))} · {esc(lock.get('file_count'))} ملفًا · بصمة {esc(str(lock.get('root_digest'))[:16])}…</div>
          <div>فوركس</div><div>{len(fx)} ذرّة · تلقائي {sum(1 for a in fx if a['startup']=='auto')} · يدوي {sum(1 for a in fx if a['startup']=='manual')}</div>
          <div>كريبتو</div><div>{len(cr)} ذرّة · تلقائي {rel.get('crypto_auto_start_atoms')} · يدوي {rel.get('crypto_manual_atoms')}</div>
          <div>أحداث مميّزة</div><div>نشر {len(pub)} · اشتراك {len(sub)} · اتحاد {len(set(pub)|set(sub))}</div>
          <div>الإطلاق</div><div>زرّان منفصلان — فوركس 8090/8010 · كريبتو 8091/8020</div>
        </div>
        {svg_layers()}
        """,
        "الرسم يُقرأ من الأرض (١) إلى اللوحة (٦). كل طبقة فوق التي قبلها، لا داخلها.",
    )

    # index filled after we know all — placeholder rebuilt at end? We'll add index as sheet 01 after listing planned numbers.
    # We'll generate 01 at the end by inserting. For now skip and prepend later.

    add(
        "02",
        "كيف تُقرأ هذه الرسوم",
        """
        <div class="legend">
          <span><i class="sw auto"></i> تلقائي startup_mode=auto</span>
          <span><i class="sw manual"></i> يدوي — النواة ترفض تشغيله في hot-reload</span>
        </div>
        <ul>
          <li>السهم في مسار الحي = حدث على الناقل، ليس استدعاء دالة.</li>
          <li>جدول القسم = جدول أبواب المعماري: كل ذرّة سطر، بنشرها واشتراكها.</li>
          <li>إذا تعارضت ورقة مع <code>atom.py</code> فالورقة تُصحَّح.</li>
          <li>الأسرار لا تُرسم: تُنشأ من لوحة الحوكمة على جهاز المالك.</li>
          <li>الإكسبرت غبيّ عمدًا: ينفّذ أوامر الجسر ولا يقرّر.</li>
        </ul>
        """,
    )

    add(
        "03",
        "الأرض — طبقات المشروع كما بُنيت",
        f"""
        <p>المالك وصف البناء: أرض → نواة → توابع → ذرّات → أحداث → طبقة حوكمة.
        الرسم في الغلاف هو نفس السلم. التفصيل:</p>
        <div class="layers">
          <div class="layer"><b>الأرض</b><span>المستودع، الأزرار، runtime لكل سوق، config/unified_release.json</span></div>
          <div class="layer"><b>النواة</b><span>اكتشاف، دورة حياة، ناقل، صحة، لقطة — بلا اسم ذرّة</span></div>
          <div class="layer"><b>التوابع</b><span>clock/ · transport/ · security/ · storage_policy/ · shared/</span></div>
          <div class="layer"><b>الذرّات</b><span>atoms/ فوركس · atoms_crypto/ كريبتو · كل واحدة مانيفست+atom.py</span></div>
          <div class="layer"><b>الأحداث</b><span>عقد النشر/الاشتراك في المانيفست — المادة: لا وصول مباشر بين الذرّات</span></div>
          <div class="layer"><b>الحوكمة</b><span>governance/server.py · الواجهة · الخزنة · ٩٠١ بوابة أوامر الإنسان</span></div>
        </div>
        """,
    )

    add(
        "04",
        "سوقان معزولان",
        f"""
        <div class="twin">
          <div>
            <h2>فوركس</h2>
            {table(['بند','قيمة'], [
                ['الذرّات', str(rel.get('forex_atoms'))],
                ['النواة', '127.0.0.1:8010'],
                ['اللوحة', '8090'],
                ['الإطلاق', rel['official_launchers']['forex']],
                ['الجسر', 'nq_brain.db · إكسبرت MT5'],
                ['التغذية التحليلية', '622 FIX cTrader'],
            ])}
          </div>
          <div>
            <h2>كريبتو</h2>
            {table(['بند','قيمة'], [
                ['الذرّات', str(rel.get('crypto_atoms_packaged'))],
                ['النواة', '127.0.0.1:8020'],
                ['اللوحة', '8091'],
                ['الإطلاق', rel['official_launchers']['crypto']],
                ['جذر البيانات', rel.get('crypto_data_root')],
                ['سلطة الإقلاع', rel.get('crypto_startup_authority')],
            ])}
          </div>
        </div>
        <p>عبارة «ناقل واحد» تعني ناقلًا داخل كل runtime، لا ناقلًا مشتركًا بين السوقين.</p>
        """,
    )

    add(
        "05",
        "النواة المختومة — 23 ملفًا",
        table(
            ["الملف", "الدور"],
            [[esc(a), esc(b)] for a, b in CORE_FILES],
        )
        + f"<p>الختم: core_version={esc(lock.get('core_version'))} · sealed_at={esc(lock.get('sealed_at'))} · file_count={esc(lock.get('file_count'))}</p>",
        "لا تُعدَّل هذه الملفات يدويًا دون كسر CORE.lock.",
    )

    add(
        "06",
        "عقد الذرّة ودورة الحياة",
        """
        <h2>AtomBase — ما تلتزم به كل ذرّة</h2>
        <ul>
          <li><code>initialize(context)</code> — تجهيز، بلا عمل.</li>
          <li><code>start()</code> / <code>stop()</code> — العمل والإيقاف القابل للعكس (إعادة الصحة).</li>
          <li><code>shutdown()</code> — إنهاء غير قابل للعكس عند إغلاق العملية فقط.</li>
          <li><code>health_check()</code> — healthy / degraded / unhealthy / unknown.</li>
          <li><code>snapshot</code> / <code>restore</code> — اختياري؛ None = تجاوز.</li>
        </ul>
        <h2>السياق AtomContext</h2>
        <p>atom_id · config · logger · publish · subscribe · subscribe_all اختياري.
        الذرّة لا تستورد وحدات النواة.</p>
        <h2>حالات AtomState</h2>
        <p>discovered → registered → initializing → initialized → starting → running
        → stopping → stopped · failed · unloaded</p>
        <h2>startup_mode</h2>
        <p><b>auto</b> تُقلعها النواة. <b>manual</b> تُكتشف وتُرفض في hot-reload
        (مثال فوركس: 107، 256–258، 625، 626، 630). هذا رفض تصميم لا عطل.</p>
        """,
    )

    add(
        "07",
        "الناقل — قانون الأحداث",
        f"""
        <p>كل تواصل سلوكي عبر أحداث. المقاس من المانيفستات:</p>
        {table(['', 'العدد'], [
            ['أحداث لها ناشر', str(len(pub))],
            ['أحداث لها مستمع', str(len(sub))],
            ['أحداث بلا مستمع في المانيفست', str(len(set(pub)-set(sub)))],
            ['اشتراكات بلا ناشر في المانيفست', str(len(set(sub)-set(pub)))],
        ])}
        <p>النبض الزمني <code>SYS_SECOND</code> ناشره 806 (فوركس). الاشتراك عليه شائع للدورات والجسور.</p>
        <p>أوراق لاحقة: كتالوج كامل ناشر/مستمع لكل اسم حدث.</p>
        """,
    )

    add(
        "08",
        "الزمن",
        """
        <p>الذرة <b>806 Time Tick</b> تنشر: SYS_SECOND · SYS_5MIN · SYS_15MIN · SYS_HOUR · SYS_DAY.
        لا تشترك على شيء. الناقل يختم الوقت من الساعة الرسمية لا من إزاحة الحدث.</p>
        <p>608 تُزامن NTP كعيّنات، وليست مالك زمن الإطلاق.</p>
        <p>المختبر/الباك تست يحقنان زمنًا تاريخيًا في مسار معزول — لا يحرّكان ساعة التداول الحي.</p>
        """,
    )

    add(
        "09",
        "مسار الحي فوركس — من التيك إلى الجسر",
        svg_live()
        + table(
            ["المرحلة", "الذرّة", "العقد"],
            [[esc(c), esc(a), esc(b)] for a, b, c in [(x[1], x[0], x[2]) for x in FOREX_LIVE]],
        )
        + """
        <p>٩٠١ لا تشتري. هي بوابة أوامر الإنسان: تفعيل أصل، بوابة تنفيذ، إيقاف، معايرة معتمدة.
        ٥٧٦ لا تكتب في الوسيط. ٥٥٢ تملك <code>trading.final_decision</code>.
        ٦٠١ تكتب صف PENDING. الإكسبرت ينفّذ.</p>
        """,
        "هذا مسار الفوركس كما في المانيفست. الكريبتو ناقل وسوق منفصلان.",
    )

    add(
        "10",
        "التنفيذ — 576 · 551 · 552 · 901",
        """
        <ul>
          <li><b>901</b> تنشر perpetual.asset.activate / deactivate و execution.gate.command و risk.halt.requested.</li>
          <li><b>576</b> تسمع التفعيل والقرار والhalt وتنشر execution.order.requested.</li>
          <li><b>551</b> تبني الأمر بعد risk.validation.completed.</li>
          <li><b>552</b> مدقق الأمر — enabled في المانيفست — تنشر trading.final_decision أو رفضًا.</li>
        </ul>
        <p>قرار wait / NOT_READY من التحليل والعقد الموحّد (عمق معلوم، اتجاه معلوم، هوية حساب+وسيط+رمز)
        يغلق بوابة READY. هذا حكم التحليل لا عطل نواة.</p>
        """,
    )

    add(
        "11",
        "الإكسبرت والجسر المشترك",
        """
        <p><code>mt5/QUANT_NQ.mq5</code> — منفّذ، بلا استراتيجية. القاعدة اسمًا
        <code>nq_brain.db</code> في Common\\Files (WAL). بايثون ٦٠١ يكتب commands،
        الإكسبرت يقرأ PENDING وينفّذ CTrade ويكتب النتيجة.</p>
        <h2>جداول تشغيلية (v2 حيث وُجدت)</h2>
        """
        + table(
            ["جدول", "من يكتب", "من يقرأ"],
            [
                ["account_v2", "الإكسبرت", "٦٠١ هوية CURRENT_ACCOUNT · ٦١٩"],
                ["ticks_v2", "الإكسبرت CopyTicks", "٦١٨ → تغذية MT5"],
                ["commands", "٦٠١", "الإكسبرت PumpCommands"],
                ["trade_events_v2", "الإكسبرت", "٦١١"],
                ["positions_v2", "الإكسبرت", "٦٠٩"],
                ["symbol_specs_v2", "الإكسبرت", "٦١٨"],
                ["display", "٦٠١ نبض", "لوحة الإكسبرت — بايثون حيّ؟"],
                ["calendar / candles_history / depth", "الإكسبرت", "٦١٦ / ٦٠٢ / مخزون"],
            ],
        )
        + "<p>magic الافتراضي 20260801 عند الطرفين. أمر بلا ملكية حساب/بناء يُرفض.</p>",
    )

    add(
        "12",
        "التغذية — FIX وMT5 والمجمّع",
        """
        <ul>
          <li><b>622</b> FIX cTrader — ينشر feed.ctrader.tick (تحليل/مرجع).</li>
          <li><b>618</b> تقرأ الجسر وتنشر feed.mt5.tick.</li>
          <li><b>613</b> تسمع الاثنين وتنشر market.tick.</li>
          <li><b>112</b> بوابة التيك الصالح → market.tick.validated. معظم التحليل يسمع هذا الحدث.</li>
          <li><b>103</b> تبني شموعًا من التيك الصالح. النظام تيكات؛ الشموع عدد محدود منها.</li>
        </ul>
        """,
    )

    add(
        "13",
        "الحوكمة — اللوحة والـAPI",
        "<p>تبويبات الواجهة (src/sections):</p>"
        + "<p>" + " · ".join(esc(x) for x in ui) + "</p>"
        + table(
            ["مسار", "دور"],
            [
                ["/gov/command", "أمر إنسان → ٩٠١"],
                ["/gov/lab", "مختبر معزول"],
                ["/gov/backtest", "باك تست رسمي + دفتر ورقي"],
                ["/gov/vault", "أسرار من اللوحة"],
                ["/gov/analysis/settings", "معايرة تحليل معتمدة"],
                ["/api/rescan", "إعادة اكتشاف النواة"],
                ["/gov/health · /gov/atoms", "صحة وتشغيل ذرّة"],
            ],
        ),
    )

    add(
        "14",
        "عزل المختبر عن الحي",
        svg_lab()
        + "<p>الملف الحاكم: <code>backtest/lab_sandbox.py</code> — overlays في var/lab/overrides/{id}.json. لا manifest ولا rescan.</p>",
    )

    add(
        "15",
        "الباك تست الرسمي",
        """
        <p><code>backtest/trade_replay.py</code> يشغّل ذرّات التداول الحقيقية على تيكات تاريخية.
        BLOCKED = {576, 601, 901, 622, 618}. الدفتر يسمع decision.resolved فقط.
        إن انتظرت الاستراتيجية، الدفتر لا يخترع صفقة.</p>
        <p>من–إلى، نافذة أخبار من الجسر إن وُجدت، ملف PnL بعد الجولة.</p>
        """,
    )

    add(
        "16",
        "الأمن",
        """
        <p>طبقة الخزنة في <code>security/</code> و<code>governance/vault_ops.py</code>.
        الأسرار تُنشأ من لوحة الحوكمة. المفتاح ليس لقفل اللوحة.
        ٦٢٢ تطلب سرًا باسم <code>ctrader_fix_password</code> من المزوّد — بلا كلمة سر في المانيفست.</p>
        <p>checkout بلا خزنة = SECURITY_STATE=NOT_CONFIGURED. هذا مصدر نظيف، لا عطل.</p>
        """,
    )

    add(
        "17",
        "الإطلاق والمنافذ",
        table(
            ["العقد", "الملف / المنفذ"],
            [
                ["فوركس رسمي", "أزرار التشغيل/تشغيل الفوركس الموحد.bat → لوحة 8090 · نواة 8010"],
                ["كريبتو رسمي", "أزرار التشغيل/تشغيل الكريبتو الموحد.bat → 8091 · 8020"],
                ["تهيئة", "تهيئة المشروع الموحد.bat"],
                ["غرفة القيادة", "غرفة القيادة.bat — أداة جذر، ليست عقد السوق"],
                ["إيقاف", "إيقاف النظام.bat"],
            ],
        ),
    )

    add(
        "18",
        "التخزين",
        """
        <p>ذرّات 701+ تحفظ أسعارًا وقراراتًا في مخازن SQLite تحت runtime/var لكل سوق.
        كريبتو: الجذر الوحيد crypto_runtime/var. فوركس: forex_runtime/var.
        جسر MT5 خارج هذا الشجر: Common\\Files\\nq_brain.db.</p>
        """,
    )

    add(
        "19",
        "الأخبار",
        """
        <p>411 News Strategic Regime تسمع market.news.enriched و market_data.news_received.
        بوت news_bot مصدر مستقل يكتب للجسر إن شُغّل. المختبر يقرأ نافذة اليوم من الجسر إن وُجدت،
        ولا يخترع عنوانًا.</p>
        """,
    )

    add(
        "20",
        "الصحة والاكتشاف",
        """
        <p>Discovery كل 5 ثوانٍ + POST /api/rescan. الصحة: interval من المانيفست.
        مثال ٦٠١: كل 5ث، عتبة 3 فشل، إعادة تشغيل حتى 5. غير صحية بعد الإقلاع تعني غالبًا
        أن account_v2 فارغ (الإكسبرت لم يكتب الحساب بعد) لأن الحساب CURRENT_ACCOUNT.</p>
        """,
    )

    # forex sections
    n = 21
    add(
        f"{n:02d}",
        "فهرس أقسام الفوركس",
        table(
            ["قسم", "ذرّات", "تلقائي", "يدوي"],
            [
                [
                    esc(sec),
                    str(len(fx_sec[sec])),
                    str(sum(1 for a in fx_sec[sec] if a["startup"] == "auto")),
                    str(sum(1 for a in fx_sec[sec] if a["startup"] == "manual")),
                ]
                for sec in sorted(fx_sec)
            ],
        ),
    )
    n += 1
    for sec in sorted(fx_sec):
        grp = fx_sec[sec]
        add(
            f"{n:02d}",
            f"جدول ذرّات — {sec}",
            table(
                ["id", "الاسم", "إصدار", "إقلاع", "حرجة", "تنشر", "تسمع"],
                atom_rows(grp),
                small=True,
            ),
            f"{len(grp)} ذرّة من المانيفست.",
        )
        n += 1

    add(
        f"{n:02d}",
        "فهرس أقسام الكريبتو",
        table(
            ["قسم", "ذرّات", "تلقائي", "يدوي"],
            [
                [
                    esc(sec),
                    str(len(cr_sec[sec])),
                    str(sum(1 for a in cr_sec[sec] if a["startup"] == "auto")),
                    str(sum(1 for a in cr_sec[sec] if a["startup"] == "manual")),
                ]
                for sec in sorted(cr_sec)
            ],
        ),
    )
    n += 1
    for sec in sorted(cr_sec):
        grp = cr_sec[sec]
        add(
            f"{n:02d}",
            f"جدول ذرّات كريبتو — {sec}",
            table(
                ["id", "الاسم", "إصدار", "إقلاع", "حرجة", "تنشر", "تسمع"],
                atom_rows(grp),
                small=True,
            ),
        )
        n += 1

    # event catalog in chunks of 40 events
    names = sorted(set(pub) | set(sub))
    chunk = 45
    for i in range(0, len(names), chunk):
        part = names[i : i + chunk]
        rows = []
        for e in part:
            rows.append(
                [
                    esc(e),
                    esc(", ".join(str(x) for x in pub.get(e, [])) or "—"),
                    esc(", ".join(str(x) for x in sub.get(e, [])) or "—"),
                ]
            )
        add(
            f"{n:02d}",
            f"كتالوج أحداث {i+1}–{i+len(part)} / {len(names)}",
            table(["الحدث", "ناشرون (id)", "مستمعون (id)"], rows, small=True),
        )
        n += 1

    # full atom schedule
    add(
        f"{n:02d}",
        "جدول كل ذرّات الفوركس (مختصر)",
        table(
            ["id", "الاسم", "إقلاع", "قسم"],
            [[esc(a["id"]), esc(a["name"]), "يدوي" if a["startup"]=="manual" else "تلقائي", esc(a["section"])] for a in fx],
            small=True,
        ),
    )
    n += 1
    add(
        f"{n:02d}",
        "جدول كل ذرّات الكريبتو (مختصر)",
        table(
            ["id", "الاسم", "إقلاع", "قسم"],
            [[esc(a["id"]), esc(a["name"]), "يدوي" if a["startup"]=="manual" else "تلقائي", esc(a["section"])] for a in cr],
            small=True,
        ),
    )
    n += 1
    add(
        f"{n:02d}",
        "ما ليس في المخطط عن قصد",
        """
        <ul>
          <li>كلمات السر وقيم الخزنة.</li>
          <li>محتوى var/ التشغيلي وقواعد SQLite الحيّة.</li>
          <li>منطق كل محلل سطرًا سطرًا — ذلك atom.py؛ الجدول يعطي عقد الحدث.</li>
        </ul>
        <p>عدد الأوراق في هذه المجموعة يُقرأ من الفهرس. إن تغيّر مانيفست، أُعيد توليد الملف
        بـ <code>python tools/build_architecture_atlas.py</code>.</p>
        """,
    )

    # insert index as 01
    idx_rows = [[esc(num), f'<a href="#p{esc(num)}">{esc(title)}</a>'] for num, title in toc]
    index_sheet = sheet(
        "01",
        "فهرس الأوراق",
        table(["ورقة", "الموضوع"], idx_rows, small=True),
        f"{len(toc)+1} ورقة في المجموعة (مع الفهرس).",
    )
    # toc[0] is 00, rest starts 02... we want 00, 01, 02...
    sheets_final = [sheets[0], index_sheet] + sheets[1:]
    toc_final = [toc[0], ("01", "فهرس الأوراق")] + toc[1:]

    nav = '<nav class="toc">' + " ".join(
        f'<a href="#p{esc(n)}">{esc(n)}</a>' for n, _ in toc_final
    ) + "</nav>"

    doc = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>QUANT_NQ — مخطط هندسي كامل</title>
<style>{CSS}</style>
</head>
<body>
{nav}
{''.join(sheets_final)}
</body>
</html>
"""
    return doc, len(sheets_final), len(fx), len(cr), len(names)


def main() -> None:
    doc, nsheets, nfx, ncr, nev = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(doc, encoding="utf-8")
    OUT2.write_text(doc, encoding="utf-8")
    print(f"wrote {OUT} sheets={nsheets} bytes={OUT.stat().st_size} fx={nfx} cr={ncr} events={nev}")
    print(f"copy {OUT2}")


if __name__ == "__main__":
    main()
