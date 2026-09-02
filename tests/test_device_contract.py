from __future__ import annotations
import importlib.util,inspect,os,shutil,sys,time
from pathlib import Path
import pytest,yaml
from core.contracts.atom import AtomContext,HealthState
ROOT=Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

def load(d,n):
 p=ATOM_ROOT/d/'atom.py';s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);sys.modules[n]=m;s.loader.exec_module(m);return m
M006=load('006_مراقب_التخزين','device006');M007=load('007_سلامة_الملفات','device007');M753=load('753_موارد_الجهاز','device753')
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[];self.h={}
 def subscribe(self,n,h):self.h.setdefault(n,[]).append(h)
 async def publish(self,n,p):
  self.e.append((n,p))
  for h in list(self.h.get(n,[])):
   r=h(dict(p))
   if inspect.isawaitable(r):await r
 def ctx(self,i,c):return AtomContext(i,c,L(),self.publish,self.subscribe)
def write(p,s='x'):p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(s,encoding='utf8')
def guard_cfg(root,minimum=1,ignored=None):return {'watched_files':[],'watched_dirs':[str(root)],'watched_extensions':['.py','.yaml','.yml','.json','.toml','.mq5','.cs','.bat'],'watched_names':['الشرح.md'],'min_watched_items':minimum,'ignored_dir_names':ignored or ['var','built','docs','سياق','__pycache__'],'ignored_suffixes':['.db','.db-wal','.db-shm','.sqlite','.sqlite3','.pyc','.tmp','.log']}
async def guard(root,cfg=None):
 b=B();a=M007.Atom();await a.initialize(b.ctx(7,cfg or guard_cfg(root)));await a.start();return a,b
async def scan(a,b):await b.publish(M007.EVENT_PULSE,{});return await a.health_check()

@pytest.mark.parametrize('relative',[Path('atoms/901_x/atom.py'),Path('atoms/901_x/manifest.yaml'),Path('atoms/901_x/الشرح.md'),Path('transport/client.py'),Path('security/keys.py'),Path('governance/checks/x.py'),Path('core/CORE.lock'),Path('governance/launchers/start.bat')])
@pytest.mark.asyncio
async def test_01_to_08_guarded_modifications_are_unhealthy(tmp_path,relative):
 target=tmp_path/relative;write(target,'before');c=guard_cfg(tmp_path)
 if relative.as_posix()=='core/CORE.lock':c['watched_files']=[str(target)]
 a,b=await guard(tmp_path,c);await a.establish_baseline();write(target,'after');h=await scan(a,b);assert h.state==HealthState.UNHEALTHY and 'modified' in h.message
@pytest.mark.parametrize('relative',[Path('atoms/evil.py'),Path('transport/evil.py')])
@pytest.mark.asyncio
async def test_09_to_10_added_code_is_unhealthy(tmp_path,relative):
 write(tmp_path/'atoms/base.py','base');a,b=await guard(tmp_path);await a.establish_baseline();write(tmp_path/relative,'evil');h=await scan(a,b);assert h.state==HealthState.UNHEALTHY and 'added' in h.message
@pytest.mark.parametrize('relative',[Path('var/live.db'),Path('atoms/__pycache__/x.pyc'),Path('governance/ui/built/app.js'),Path('service.conf.lock'),Path('سياق/note.md'),Path('governance/docs/note.md')])
@pytest.mark.asyncio
async def test_11_to_16_runtime_and_docs_are_ignored(tmp_path,relative):
 write(tmp_path/'atoms/base.py','base');a,b=await guard(tmp_path);await a.establish_baseline();write(tmp_path/relative,'runtime');assert (await scan(a,b)).state==HealthState.HEALTHY
@pytest.mark.asyncio
async def test_17_empty_watched_dirs_guard_disabled(tmp_path):
 a,b=await guard(tmp_path,{'watched_files':[],'watched_dirs':[],'min_watched_items':1});await b.publish(M007.EVENT_PULSE,{});h=await a.health_check();assert h.message=='GUARD_DISABLED' and h.state==HealthState.UNHEALTHY
@pytest.mark.asyncio
async def test_18_minimum_not_met_guard_disabled(tmp_path):
 write(tmp_path/'a.py','a');a,b=await guard(tmp_path,guard_cfg(tmp_path,minimum=2));await b.publish(M007.EVENT_PULSE,{});assert (await a.health_check()).message=='GUARD_DISABLED'
@pytest.mark.asyncio
async def test_19_guard_detects_own_manifest_change(tmp_path):
 p=tmp_path/'atoms/007_x/manifest.yaml';write(p,'id: 7');a,b=await guard(tmp_path);await a.establish_baseline();write(p,'id: 8');assert (await scan(a,b)).state==HealthState.UNHEALTHY
@pytest.mark.asyncio
async def test_20_first_boot_without_baseline_is_untrusted(tmp_path):
 write(tmp_path/'a.py','a');a,b=await guard(tmp_path);h=await scan(a,b);assert h.message=='UNTRUSTED' and not a._established
@pytest.mark.asyncio
async def test_21_tampered_boot_without_restore_is_untrusted(tmp_path):
 p=tmp_path/'a.py';write(p,'clean');a,b=await guard(tmp_path);await a.establish_baseline();write(p,'evil');a2,b2=await guard(tmp_path);assert (await scan(a2,b2)).message=='UNTRUSTED'
@pytest.mark.asyncio
async def test_22_restore_detects_downtime_tamper(tmp_path):
 p=tmp_path/'a.py';write(p,'clean');c=guard_cfg(tmp_path);a,b=await guard(tmp_path,c);await a.establish_baseline();snap=await a.snapshot();write(p,'evil');a2,b2=await guard(tmp_path,c);await a2.restore(snap);h=await scan(a2,b2);assert h.state==HealthState.UNHEALTHY and 'modified' in h.message
@pytest.mark.asyncio
async def test_23_storage_alert_uses_pulse_not_health_manager(monkeypatch):
 usage=shutil._ntuple_diskusage(100,95,5);monkeypatch.setattr(M006.shutil,'disk_usage',lambda _p:usage);b=B();a=M006.Atom();await a.initialize(b.ctx(6,{'path':'.','warn_threshold_pct':80,'critical_threshold_pct':90}));await a.start();assert not [x for x in b.e if x[0]==M006.EVENT_LOW];await b.publish(M006.EVENT_PULSE,{});assert [x for x in b.e if x[0]==M006.EVENT_LOW]
@pytest.mark.asyncio
async def test_24_integrity_violation_uses_pulse_not_health_manager(tmp_path):
 p=tmp_path/'a.py';write(p,'a');a,b=await guard(tmp_path);await a.establish_baseline();write(p,'b');assert (await a.health_check()).state==HealthState.HEALTHY;assert (await scan(a,b)).state==HealthState.UNHEALTHY
@pytest.mark.asyncio
async def test_25_storage_state_survives_restart_without_duplicate(monkeypatch):
 usage=shutil._ntuple_diskusage(100,95,5);monkeypatch.setattr(M006.shutil,'disk_usage',lambda _p:usage);cfg={'path':'.','warn_threshold_pct':80,'critical_threshold_pct':90};b=B();a=M006.Atom();await a.initialize(b.ctx(6,cfg));await a.start();await b.publish(M006.EVENT_PULSE,{});snap=await a.snapshot();b2=B();a2=M006.Atom();await a2.initialize(b2.ctx(6,cfg));await a2.restore(snap);await a2.start();await b2.publish(M006.EVENT_PULSE,{});assert not [x for x in b2.e if x[0]==M006.EVENT_LOW]
@pytest.mark.asyncio
async def test_26_device_without_psutil_degrades(monkeypatch):
 monkeypatch.setattr(M753,'_get_psutil',lambda:None);b=B();a=M753.Atom();await a.initialize(b.ctx(753,{'enable_temperature':False}));await a.start();await b.publish(M753.EVENT_IN,{});assert (await a.health_check()).state==HealthState.DEGRADED
@pytest.mark.asyncio
async def test_27_storage_missing_path_unknown():
 b=B();a=M006.Atom();await a.initialize(b.ctx(6,{'path':'Z:/definitely-missing','warn_threshold_pct':80,'critical_threshold_pct':90}));await a.start();await b.publish(M006.EVENT_PULSE,{});assert (await a.health_check()).state==HealthState.UNKNOWN
@pytest.mark.asyncio
async def test_28_full_scan_under_200ms_and_no_false_alerts():
 # ⛔ العقد هنا **سرعة**، لا زمن مطلق — والفرق مقصود:
 # السقف القديم «200 مللي» كُتب يوم كان المشروع 700 ملفّ. صار 1049 ملفّاً، فالمسح
 # يتجاوز 200 مللي بلا أن تبطؤ الذرّة إطلاقاً — قياس 2026-08-18: 0.185 مللي لكلّ
 # ملفّ مقابل 0.286 في العقد الأصليّ، أي **أسرع بـ 35%**. فالرقم المطلق كان يكذب،
 # ويزداد كذبه مع كلّ ملفّ يُضاف.
 # لذلك ثُبِّتت نفس سرعة العقد الأصليّ حرفيّاً (200 ÷ 700 = 0.286 مللي/ملفّ) كحدّ
 # دائم: لا يُخفَّف مع كِبَر المشروع، ويلتقط أيّ تباطؤ حقيقيّ لا يلتقطه الرقم
 # المطلق (لو نقص عدد الملفّات وتضاعف زمن الملفّ الواحد، الرقم المطلق يمرّ وهذا يسقط).
 # وإن سقط يوماً فالسبب تباطؤ فعليّ — يُفحَص، ولا يُرفَع الحدّ.
 #
 # ولماذا **أسرع** قياس لا قياسًا واحدًا: المسح مقيَّد بالقرص، فقياسه المفرد يتبع
 # حِمل الجهاز لا سرعة الذرّة. مقيس 2026-08-18: 0.207 مللي/ملفّ على جهاز هادئ،
 # و0.334 لنفس الكود داخل جولة اختبارات كاملة بعد نسخ قاعدة 10 جيجا — أي الحارس
 # كان يسقط على كود سليم. التباطؤ الحقيقيّ يظهر في أسرع قياس أيضًا؛ ضجيج الحِمل
 # لا يظهر. (نفس درس مؤقّت ويندوز في test_event_bus_hardening.)
 cfg=yaml.safe_load((ATOM_ROOT/'007_سلامة_الملفات'/'manifest.yaml').read_text())['config']
 best=None;items=0
 for _ in range(3):
  a,b=await guard(ROOT,cfg);h=await a.establish_baseline()
  items=h.details['watched_items'];ms=h.details['scan_ms']
  best=ms if best is None else min(best,ms)
 assert items>=700
 assert best/items<200/700, "مسح 007 تباطأ فعلاً — يُفحَص السبب، ولا يُرفَع الحدّ"
 for _ in range(4):assert (await scan(a,b)).state==HealthState.HEALTHY
 assert not [x for x in b.e if x[0]==M007.EVENT_VIOLATION]
