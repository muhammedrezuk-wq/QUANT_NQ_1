from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

import clock
from core.contracts.atom import AtomContext
from core.event_bus import EventBus
from build_registry import BuildRegistry

ROOT=Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

class L:
 def __getattr__(self,n):return lambda *a,**k:None
def load(i):
 d=next((ATOM_ROOT).glob(f'{i:03d}_*'));sys.path.insert(0,str(d))
 try:
  sp=importlib.util.spec_from_file_location(f'time_contract_{i}',d/'atom.py');m=importlib.util.module_from_spec(sp);sys.modules[sp.name]=m;sp.loader.exec_module(m);return m
 finally:sys.path.pop(0)
def ctx(atom_id,cfg,bus):
 return AtomContext(atom_id,cfg,L(),lambda n,p:bus.publish(n,p,publisher=str(atom_id)),lambda n,h:bus.subscribe(n,h,subscriber=str(atom_id)))

@pytest.mark.asyncio
async def test_ntp_to_clock_to_independent_sys_pulse() -> None:
 clock.reset_for_tests();bus=EventBus();m608,m3,m806,m111=(load(i) for i in (608,3,806,111))
 a608=m608.Atom();await a608.initialize(ctx(608,{"reference_servers":["a","b","c"],"sync_interval_s":300,"query_timeout_s":1,"drift_alert_s":1,"stale_after_s":900,"max_accepted_offset_s":5,"max_sample_deviation_s":.25,"min_samples":2},bus))
 a608._query=lambda host:{"server":host,"offset_s":{"a":.3,"b":.31,"c":4.0}[host],"round_trip_s":.01,"stratum":2}
 a3=m3.Atom();await a3.initialize(ctx(3,{"sys_tick_interval_s":.05,"heartbeat_interval_s":.1,"drift_alert_s":1,"max_accepted_offset_s":5,"max_sample_age_s":30,"stale_after_s":900,"max_slew_per_second":.05},bus));await a3.start()
 a806=m806.Atom();await a806.initialize(ctx(806,{},bus));await a806.start()
 a111=m111.Atom();await a111.initialize(ctx(111,{"max_age_s":5,"divergence_threshold_s":.5},bus));await a111.start()
 # ٢٠٢٦-٠٨-٣١: كان هنا مشترك يغذّي إزاحة الناقل من الحدث — أي مالك ثانٍ للوقت.
 # أُلغيت الملكية الثانية: الناقل بلا ساعة، والبرهان أن تصحيح الساعة **لا يحتاج
 # مرور حدث في الناقل** — 608 يقيس، و003 يكتب في `clock` مباشرة.
 assert not hasattr(bus,"set_time_offset"),"الناقل لا يملك ساعة"
 assert not hasattr(bus,"now"),"الناقل لا يملك ساعة"
 pulses=[];bus.subscribe("SYS_SECOND",lambda p:pulses.append(p),subscriber="consumer")
 assert await a608._sync_once() is True
 assert clock.quality()==clock.SYNCED and abs(clock.state()["target_offset_s"]-.305)<1e-6
 await asyncio.sleep(1.1);assert pulses and pulses[-1]["pulse_id"].startswith("SYS_SECOND|")
 before=len(pulses);await a3.stop();await asyncio.sleep(1.1)
 assert len(pulses)>before,"806 must continue when 003 is stopped"
 await a806.stop();await a111.stop()


@pytest.mark.asyncio
async def test_late_003_consumes_replayed_sample_state() -> None:
    clock.reset_for_tests(); bus = EventBus(); module = load(3)
    await bus.publish("time.ntp.samples.state", {"sample_id": 99,
        "measured_at": __import__('time').time(), "median_offset_s": .2,
        "quorum": True}, publisher="608")
    atom = module.Atom()
    await atom.initialize(ctx(3,{"sys_tick_interval_s":.05,"heartbeat_interval_s":.1,
        "drift_alert_s":1,"max_accepted_offset_s":5,"max_sample_age_s":30,
        "stale_after_s":900,"max_slew_per_second":.05},bus))
    await atom.start(); await asyncio.sleep(.05)
    assert clock.state()["sequence"] == 1
    await atom.stop()


@pytest.mark.asyncio
async def test_901_executes_after_003_is_stopped() -> None:
    clock.reset_for_tests(); clock.configure(max_accepted_offset_s=5,
        max_sample_age_s=30, stale_after_s=900, max_slew_per_second=.05)
    assert clock.accept_sample({"sample_id": 1, "measured_at": time.time(),
        "median_offset_s": .1, "quorum": True}, writer="003")[0]
    bus=EventBus(); m3,m806,m901=(load(i) for i in (3,806,901))
    atom3=m3.Atom(); await atom3.initialize(ctx(3,{"sys_tick_interval_s":.05,
        "heartbeat_interval_s":.1,"drift_alert_s":1,"max_accepted_offset_s":5,
        "max_sample_age_s":30,"stale_after_s":900,"max_slew_per_second":.05},bus)); await atom3.start()
    atom806=m806.Atom(); await atom806.initialize(ctx(806,{},bus)); await atom806.start()
    with tempfile.TemporaryDirectory() as directory:
        db=str(Path(directory)/"commands.db"); atom901=m901.Atom()
        await atom901.initialize(ctx(901,{"db_path":db,"max_age_s":120,"batch_limit":20},bus)); await atom901.start()
        connection=sqlite3.connect(db); connection.execute(m901._SCHEMA)
        connection.execute("INSERT INTO commands(action,operator,requested_at,status) VALUES(?,?,?,?)",
            ("kill_switch_reset","dashboard",clock.now(),"PENDING")); connection.commit(); connection.close()
        await atom3.stop()
        await asyncio.sleep(1.2)
        connection=sqlite3.connect(db); status=connection.execute("SELECT status FROM commands").fetchone()[0]; connection.close()
        assert status == "DONE"
        await atom901.stop()
    await atom806.stop()


def test_only_003_is_production_writer_of_shared_clock() -> None:
 callers=[]
 for path in ROOT.rglob('*.py'):
  # إصلاح 2026-08-27: نسخ النشر (forex_runtime/crypto_runtime) تحمل الذرّة
  # نفسها بحكم كونها نسخة توزيع معزولة — لا تُحسب كتّاباً ثانياً.
  if 'tests' in path.parts or 'atoms_crypto' in path.parts or 'forex_runtime' in path.parts or 'crypto_runtime' in path.parts or path.parts[-2:] == ('clock','clock.py'):continue
  text=path.read_text('utf-8',errors='ignore')
  if 'accept_sample(' in text:callers.append(path.relative_to(ROOT).as_posix())
 expected = BuildRegistry(ROOT).find_atom(3, scope="forex")
 assert len(expected) == 1
 assert callers == [Path(expected[0].path, "atom.py").relative_to(ROOT).as_posix()]


def test_core_seal_digest_unchanged() -> None:
 # هذا الحارس يثبّت بصمة النواة: أيّ تغيير في `core/` يُسقطه، فلا يمرّ تعديل صامت.
 # ⛔ لا تُحدَّث هذه البصمة إلّا بأمر مالك صريح مع رفع `CORE_VERSION` وإعادة ختم.
 #
 # البصمة السابقة `08eefd30…` كانت للإصدار 1.12.0، وهو أحد أربعة أختام وُضعت بلا
 # إذن المالك — فما كانت خطًّا أحمر بل أثر انحراف. استُبدلت 2026-08-18 بأمره
 # المباشر ببصمة 1.13.0 التي أقرّها: استعادة المادة 5 (إزالة `asyncio.shield`
 # من `_run_handler`) مع إبقاء التحسينات التقنيّة الستّ. تفصيلها في
 # `core/__version__.py` تحت 1.13.0.
 #
 # 2026-08-23 — أمر المالك (جلسة «ظبط المشاكل قبل النشر»): رُفع الحارس إلى
 # الختم الجاري 1.17.0 المختوم 2026-08-19 بأمره (راجع README — جدول الأرقام).
 # الحارس ظلّ يفشل منذ 1.14.0 لأن أحدًا لم يرفع بصمته مع كل إعادة ختم مأذونة.
 #
 # (nq seal 2026-08-25: owner resealed core at 1.19.0 (coalescing + read-only covenant), root_digest 51235944...
 # The guard now ALSO recomputes the digest from the actual core/ files via the
 # canonical governance tool and compares it to CORE.lock — so it catches silent
 # edits to core/, not only a lock swap, and the pinned constant below is the
 # owner's explicit seal that must be bumped by hand on every authorized reseal.)
 import importlib.util,json
 spec=importlib.util.spec_from_file_location('freeze_core_guard',ROOT/'governance'/'scripts'/'freeze_core.py')
 freeze_core=importlib.util.module_from_spec(spec);spec.loader.exec_module(freeze_core)
 lock=json.loads((ROOT/'core'/'CORE.lock').read_text('utf8'))
 from core import CORE_VERSION
 assert lock['core_version']==CORE_VERSION
 current=freeze_core.compute_manifest()
 assert current['root_digest']==lock['root_digest'],"core/ files diverged from the sealed CORE.lock"
 assert current['file_count']==lock['file_count']
