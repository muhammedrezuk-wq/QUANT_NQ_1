import asyncio,importlib.util,sys
from pathlib import Path
root=Path(__file__).resolve().parents[3];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));s=importlib.util.spec_from_file_location('a650',folder/'atom.py');m=importlib.util.module_from_spec(s);sys.modules['a650']=m;s.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
async def test_no_publish_before_pulse_and_one_snapshot_per_pulse():
    # عقد المالك ٩٠-١١ نقطة ٢+٣ (٢٠٢٦-٠٨-١٩): طبقة المحافظ تجمع آخر حالة فقط
    # خلال نبضة SYS_SECOND، وتنشر مرة واحدة لكل account/نبضة -- لا عند كل
    # رسالة واردة. ثلاث تحديثات لنفس الحساب قبل أي نبضة = صفر نشر؛ نبضة واحدة
    # بعدها = نشرة واحدة بالضبط تحمل الحالة المجمّعة كلها.
    b=B();a=m.Atom()
    await a.initialize(m.AtomContext(650,{},L(),b.publish,b.subscribe))
    await a.start()
    await a._on_component({'account_id':'A','component':'balance','balance':100})
    await a._on_component({'account_id':'A','component':'equity','equity':100})
    await a._on_component({'account_id':'A','component':'margin','margin':10})
    assert not [p for n,p in b.e if n==m.EVENT_OUT], "ما في نشر قبل أي نبضة SYS_SECOND"
    await a._on_pulse({'sequence':1,'pulse_id':'SYS_SECOND|1'})
    publishes=[p for n,p in b.e if n==m.EVENT_OUT]
    assert len(publishes)==1, "نشرة واحدة بالضبط متوقّعة بعد النبضة، الفعلي: %d"%len(publishes)
    row=[r for r in publishes[0]['accounts'] if r['account_id']=='A'][0]
    assert set(row['components']) >= {'balance','equity','margin'}, row['components']
    assert publishes[0]['sequence']==1 and publishes[0]['pulse_id']=='SYS_SECOND|1'
    print('OK — snapshot واحد لكل نبضة، لا لكل مكوّن')


async def test_no_republish_on_pulse_when_nothing_changed():
    # عقد المالك ٩٠-١١ نقطة ٤+٨: رسالة/عدة رسائل لنفس الحالة لا تولّد أحداثاً
    # إضافية؛ لا نخفض الأحداث بالقوة، فقط نمنع إعادة إنتاج الحدث نفسه --
    # نبضة تالية بلا أي تغيير حقيقي = صفر نشر إضافي.
    b=B();a=m.Atom()
    await a.initialize(m.AtomContext(650,{},L(),b.publish,b.subscribe))
    await a.start()
    await a._on_component({'account_id':'A','component':'balance','balance':100})
    await a._on_pulse({'sequence':1,'pulse_id':'SYS_SECOND|1'})
    assert len([p for n,p in b.e if n==m.EVENT_OUT])==1
    await a._on_pulse({'sequence':2,'pulse_id':'SYS_SECOND|2'})
    assert len([p for n,p in b.e if n==m.EVENT_OUT])==1, "بلا تغيّر حقيقي، النبضة الثانية يجب ألا تنشر"
    print('OK — لا إعادة إنتاج لنفس الحالة على نبضة بلا تغيير')


async def main():
    b=B();a=m.Atom();await a.initialize(m.AtomContext(650,{},L(),b.publish,b.subscribe));await a.start()
    await a._on_component({'account_id':'A','component':'equity','equity':100})
    await a._on_pulse({'sequence':1,'pulse_id':'SYS_SECOND|1'})
    p=[p for n,p in b.e if n==m.EVENT_OUT][-1];assert p['read_only'];print('650 manager tests passed')
    await test_no_publish_before_pulse_and_one_snapshot_per_pulse()
    await test_no_republish_on_pulse_when_nothing_changed()
if __name__=='__main__':asyncio.run(main())
