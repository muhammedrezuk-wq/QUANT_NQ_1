from __future__ import annotations

import importlib.util
import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "governance" / "ui"


def load_server():
    path = ROOT / "governance" / "server.py"
    spec = importlib.util.spec_from_file_location("analysis_dashboard_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_analysis_panel_contains_all_live_fields_and_owner_controls():
    source = (UI / "src" / "sections" / "Analysis.tsx").read_text(encoding="utf-8")
    required_arabic = [
        "الحساب", "الأصل", "المحلل", "الاتجاه", "الثقة", "العمق الحالي",
        "العمق المطلوب", "عيار الثقة", "الوزن", "الحالة", "آخر تحديث",
        "الوزن النشط", "الوزن الغائب", "لا يُعاد توزيعه خفيةً",
        "اللوحة لا تصنع قرار تداول", "جاهز للقرار",
    ]
    assert all(text in source for text in required_arabic)
    assert "confirmedCommand('analysis_setting'" in source
    assert "required_depth" in source and "confidence_threshold" in source and "weight" in source
    assert "نطاق التحليل والضبط" in source


def test_analysis_store_is_account_and_symbol_scoped_and_confidence_is_0_to_100():
    store = (UI / "src" / "core" / "store.ts").read_text(encoding="utf-8")
    engine = (UI / "src" / "core" / "engine.ts").read_text(encoding="utf-8")
    assert "confidence: number              // 0..100" in store
    assert "current_depth?: number" in store and "required_depth?: number" in store
    assert "active_weight?: number" in store and "missing_weight?: number" in store
    assert "`${p.account_id ?? 'بلا حساب'}::${p.symbol}`" in engine


def test_visible_static_dashboard_copy_has_no_known_english_brand_leaks():
    # نفحص النصوص الحرفية فقط؛ أسماء المكتبات والحقول البرمجية ليست نص واجهة.
    forbidden = {"QUANT_NQ", "MT5", "React", "WebGL", "Kill-Switch", "Equity", "BotFather"}
    leaks: list[str] = []
    for path in (UI / "src").rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        source = re.sub(r"//.*", "", path.read_text(encoding="utf-8"))
        for quote, value in re.findall(r"(['\"])(.*?)(?<!\\)\1", source):
            # واردات وحروف العقد البرمجية ليست معروضة؛ النص العربي المختلط أو نص JSX هو المقصود.
            if re.search(r"[\u0600-\u06ff]", value):
                for word in forbidden:
                    if word in value:
                        leaks.append(f"{path.relative_to(UI)}: {value}")
        for value in re.findall(r">\s*([^<>{}\n]+?)\s*<", source):
            for word in forbidden:
                if word in value:
                    leaks.append(f"{path.relative_to(UI)}: {value}")
    assert not leaks, "\n".join(leaks)


def test_all_unknown_runtime_text_falls_back_to_arabic_not_raw_identifier():
    arabic = (UI / "src" / "core" / "arabic.ts").read_text(encoding="utf-8")
    assert "arabicVisible" in arabic
    assert "حالة غير معروفة" in arabic and "تفصيل تقني غير مترجم" in arabic
    checks = {
        "sections/Decision.tsx": "{ t: 'غير معروف'",
        "sections/Liquidity.tsx": "'حالة غير معروفة'",
        "sections/Structure.tsx": "'اتجاه غير معروف'",
        "sections/Execution.tsx": "'سبب غير معروف'",
        "sections/Monitor.tsx": "return ['حدث', 'grey']",
    }
    for relative, marker in checks.items():
        assert marker in (UI / "src" / relative).read_text(encoding="utf-8")


def test_governance_settings_read_is_scoped_and_persistent(tmp_path: Path):
    server = load_server()
    database = tmp_path / "analysis_settings.db"
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE analysis_settings(
            account_id TEXT,symbol TEXT,analyzer_id TEXT,required_depth REAL,
            confidence_threshold REAL,weight REAL,revision INTEGER,updated_at REAL,updated_by TEXT,
            PRIMARY KEY(account_id,symbol,analyzer_id))""")
        connection.executemany("INSERT INTO analysis_settings VALUES(?,?,?,?,?,?,?,?,?)", [
            ("A", "NQ", "trend", 71, 66, 19, 2, 1.0, "المالك"),
            ("B", "NQ", "trend", 10, 11, 12, 3, 2.0, "المالك"),
        ])
    server.ANALYSIS_SETTINGS_DB = database
    a = server.analysis_settings("A", "nq")
    b = server.analysis_settings("B", "NQ")
    assert a["settings"]["trend"]["required_depth"] == 71
    assert a["settings"]["trend"]["confidence_threshold"] == 66
    assert a["settings"]["trend"]["weight"] == 19
    assert b["settings"]["trend"]["weight"] == 12
    assert len(a["settings"]) == 15


def test_built_dashboard_was_regenerated_with_analysis_copy():
    # إصلاح 2026-08-27: governance/ui/built/ مستبعد من git عمداً، فالنسخة
    # المستنسخة حديثاً لا تحمل بناءً — هذا فحص إصدار (يُشغَّل بعد npm build)
    # وليس فحص وحدة، فلا يجب أن يسقط على نسخة نظيفة.
    built_index = UI / "built" / "index.html"
    if not built_index.exists():
        pytest.skip(
            "governance/ui/built/ غير مبنيّ في هذه النسخة (مستبعد من git) — "
            "شغّل بناء الواجهة أولاً ليفحص تطابق النسخة المبنية"
        )
    index = built_index.read_text(encoding="utf-8")
    match = re.search(r'src="([^"]+\.js)"', index)
    assert match
    bundle = (UI / "built" / match.group(1).lstrip("/")).read_text(encoding="utf-8")
    assert "نطاق التحليل والضبط" in bundle
    assert "الوزن الغائب" in bundle
    assert "اللوحة لا تصنع قرار تداول" in bundle
