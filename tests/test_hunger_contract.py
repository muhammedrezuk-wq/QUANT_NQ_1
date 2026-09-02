"""عقد النضارة — «سليمة» يجب ألّا تعني «صائمة».

السبب المقيس (2026-08-21): فحص الجوع داخل الذرّات يسأل «هل رأيتُ شيئًا يومًا؟»
لا «هل وصلني شيء مؤخّرًا؟». فحين أطعم الإحماءُ (602) المحلّلاتِ مئتَي شمعة عند
الإقلاع، عبرت العتبة مرّة واحدة إلى الأبد. وبعد توقّف باني الشموع (103) بقيت
71 ذرّة متجمّدة عند العدد 200 بالضبط — كلّها تعلن «سليمة» خضراء بينما لم يصلها
شيء منذ ساعتين. متحفٌ يبتسم.

فالقياس خرج من الذرّة إلى الحوكمة: لا تسأل الذرّة عن حالها، بل تراقب الأرقام
التي تنشرها عن نفسها. ما لم يتحرّك رقم، لم يحصل شغل. وهذه الاختبارات تحرس أن
يبقى الأمر كذلك.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)



@pytest.fixture(scope="module")
def gov():
    spec = importlib.util.spec_from_file_location(
        "governance_server", ROOT / "governance" / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _atom(aid: int, message: str, state: str = "running",
          health: str = "healthy", details: dict | None = None) -> dict:
    return {"id": aid, "state": state,
            "health": {"state": health, "message": message, "details": details or {}}}


def test_unmoving_counters_are_the_same_fingerprint(gov):
    """تمثالان متتاليان ببصمة واحدة — لا تقدّم."""
    first = gov._progress_print(_atom(151, "candles=200 emitted=200 tracked=1"))
    again = gov._progress_print(_atom(151, "candles=200 emitted=200 tracked=1"))
    assert first == again


def test_one_moving_counter_changes_the_fingerprint(gov):
    """رقم واحد تحرّك = شغل حصل، ولو بقي النصّ نفسه."""
    before = gov._progress_print(_atom(151, "candles=200 emitted=200 tracked=1"))
    after = gov._progress_print(_atom(151, "candles=201 emitted=201 tracked=1"))
    assert before != after


def test_details_alone_can_prove_progress(gov):
    """ذرّة نصّها ثابت بالتصميم (مثل 613 'forwarding 7 symbols') لا تُتّهم ظلمًا
    ما دام رقمٌ في تفاصيلها يتحرّك."""
    still = _atom(613, "forwarding 7 symbols via 2 routes", details={"forwarded": 10})
    moved = _atom(613, "forwarding 7 symbols via 2 routes", details={"forwarded": 11})
    assert gov._progress_print(still) != gov._progress_print(moved)


def test_hunger_starts_at_zero_on_first_sight(gov):
    """الحوكمة لا تدّعي علمًا بما لم تشهده: أوّل رصد سكونه صفر."""
    gov._HUNGER.clear()
    verdict = gov.hunger_of(_atom(901, "x=1"), now=1_000.0, limit=10.0)
    assert verdict["idle_s"] == 0.0 and verdict["hungry"] is False


def test_frozen_atom_becomes_hungry_past_its_declared_limit(gov):
    """أرقامها لم تتحرّك وتجاوزت حدَّها المعلَن ⟶ جائعة، والشارة برتقالية."""
    gov._HUNGER.clear()
    statue = _atom(151, "candles=200 emitted=200 tracked=1")
    gov.hunger_of(statue, now=1_000.0, limit=30.0)          # أوّل رصد
    statue["hunger"] = gov.hunger_of(statue, now=1_041.0, limit=30.0)
    assert statue["hunger"]["idle_s"] == pytest.approx(41.0)
    assert statue["hunger"]["hungry"] is True
    assert gov.label(statue) == ("جائعة", "amber")


def test_moving_atom_never_goes_hungry(gov):
    """ما دام رقم يتحرّك، السكون يعود صفرًا مهما طال الزمن."""
    gov._HUNGER.clear()
    alive = _atom(622, "ticks=1 depth=1")
    gov.hunger_of(alive, now=1_000.0, limit=5.0)
    alive = _atom(622, "ticks=2 depth=2")
    alive["hunger"] = gov.hunger_of(alive, now=1_600.0, limit=5.0)
    assert alive["hunger"]["idle_s"] == 0.0
    assert alive["hunger"]["hungry"] is False
    assert gov.label(alive) == ("سليمة", "green")


def test_no_declared_limit_means_no_verdict(gov):
    """قانون «لا اختراع مدد»: بلا حدٍّ معلَن تُقاس المدّة وتُعرض، ولا يصدر حكم."""
    gov._HUNGER.clear()
    statue = _atom(151, "candles=200 emitted=200")
    gov.hunger_of(statue, now=1_000.0, limit=None)
    statue["hunger"] = gov.hunger_of(statue, now=99_000.0, limit=None)
    assert statue["hunger"]["idle_s"] > 90_000          # المدّة صادقة ومعروضة
    assert statue["hunger"]["declared"] is False
    assert statue["hunger"]["hungry"] is False          # ولا حكم بلا رقم المالك
    assert gov.label(statue) == ("سليمة", "green")


def test_atom_that_publishes_no_number_is_outside_measurement_not_guilty(gov):
    """مقيس 2026-08-21: 15 ذرّة من 212 تقول كلمة مجرّدة بلا رقم (`ACTIVE` ·
    `NO_VOLUME_YET` · `reconciliation_live`). لا نملك ما نقيسه فيها، فلا تُتّهم.
    اتّهام من لا نستطيع قياسه هو مرض التمثال الأخضر مقلوبًا."""
    gov._HUNGER.clear()
    mute = _atom(116, "ACTIVE")
    gov.hunger_of(mute, now=1_000.0, limit=5.0)
    mute["hunger"] = gov.hunger_of(mute, now=9_000.0, limit=5.0)
    assert mute["hunger"]["measurable"] is False
    assert mute["hunger"]["idle_s"] > 7_000        # المدّة تبقى صادقة ومعروضة
    assert mute["hunger"]["hungry"] is False       # ولا تهمة بلا قياس
    assert gov.label(mute) == ("سليمة", "green")


def test_a_single_number_makes_an_atom_measurable(gov):
    """رقم واحد يكفي ليخرج من دائرة «خارج القياس» إلى دائرة المحاسبة."""
    assert gov._reports_numbers(_atom(116, "ACTIVE")) is False
    assert gov._reports_numbers(_atom(116, "ACTIVE forwarded=0")) is True
    assert gov._reports_numbers(_atom(116, "ACTIVE", details={"seen": 0})) is True


def test_stopped_atom_is_not_accused_of_hunger(gov):
    """الواقفة واقفة بأمر، لا جائعة."""
    gov._HUNGER.clear()
    off = _atom(258, "idle", state="stopped")
    gov.hunger_of(off, now=1_000.0, limit=5.0)
    off["hunger"] = gov.hunger_of(off, now=9_000.0, limit=5.0)
    assert off["hunger"]["hungry"] is False
    assert gov.label(off) == ("واقفة", "grey")


def test_degraded_keeps_its_own_voice(gov):
    """ذرّة تقول «متعثّرة» بنفسها لا يبتلع الجوعُ صوتَها."""
    gov._HUNGER.clear()
    sick = _atom(618, "MT5_TICK_FEED_STALE", health="degraded")
    gov.hunger_of(sick, now=1_000.0, limit=5.0)
    sick["hunger"] = gov.hunger_of(sick, now=9_000.0, limit=5.0)
    assert sick["hunger"]["hungry"] is True
    assert gov.label(sick)[0] == "متعثّرة"


def test_validator_demands_a_declared_limit():
    """حارس المدقّق حيًّا: ذرّة تستقبل حدثًا بلا max_idle_s يُرصد نقصُها.

    يُشغَّل المدقّق كما يُشغَّل فعلًا (عملية مستقلّة) لا بالاستيراد: الحارس الذي
    لا يُختبَر بالطريقة التي يعمل بها ليس حارسًا.
    """
    import json
    import os
    import subprocess
    import sys

    analyzer = next((d for d in (ATOM_ROOT).iterdir()
                     if d.is_dir() and d.name.startswith("151_")), None)
    if analyzer is None:
        pytest.skip("الذرّة 151 غير موجودة على القرص")
    manifest = (analyzer / "manifest.yaml").read_text(encoding="utf-8-sig")
    if "max_idle_s" in manifest:
        pytest.skip("151 أعلنت حدَّها — الحالة المختبَرة هنا هي غيابه")

    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    run = subprocess.run(
        [sys.executable, str(ROOT / "governance" / "scripts" / "validate_atoms.py"),
         "--atom", "151", "--json"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), env=env)
    report = json.loads(run.stdout)
    hunger = [f for f in report["findings"] if f["check"] == "حدّ الجوع"]
    assert hunger, "المدقّق لم يرصد غياب max_idle_s على ذرّة تستقبل أحداثًا"
    assert "max_idle_s" in hunger[0]["detail"]
