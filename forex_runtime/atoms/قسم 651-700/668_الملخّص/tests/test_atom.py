import asyncio,importlib.util,sys
from pathlib import Path
root=Path(__file__).resolve().parents[3];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));s=importlib.util.spec_from_file_location('a668',folder/'atom.py');m=importlib.util.module_from_spec(s);sys.modules['a668']=m;s.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
async def test_no_publish_before_pulse_and_one_snapshot_per_pulse():
    # عقد ٩٠-١١ (٢٠٢٦-٠٨-١٩): ٦٦٨ تجمع ٧ مصادر غير متزامنة (٥ منها بلا هوية
    # نبضة إطلاقاً) -- بنفس نمط ٦٥٠: تخزين فقط عند الوصول، نشر واحد بالنبضة.
    b=B();a=m.Atom()
    await a.initialize(m.AtomContext(668,{},L(),b.publish,b.subscribe))
    await a.start()
    await a._on_named('portfolio.components.state',{'accounts':[{'account_id':'A'}]})
    await a._on_named('portfolio.risk_distribution.state',{'assets':[]})
    assert not [p for n,p in b.e if n==m.EVENT_OUT], "لا نشر قبل أي نبضة"
    await a._on_pulse({'sequence':1,'pulse_id':'SYS_SECOND|1'})
    publishes=[p for n,p in b.e if n==m.EVENT_OUT]
    assert len(publishes)==1, "نشرة واحدة بالضبط متوقّعة بعد النبضة، الفعلي: %d"%len(publishes)
    assert publishes[0]['sequence']==1 and publishes[0]['pulse_id']=='SYS_SECOND|1'
    print('OK — snapshot واحد لكل نبضة عبر السبعة مصادر')


async def test_no_republish_when_nothing_changed():
    b=B();a=m.Atom()
    await a.initialize(m.AtomContext(668,{},L(),b.publish,b.subscribe))
    await a.start()
    await a._on_named('portfolio.components.state',{'accounts':[{'account_id':'A'}]})
    await a._on_pulse({'sequence':1,'pulse_id':'SYS_SECOND|1'})
    assert len([p for n,p in b.e if n==m.EVENT_OUT])==1
    await a._on_pulse({'sequence':2,'pulse_id':'SYS_SECOND|2'})
    assert len([p for n,p in b.e if n==m.EVENT_OUT])==1, "بلا تغيّر حقيقي، النبضة الثانية يجب ألا تنشر"
    print('OK — لا إعادة إنتاج لنفس الحالة رغم نبضة جديدة')


async def test_pulse_sequence_change_alone_does_not_force_republish():
    # حاسمة: sequence يتغيّر كل نبضة من مصدره الأصلي (650) نفسه -- يجب ألا
    # يُقرأ كتغيّر بالمحتوى بحد ذاته إذا كانت البيانات الفعلية نفسها.
    b=B();a=m.Atom()
    await a.initialize(m.AtomContext(668,{},L(),b.publish,b.subscribe))
    await a.start()
    await a._on_named('portfolio.components.state',
        {'accounts':[{'account_id':'A'}],'sequence':1,'pulse_id':'SYS_SECOND|1'})
    await a._on_pulse({'sequence':1,'pulse_id':'SYS_SECOND|1'})
    assert len([p for n,p in b.e if n==m.EVENT_OUT])==1
    # نفس المحتوى بالضبط يصل تاني بس بـsequence مختلف من 650 نفسها (لأنها
    # نشرت مجدداً لسبب آخر) -- 668 يجب ألا تعتبر هذا تغييراً.
    await a._on_named('portfolio.components.state',
        {'accounts':[{'account_id':'A'}],'sequence':2,'pulse_id':'SYS_SECOND|2'})
    await a._on_pulse({'sequence':3,'pulse_id':'SYS_SECOND|3'})
    assert len([p for n,p in b.e if n==m.EVENT_OUT])==1, (
        "sequence وحدها تغيّرت بمصدر فرعي، المحتوى الفعلي نفسه -- يجب ألا تُعتبر تغييراً")
    print('OK — sequence القادمة من مصدر فرعي لا تُقرأ كتغيّر محتوى')


async def main():
    b=B();a=m.Atom();await a.initialize(m.AtomContext(668,{},L(),b.publish,b.subscribe));await a.start()
    await a._on_named('portfolio.components.state',{'x':1})
    await a._on_pulse({'sequence':1,'pulse_id':'SYS_SECOND|1'})
    assert [p for n,p in b.e if n==m.EVENT_OUT][-1]['read_only'];print('668 overview tests passed')
    await test_no_publish_before_pulse_and_one_snapshot_per_pulse()
    await test_no_republish_when_nothing_changed()
    await test_pulse_sequence_change_alone_does_not_force_republish()
if __name__=='__main__':asyncio.run(main())
