# -*- coding: utf-8 -*-
"""اختبارات 580 — محرك الترجيح متعدد المستويات (ختم NQ بند 22 حزمة ث — ق١٠ §٤٦
مكيّفة للمنحنى المستمر بتعديل الاستمرارية §٥٢).

المدخل الوحيد قرار معتمد عبر decision.gate.passed؛ المنحنيات ملك المالك عبر
tilt.rule.command؛ المخازن اختبارية مؤقتة حصرًا (tempfile) — قاعدة حية ممنوعة.
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path as _Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts.atom import AtomContext, HealthState  # noqa: E402
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_atom580", _Path(__file__).resolve().parents[1] / "atom.py")
_mod = _ilu.module_from_spec(_spec)
sys.modules["_atom580"] = _mod
_spec.loader.exec_module(_mod)
Atom = _mod.Atom

EVENT_GATE = _mod.EVENT_GATE_PASSED
EVENT_RULE = _mod.EVENT_RULE_COMMAND
EVENT_RULES_STATE = _mod.EVENT_RULES_STATE
EVENT_TILT = _mod.EVENT_TILT_STATE

_EPS = 1e-9
_CID = [0]


class _NullLogger:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def critical(self, *a, **k): pass


class FakeEventBus:
    def __init__(self):
        self.published = []
        self.subs = {}

    def subscribe(self, name, handler):
        self.subs.setdefault(name, []).append(handler)

    async def publish(self, name, payload):
        self.published.append((name, payload))

    def make_context(self, config):
        return AtomContext(atom_id=580, config=config, logger=_NullLogger(),
                           publish=self.publish, subscribe=self.subscribe)


async def _new(db_path, cap=1.0):
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"db_path": db_path, "tilt_max_total": cap}))
    await atom.start()
    return atom, bus


def _cid():
    _CID[0] += 1
    return _CID[0]


def rule(field, points, side="abs", enabled=True, operator="dashboard",
         at=1000.0, cid=None):
    """حمولة tilt.rule.command كما تنشرها بوابة 901 حرفيًّا."""
    return {"field": field, "side": side, "points": points,
            "enabled": enabled, "operator": operator, "origin": "901",
            "reason": "OWNER_COMMAND",
            "command_id": cid if cid is not None else _cid(),
            "command_requested_at": at}


def gate(symbol="GOLD", state="READY", decision="D1", cycle="C1",
         side="buy", gated_at=1000.0, unknown=None, **fields):
    """حمولة decision.gate.passed بمفاتيح السلك الفعلية (453→467):
    direction_value / strength_value / confidence_value / current_depth /
    required_depth / ratio / weight / aggregate_state."""
    p = {"account_id": "A", "broker": "B", "symbol": symbol,
         "timeframe": "60s", "period_start": "PS", "cycle_id": cycle,
         "decision_id": decision, "gate_request_id": decision + ":req1",
         "decision_side": side, "gate_state": "PASSED", "gated_at": gated_at,
         "direction": side}
    if state is not None:
        p["aggregate_state"] = state
    if unknown is not None:
        p["unknown_fields"] = unknown
    p.update(fields)
    return p


def _tilts(bus):
    return [p for n, p in bus.published if n == EVENT_TILT]


def _rules_states(bus):
    return [p for n, p in bus.published if n == EVENT_RULES_STATE]


async def test_1_first_level_interpolated(tmp_path):
    print("\n--- (١) تجاوز أول نقطة + الاستيفاء الخطي بمثال عددي دقيق ---")
    atom, bus = await _new(str(tmp_path / "t1.db"))
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10], [85.0, 0.20]]))
    await atom._on_gate_passed(gate(confidence_value=79.9999))
    below = _tilts(bus)[-1]
    assert abs(below["total_raw"]) < _EPS, below["total_raw"]
    assert below["contributions"]["confidence"]["note"] == "below_first_point"
    await atom._on_gate_passed(gate(confidence_value=80.0))
    at_point = _tilts(bus)[-1]
    assert abs(at_point["total_raw"] - 0.10) < _EPS, at_point["total_raw"]
    await atom._on_gate_passed(gate(confidence_value=82.0))
    mid = _tilts(bus)[-1]
    # الاستيفاء: 0.10 + (0.20-0.10)×(82-80)/(85-80) = 0.14 بالضبط
    assert abs(mid["total_raw"] - 0.14) < _EPS, mid["total_raw"]
    entry = mid["contributions"]["confidence"]
    assert entry["note"] == "interpolated"
    assert entry["curve_active"] == [[80.0, 0.10], [85.0, 0.20]], entry
    assert abs(mid["total_capped"] - 0.14) < _EPS
    print("OK — 79.9999→0 · 80→0.10 · 82→0.14 (منحنى متصل لا شرائح)")


async def test_2_second_level_and_flat_tail(tmp_path):
    print("\n--- (٢) النقطة الثانية أعلى وما بعد آخر نقطة ثابت ---")
    atom, bus = await _new(str(tmp_path / "t2.db"))
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10], [85.0, 0.20]]))
    await atom._on_gate_passed(gate(confidence_value=85.0))
    assert abs(_tilts(bus)[-1]["total_raw"] - 0.20) < _EPS
    await atom._on_gate_passed(gate(confidence_value=93.7))
    tail = _tilts(bus)[-1]
    assert abs(tail["total_raw"] - 0.20) < _EPS, tail["total_raw"]
    assert tail["contributions"]["confidence"]["note"] == "beyond_last_point"
    print("OK — 85→0.20 · 93.7→0.20 (فوق آخر نقطة قيمة آخر نقطة)")


async def test_3_return_softens_by_itself(tmp_path):
    print("\n--- (٣) الرجوع يخفف لحاله — تدرج بلا علوق (§٧/§٢٧/§٤٥) ---")
    atom, bus = await _new(str(tmp_path / "t3.db"))
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10], [85.0, 0.20]]))
    seen = []
    for value in (84.0, 81.0, 76.0):
        await atom._on_gate_passed(gate(confidence_value=value))
        seen.append(_tilts(bus)[-1]["total_raw"])
    assert abs(seen[0] - 0.18) < _EPS, seen
    assert abs(seen[1] - 0.12) < _EPS, seen
    assert abs(seen[2] - 0.0) < _EPS, seen
    print("OK — 84→0.18 ثم 81→0.12 ثم 76→0 (لا يبقى عالقًا على قيمة قديمة)")


async def test_4_big_jump_no_slab_accumulation(tmp_path):
    print("\n--- (٤) قفزة كبيرة = قيمة المنحنى عند الوصول لا تراكم شرائح ---")
    atom, bus = await _new(str(tmp_path / "t4.db"))
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10], [85.0, 0.20]]))
    await atom._on_gate_passed(gate(confidence_value=88.0))
    jump = _tilts(bus)[-1]
    # ليس 0.10+0.20=0.30 (تراكم مستويات) بل قيمة المنحنى عند 88 = 0.20
    assert abs(jump["total_raw"] - 0.20) < _EPS, jump["total_raw"]
    print("OK — 0→88 دفعة واحدة = 0.20 (لا 0.30)")


async def test_5_confidence_alone_moves_only_itself(tmp_path):
    print("\n--- (٥) تغير الثقة وحدها يغير مساهمتها فقط ---")
    atom, bus = await _new(str(tmp_path / "t5.db"))
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10], [85.0, 0.20]]))
    await atom._on_rule_command(rule("strength", [[50.0, 0.05], [75.0, 0.15]]))
    await atom._on_gate_passed(gate(confidence_value=82.0, strength_value=70.0))
    first = _tilts(bus)[-1]
    # القوة: 0.05 + 0.10×(70-50)/(75-50) = 0.13
    assert abs(first["contributions"]["strength"]["tilt"] - 0.13) < _EPS
    assert abs(first["contributions"]["confidence"]["tilt"] - 0.14) < _EPS
    assert abs(first["total_raw"] - 0.27) < _EPS
    await atom._on_gate_passed(gate(confidence_value=84.0, strength_value=70.0))
    second = _tilts(bus)[-1]
    assert abs(second["contributions"]["strength"]["tilt"] - 0.13) < _EPS
    assert abs(second["contributions"]["confidence"]["tilt"] - 0.18) < _EPS
    assert abs(second["total_raw"] - 0.31) < _EPS
    print("OK — الثقة 82→84 حركت مساهمتها وحدها (0.14→0.18) والقوة ثابتة 0.13")


async def test_6_depth_alone_moves_only_itself(tmp_path):
    print("\n--- (٦) تغير العمق وحده يغير مساهمته فقط ---")
    atom, bus = await _new(str(tmp_path / "t6.db"))
    await atom._on_rule_command(rule("current_depth", [[60.0, 0.05], [90.0, 0.35]]))
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10], [85.0, 0.20]]))
    await atom._on_gate_passed(gate(current_depth=75.0, confidence_value=82.0))
    first = _tilts(bus)[-1]
    # العمق: 0.05 + 0.30×(75-60)/(90-60) = 0.20
    assert abs(first["contributions"]["current_depth"]["tilt"] - 0.20) < _EPS
    await atom._on_gate_passed(gate(current_depth=81.0, confidence_value=82.0))
    second = _tilts(bus)[-1]
    # العمق: 0.05 + 0.30×(81-60)/(90-60) = 0.26
    assert abs(second["contributions"]["current_depth"]["tilt"] - 0.26) < _EPS
    assert abs(second["contributions"]["confidence"]["tilt"] - 0.14) < _EPS
    print("OK — العمق 75→81 حرك مساهمته وحدها (0.20→0.26)")


async def test_7_unknown_is_declared_zero_not_negative(tmp_path):
    print("\n--- (٧) المجهول صفر معلَن لا دليل سلبي (§٤٢) ---")
    atom, bus = await _new(str(tmp_path / "t7.db"))
    # منحنى يعطي قيمة سالبة عند الصفر — لو قُرئ المجهول كصفر حقيقي لأنتج -0.3
    await atom._on_rule_command(rule("confidence", [[0.0, -0.3], [50.0, 0.0]]))
    await atom._on_gate_passed(gate(confidence_value=0.0,
                                    unknown=["confidence"]))
    declared = _tilts(bus)[-1]
    entry = declared["contributions"]["confidence"]
    assert entry["note"] == "unknown" and entry["value"] is None, entry
    assert abs(entry["tilt"]) < _EPS and abs(declared["total_raw"]) < _EPS
    # الصفر الحقيقي غير المعلن مجهولًا يمر على المنحنى فعلًا (لا معنى خاصًّا للصفر)
    await atom._on_gate_passed(gate(confidence_value=0.0))
    real_zero = _tilts(bus)[-1]
    assert abs(real_zero["contributions"]["confidence"]["tilt"] + 0.3) < _EPS
    # كلمة الاتجاه ليست رقمًا: بلا direction_value الاتجاه مجهول لا يُقرأ من الكلمة
    await atom._on_rule_command(rule("direction", [[10.0, 0.5]], side="up"))
    await atom._on_gate_passed(gate())
    word_only = _tilts(bus)[-1]
    assert word_only["contributions"]["direction"]["note"] == "unknown"
    print("OK — unknown_fields ⇒ صفر معلَن «unknown»؛ والصفر الحقيقي يُحسب؛ والكلمة لا تُقرأ رقمًا")


async def test_8_no_gate_no_tilt_blocked_generates_nothing(tmp_path):
    print("\n--- (٨) لا حدث بوابة ⇒ لا ترجيح · وblocked لا يولّد شيئًا ---")
    atom, bus = await _new(str(tmp_path / "t8.db"))
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10]]))
    assert len(_tilts(bus)) == 0, "قاعدة بلا قرار معتمد لا تنشئ ترجيحًا"
    # 580 لا يشترك أصلًا بغير decision.gate.passed من أحداث البوابة
    assert EVENT_GATE in bus.subs
    assert "decision.gate.blocked" not in bus.subs
    assert "decision.gate.recorded" not in bus.subs
    assert "decision.scored.state" not in bus.subs, "لا درجات ما قبل الفلاتر"
    # ودفاعيًا: حمولة غير PASSED وصلت للمعالج لا تولّد شيئًا
    payload = gate(confidence_value=99.0)
    payload["gate_state"] = "BLOCKED"
    await atom._on_gate_passed(payload)
    assert len(_tilts(bus)) == 0
    assert atom._ignored_not_passed == 1
    print("OK — بلا بوابة لا شيء؛ blocked لا يولّد شيئًا؛ ولا اشتراك بدرجات ما قبل الفلاتر")


async def test_9_caps_clip_both_sides(tmp_path):
    print("\n--- (٩) الحدان يقصّان [−TILT_MAX_TOTAL,+TILT_MAX_TOTAL] (§٢٣/§٤٣) ---")
    atom, bus = await _new(str(tmp_path / "t9.db"), cap=0.15)
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10], [85.0, 0.20]]))
    await atom._on_rule_command(rule("strength", [[50.0, 0.05], [75.0, 0.15]]))
    await atom._on_gate_passed(gate(confidence_value=82.0, strength_value=70.0))
    plus = _tilts(bus)[-1]
    assert abs(plus["total_raw"] - 0.27) < _EPS
    assert abs(plus["total_capped"] - 0.15) < _EPS, plus["total_capped"]
    atom2, bus2 = await _new(str(tmp_path / "t9b.db"), cap=0.15)
    await atom2._on_rule_command(rule("confidence", [[80.0, -0.14]]))
    await atom2._on_rule_command(rule("strength", [[50.0, -0.05], [75.0, -0.15]]))
    await atom2._on_gate_passed(gate(confidence_value=82.0, strength_value=70.0))
    minus = _tilts(bus2)[-1]
    assert abs(minus["total_raw"] + 0.27) < _EPS
    assert abs(minus["total_capped"] + 0.15) < _EPS, minus["total_capped"]
    # الافتراض المختوم: بلا رقم من المالك الحد 0.0 ولا يخرج أي ترجيح
    atom3, bus3 = await _new(str(tmp_path / "t9c.db"), cap=0.0)
    await atom3._on_rule_command(rule("confidence", [[80.0, 0.10], [85.0, 0.20]]))
    await atom3._on_gate_passed(gate(confidence_value=82.0))
    sealed = _tilts(bus3)[-1]
    assert abs(sealed["total_raw"] - 0.14) < _EPS
    assert sealed["total_capped"] == 0.0, sealed["total_capped"]
    print("OK — +0.27→+0.15 · −0.27→−0.15 · وبحد 0.0 الافتراضي لا يخرج ترجيح")


async def test_10_restart_restores_rules_and_last_tilt(tmp_path):
    print("\n--- (١٠) الاستعادة بعد الإقلاع من نفس المخزن (§٣٩) ---")
    db = str(tmp_path / "t10.db")
    atom, bus = await _new(db)
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10], [85.0, 0.20]]))
    await atom._on_gate_passed(gate(confidence_value=82.0, decision="D9"))
    live = _tilts(bus)[-1]
    assert abs(live["total_capped"] - 0.14) < _EPS
    # نسخة ذرّة جديدة على نفس المخزن المؤقت
    atom2, bus2 = await _new(db)
    states = _rules_states(bus2)
    assert states and states[-1]["restored"] is True
    restored_rule = states[-1]["rules"][0]
    assert restored_rule["field"] == "confidence"
    assert restored_rule["points"] == [[80.0, 0.10], [85.0, 0.20]]
    assert restored_rule["enabled"] is True and restored_rule["version"] == 1
    tilts = _tilts(bus2)
    assert tilts, "آخر ترجيح للرمز يُنشر بعد الإقلاع — لا قفزة"
    restored = tilts[-1]
    assert restored["symbol"] == "GOLD" and restored["restored"] is True
    assert restored["decision_id"] == "D9"
    assert abs(restored["total_capped"] - 0.14) < _EPS
    assert abs(restored["contributions"]["confidence"]["tilt"] - 0.14) < _EPS
    # والقاعدة المستعادة تعمل فعلًا على قرار جديد
    await atom2._on_gate_passed(gate(confidence_value=85.0, decision="D10"))
    fresh = _tilts(bus2)[-1]
    assert abs(fresh["total_capped"] - 0.20) < _EPS
    print("OK — القواعد وآخر ترجيح لكل رمز استُعيدا ونُشرا من المخزن نفسه")


async def test_11_state_barrier(tmp_path):
    print("\n--- (١١) الحالة حاجز: NOT_READY يمنع الزيادة وSTALE يمنع (§١٥/§٤١) ---")
    atom, bus = await _new(str(tmp_path / "t11.db"))
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10], [85.0, 0.20]]))
    await atom._on_gate_passed(gate(confidence_value=82.0, state="NOT_READY"))
    not_ready = _tilts(bus)[-1]
    assert abs(not_ready["total_raw"] - 0.14) < _EPS
    assert not_ready["total_capped"] == 0.0
    assert not_ready["state_barrier"] == {"state": "NOT_READY",
                                          "ruling": "BLOCK_INCREASE"}
    await atom._on_gate_passed(gate(confidence_value=82.0, state="STALE"))
    stale = _tilts(bus)[-1]
    assert stale["total_capped"] == 0.0
    assert stale["state_barrier"]["ruling"] == "BLOCK_ALL"
    await atom._on_gate_passed(gate(confidence_value=82.0, state="READY"))
    ready = _tilts(bus)[-1]
    assert abs(ready["total_capped"] - 0.14) < _EPS
    assert ready["state_barrier"]["ruling"] == "ALLOW"
    # NOT_READY يمنع الزيادة فقط — التخفيف (السالب) يمر؛ وSTALE يمنع حتى السالب
    atom2, bus2 = await _new(str(tmp_path / "t11b.db"))
    await atom2._on_rule_command(rule("confidence", [[80.0, -0.14]]))
    await atom2._on_gate_passed(gate(confidence_value=82.0, state="NOT_READY"))
    soften = _tilts(bus2)[-1]
    assert abs(soften["total_capped"] + 0.14) < _EPS, soften["total_capped"]
    await atom2._on_gate_passed(gate(confidence_value=82.0, state="STALE"))
    frozen = _tilts(bus2)[-1]
    assert frozen["total_capped"] == 0.0
    print("OK — NOT_READY: +0.14→0 والسالب يمر؛ STALE: منع كامل؛ READY يسمح")


async def test_12_direction_two_independent_sides(tmp_path):
    print("\n--- الاتجاهية: منحنيان مستقلان والوسط لا يُجبر (§٢٥/§٢٦) ---")
    atom, bus = await _new(str(tmp_path / "t12.db"))
    await atom._on_rule_command(rule("direction", [[60.0, 0.05], [70.0, 0.20]],
                                     side="up"))
    await atom._on_rule_command(rule("direction", [[60.0, -0.05], [70.0, -0.20]],
                                     side="down"))
    await atom._on_gate_passed(gate(direction_value=64.0))
    up = _tilts(bus)[-1]
    # 0.05 + 0.15×(64-60)/(70-60) = 0.11
    assert abs(up["contributions"]["direction"]["tilt"] - 0.11) < _EPS
    await atom._on_gate_passed(gate(direction_value=-64.0, side="sell"))
    down = _tilts(bus)[-1]
    assert abs(down["contributions"]["direction"]["tilt"] + 0.11) < _EPS
    await atom._on_gate_passed(gate(direction_value=0.0))
    middle = _tilts(bus)[-1]
    entry = middle["contributions"]["direction"]
    assert abs(entry["tilt"]) < _EPS and entry["note"] == "middle_zone", entry
    await atom._on_gate_passed(gate(direction_value=30.0))
    weak = _tilts(bus)[-1]
    assert abs(weak["contributions"]["direction"]["tilt"]) < _EPS
    assert weak["contributions"]["direction"]["note"] == "below_first_point"
    print("OK — +64→+0.11 · −64→−0.11 (بمقدار العتبة) · 0→وسط بلا إجبار · +30→0")


async def test_13_rule_command_validation(tmp_path):
    print("\n--- أوامر القواعد: تحقق العقد ورفض الحالة والوزن (§١٣/§١٥) ---")
    atom, bus = await _new(str(tmp_path / "t13.db"))
    cases = [
        (rule("state", [[1.0, 0.1]]), "FIELD_NOT_CURVABLE_OR_UNKNOWN"),
        (rule("weight", [[1.0, 0.1]]), "FIELD_NOT_CURVABLE_OR_UNKNOWN"),
        (rule("mystery", [[1.0, 0.1]]), "FIELD_NOT_CURVABLE_OR_UNKNOWN"),
        (rule("confidence", [[85.0, 0.2], [80.0, 0.1]]), "POINTS_INVALID"),
        (rule("confidence", [[80.0, 0.1], [80.0, 0.2]]), "POINTS_INVALID"),
        (rule("confidence", [[80.0, True]]), "POINTS_INVALID"),
        (rule("confidence", [[float("nan"), 0.1]]), "POINTS_INVALID"),
        (rule("confidence", [[i, 0.01] for i in range(13)]), "POINTS_INVALID"),
        (rule("confidence", [[80.0, 0.1]], side="sideways"), "SIDE_INVALID"),
        (rule("confidence", [[80.0, 0.1]], enabled=1), "ENABLED_NOT_BOOL"),
        (rule("confidence", [[80.0, 0.1]], operator=""), "OPERATOR_REQUIRED"),
    ]
    no_id = rule("confidence", [[80.0, 0.1]])
    no_id["command_id"] = None
    cases.append((no_id, "COMMAND_ID_REQUIRED"))
    bad_at = rule("confidence", [[80.0, 0.1]])
    bad_at["command_requested_at"] = "soon"
    cases.append((bad_at, "COMMAND_REQUESTED_AT_INVALID"))
    for payload, reason in cases:
        before = atom._commands_rejected
        await atom._on_rule_command(payload)
        assert atom._commands_rejected == before + 1, reason
        assert atom._last_reject == reason, (atom._last_reject, reason)
    assert len(atom._rules) == 0
    # start() نشرت حالة واحدة صادقة «بلا منحنيات» (§٣٩) — والرفض لا يزيد عليها
    assert len(_rules_states(bus)) == 1 and _rules_states(bus)[0]["count"] == 0
    # القائمة الفارغة قانونية (مسح منحنى) — نفس قانون 901
    await atom._on_rule_command(rule("confidence", []))
    assert atom._commands_applied == 1
    cleared = _rules_states(bus)[-1]["rules"][0]
    assert cleared["points"] == [] and cleared["enabled"] is True
    await atom._on_gate_passed(gate(confidence_value=99.0))
    empty = _tilts(bus)[-1]
    assert abs(empty["total_raw"]) < _EPS
    assert empty["contributions"]["confidence"]["note"] == "no_points"
    print("OK — 13 رفضًا معللًا · state/weight ليسا سلمين · المسح [] قانوني")


async def test_14_idempotent_duplicate_and_versioning(tmp_path):
    print("\n--- تكرار command_id لا يعيد التطبيق والنسخة تتصاعد بالتعديل ---")
    db = str(tmp_path / "t14.db")
    atom, bus = await _new(db)
    same = rule("confidence", [[80.0, 0.10]], cid=77)
    await atom._on_rule_command(same)
    await atom._on_rule_command(dict(same))
    assert atom._commands_applied == 1 and atom._commands_duplicate == 1
    assert atom._rules[("confidence", "abs")]["version"] == 1
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10], [90.0, 0.30]]))
    assert atom._rules[("confidence", "abs")]["version"] == 2
    conn = sqlite3.connect(db)
    audit = conn.execute(
        "SELECT command_id, version FROM tilt_rules_audit"
        " WHERE field='confidence' ORDER BY audit_id").fetchall()
    stored = conn.execute(
        "SELECT points_json, version FROM tilt_rules"
        " WHERE field='confidence' AND side='abs'").fetchone()
    conn.close()
    assert audit[0][0] == "77" and len(audit) == 2, audit
    assert json.loads(stored[0]) == [[80.0, 0.10], [90.0, 0.30]]
    assert stored[1] == 2
    print("OK — سجل التدقيق يحمل هوية الأمر مرة واحدة والنسخة 1→2")


async def test_15_disable_rule(tmp_path):
    print("\n--- تعطيل قاعدة يوقف مساهمتها معلَنًا (§١٩) ---")
    atom, bus = await _new(str(tmp_path / "t15.db"))
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10], [85.0, 0.20]]))
    await atom._on_gate_passed(gate(confidence_value=82.0))
    assert abs(_tilts(bus)[-1]["total_raw"] - 0.14) < _EPS
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10], [85.0, 0.20]],
                                     enabled=False))
    await atom._on_gate_passed(gate(confidence_value=82.0))
    off = _tilts(bus)[-1]
    assert abs(off["total_raw"]) < _EPS
    assert off["contributions"]["confidence"]["note"] == "disabled"
    print("OK — نفس القيمة بعد التعطيل: مساهمة صفر بعلة «disabled»")


async def test_16_no_default_curves_and_full_reason(tmp_path):
    print("\n--- لا منحنيات افتراضية والسبب كامل بكل نشرة (§١٨/§٢١/§٣٧) ---")
    atom, bus = await _new(str(tmp_path / "t16.db"))
    await atom._on_gate_passed(gate(direction_value=71.0, strength_value=68.0,
                                    confidence_value=82.0, current_depth=79.0,
                                    required_depth=70.0, ratio=84.0,
                                    weight=55.0))
    bare = _tilts(bus)[-1]
    assert abs(bare["total_raw"]) < _EPS and bare["total_capped"] == 0.0
    contributions = bare["contributions"]
    assert set(contributions) == {"direction", "strength", "confidence",
                                  "current_depth", "required_depth", "weight",
                                  "ratio", "state"}
    for field in ("direction", "strength", "confidence", "current_depth",
                  "required_depth", "ratio"):
        assert contributions[field]["note"] == "no_curve", field
    assert contributions["weight"]["note"] == "not_curvable"
    assert contributions["weight"]["value"] == 55.0
    assert contributions["state"]["note"] == "barrier"
    assert contributions["state"]["value"] == "READY"
    assert bare["state_barrier"]["ruling"] == "ALLOW"
    assert bare["decision_id"] == "D1"
    assert bare["gate_request_id"] == "D1:req1"
    assert bare["decision_side"] == "buy"
    assert bare["account_id"] == "A" and bare["broker"] == "B"
    assert bare["timeframe"] == "60s" and bare["cycle_id"] == "C1"
    assert bare["source_timestamp"] == 1000.0
    print("OK — المحرك يبدأ بلا منحنيات (المجموع 0) والنشرة تحمل الهوية والسبب كاملين")


async def test_17_health(tmp_path):
    print("\n--- الصحة: UNHEALTHY→DEGRADED→HEALTHY ---")
    bus = FakeEventBus()
    atom = Atom()
    await atom.initialize(bus.make_context(
        {"db_path": str(tmp_path / "t17.db"), "tilt_max_total": 0.0}))
    assert (await atom.health_check()).state == HealthState.UNHEALTHY
    await atom.start()
    health = await atom.health_check()
    assert health.state == HealthState.DEGRADED
    assert health.message == "NO_GATE_DECISION_YET"
    await atom._on_gate_passed(gate())
    health = await atom.health_check()
    assert health.state == HealthState.HEALTHY
    assert health.details["tilt_published"] == 1
    assert health.details["tilt_max_total"] == 0.0
    print("OK — الصحة تدرّجت وبياناتها تحمل الحد والعدادات")


async def test_18_missing_gated_at_falls_back_to_now_no_crash(tmp_path):
    print("\n--- gated_at غائب: يسقط لوقت الآن بلا انهيار (NameError سابقًا) ---")
    db = str(tmp_path / "t18.db")
    atom, bus = await _new(db)
    payload = gate(confidence_value=50.0)
    del payload["gated_at"]
    before = time.time()
    # قبل الإصلاح: NameError('time' غير مستورَد بـgate_runner) كانت تصل هنا
    # وتُسقِط المعالج بالكامل بعد نشر tilt.state وقبل كتابة اليوميّة.
    await atom._on_gate_passed(payload)
    after = time.time()
    assert _tilts(bus), "tilt.state لم يُنشر أصلًا"
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT changed_at FROM tilt_state_journal"
        " ORDER BY rowid DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None, "اليوميّة لم تُكتب — الانهيار منع الوصول لها"
    assert before - 1.0 <= row[0] <= after + 1.0, row[0]
    print("OK — بلا gated_at: لا انهيار، واليوميّة كُتبت بختم وقتٍ حقيقي (الآن)")


async def test_19_journal_write_off_loop_thread(tmp_path):
    print("\n--- كتابة اليوميّة لا تجمّد حلقة الحدث (asyncio.to_thread فعليًّا) ---")
    atom, bus = await _new(str(tmp_path / "t19.db"))
    await atom._on_rule_command(rule("confidence", [[80.0, 0.10]]))
    real_journal = atom._journal

    def slow_journal(*a, **k):
        time.sleep(0.3)
        return real_journal(*a, **k)

    atom._journal = slow_journal
    order = []

    async def other_task():
        await asyncio.sleep(0.05)
        order.append("other_task")

    async def gate_call():
        await atom._on_gate_passed(gate(confidence_value=82.0))
        order.append("gate_call")

    # لو بقيت الكتابة معلَّقة بخيط حلقة الحدث (قبل الإصلاح) لَما استطاع
    # other_task إنهاء نومه الأقصر (٥٠ms) قبل عودة gate_call من الحجب
    # الكامل (٣٠٠ms) -- الترتيب كان سينعكس.
    await asyncio.gather(other_task(), gate_call())
    assert order == ["other_task", "gate_call"], order
    print("OK — حلقة الحدث بقيت حرّة أثناء كتابة اليوميّة (المهمّة الأخرى أنهت أولًا)")


async def main():
    tests = [test_1_first_level_interpolated, test_2_second_level_and_flat_tail,
             test_3_return_softens_by_itself,
             test_4_big_jump_no_slab_accumulation,
             test_5_confidence_alone_moves_only_itself,
             test_6_depth_alone_moves_only_itself,
             test_7_unknown_is_declared_zero_not_negative,
             test_8_no_gate_no_tilt_blocked_generates_nothing,
             test_9_caps_clip_both_sides,
             test_10_restart_restores_rules_and_last_tilt,
             test_11_state_barrier, test_12_direction_two_independent_sides,
             test_13_rule_command_validation,
             test_14_idempotent_duplicate_and_versioning,
             test_15_disable_rule, test_16_no_default_curves_and_full_reason,
             test_17_health, test_18_missing_gated_at_falls_back_to_now_no_crash,
             test_19_journal_write_off_loop_thread]
    failed = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        tmp_path = _Path(tmp_dir)
        for test in tests:
            try:
                await test(tmp_path)
            except AssertionError as e:
                failed.append((test.__name__, str(e)))
                print(f"FAILED: {test.__name__}: {e}")
            except Exception as e:
                failed.append((test.__name__, repr(e)))
                print(f"ERROR: {test.__name__}: {e!r}")
    print("\n" + "=" * 60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}")
        sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
