# -*- coding: utf-8 -*-
"""مفتاح سرعة التحليل — عقد الاشتقاق المعلَن (ورقة v1.0 + الملحق ٢٦-٠٨).

نقطة التطابق 50.00 = سلوك اليوم حرفيًّا · لا قفزات مخفية · أرضيات معلنة ·
سقف ذاكرة المراكم 63 · النطاق (حساب␟رمز) يعلو العام · الفصل عن المخاطر grep.
"""
from pathlib import Path

from build_registry.paths import RegistryAtomRoot

from shared import analysis_speed as sp


def test_match_point_is_today_exactly():
    f = sp.speed_factor(50.0)
    assert f == 1.0
    assert sp.window(12, f, 4) == 12
    assert sp.window(32, f, 8) == 32
    assert sp.window(24, f, 6) == 24


def test_declared_factor_law():
    assert sp.speed_factor(100.0) == 0.5
    assert sp.speed_factor(25.0) == 2.0
    assert sp.speed_factor(10.0) == 5.0
    assert sp.speed_factor(1.0) == 5.0      # سقف البطء المعلن
    assert sp.speed_factor(500.0) == 0.2    # سقف السرعة المعلن
    assert sp.speed_factor(None) == 1.0     # قيمة فاسدة ⇒ نقطة التطابق
    assert sp.speed_factor(-3.0) == 1.0


def test_windows_floors_and_cap():
    fast = sp.speed_factor(100.0)
    assert sp.window(12, fast, 4) == 6
    assert sp.window(32, fast, 8) == 16
    assert sp.window(24, fast, 6) == 12
    slow = sp.speed_factor(10.0)
    assert sp.window(12, slow, 4) == 60
    assert sp.window(32, slow, 8) == 63     # سقف ذاكرة المراكم
    assert sp.window(3, fast, 4) == 4       # الأرضية الإحصائية لا تُخترق


def test_no_hidden_jumps_monotonic():
    previous = None
    for tenth in range(10, 1001):
        speed = tenth / 10.0
        w = sp.window(12, sp.speed_factor(speed), 4)
        if previous is not None:
            assert w <= previous, (speed, w, previous)  # أسرع ⇒ نافذة لا تطول
        previous = w


def test_scope_resolution_chain(monkeypatch):
    calls = []

    def fake_approved(name, fallback, scope="global"):
        calls.append((name, scope))
        if name == "ANALYSIS_SPEED" and scope == "52992818\x1fBTCUSD":
            return 22.0
        return fallback

    monkeypatch.setattr(sp, "approved_value", fake_approved)
    assert sp.speed_value("52992818", "BTCUSD") == 22.0
    assert sp.speed_value() == sp.MATCH_POINT
    assert ("ANALYSIS_SPEED", "global") in calls


def test_scoped_apply_command(tmp_path):
    """اعتماد نطاقي (حساب␟رمز) لعيار معلَن scoped — على سجل مؤقت لا الحي."""
    from shared.decision_dials import apply_command
    from shared.parameter_registry import ParameterRegistry

    registry = ParameterRegistry(tmp_path / "params.db")
    base = {"value": 22.0, "command_id": "c-scope-1", "operator": "nq",
            "approved_at": 1.0, "account_id": "52992818", "symbol": "XAUUSD"}
    applied = apply_command({"name": "ANALYSIS_SPEED", **base},
                            atom_id="150", registry=registry)
    assert applied is not None
    assert applied["scope"] == "52992818\x1fXAUUSD"
    assert applied["value"] == 22.0
    row = registry.get("ANALYSIS_SPEED", "52992818\x1fXAUUSD")
    assert row is not None and row["status"] == "APPROVED"
    # العام لم يُمسّ — يبقى غير معتمد بقيمته.
    global_row = registry.get("ANALYSIS_SPEED", "global")
    assert global_row is not None and global_row["status"] == "UNAPPROVED"

    # عيار غير معلَن scoped يتجاهل حقلي النطاق (حماية النمط النائم): يبقى عامًا.
    applied2 = apply_command({"name": "RISK_DIAL", **base,
                              "command_id": "c-scope-2", "value": 40.0},
                             atom_id="581", registry=registry)
    assert applied2 is not None and applied2["scope"] == "global"


def test_risk_files_never_read_speed():
    """الفصل الصارم §6/§33: ملفات المخاطر لا تستورد مفتاح السرعة — مثبت لا موعود."""
    root = Path(__file__).resolve().parents[1]
    risk_files = [
        root / "shared" / "position_delta_recompute.py",
        *(next(RegistryAtomRoot(root).glob(f"{atom_id}_*")) / "atom.py"
          for atom_id in ("581", "508", "516", "518", "584", "552")),
    ]
    for path in risk_files:
        text = path.read_text(encoding="utf-8")
        assert "analysis_speed" not in text, path
        assert "ANALYSIS_SPEED" not in text, path
