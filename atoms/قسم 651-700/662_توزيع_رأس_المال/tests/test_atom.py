import asyncio,importlib.util,sys
from pathlib import Path
root=Path(__file__).resolve().parents[3];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));s=importlib.util.spec_from_file_location('a662',folder/'atom.py');m=importlib.util.module_from_spec(s);sys.modules['a662']=m;s.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))
async def test_no_republish_when_allocations_unchanged():
    # عقد ٩٠-١١ نقطة ٦ (٢٠٢٦-٠٨-١٩): ٦٦٢ يجب أن تطبّق dedupe مستقلة بذاتها،
    # لا تعتمد فقط على انضباط ٦٥٠ -- نفس محتوى allocations يصل مرتين = نشرة واحدة.
    b=B();a=m.Atom()
    await a.initialize(m.AtomContext(662,{'allocation_pct':1},L(),b.publish,b.subscribe))
    await a.start()
    payload={'accounts':[{'account_id':'A','equity':100}],'sequence':1,'pulse_id':'SYS_SECOND|1'}
    await a._on(dict(payload))
    await a._on(dict(payload))
    publishes=[p for n,p in b.e if n==m.EVENT_OUT]
    assert len(publishes)==1, "نفس المحتوى وصل مرتين، يجب نشرة واحدة فقط، الفعلي: %d"%len(publishes)
    print('OK — لا إعادة نشر لنفس التوزيع')


async def test_republishes_when_allocations_actually_change():
    b=B();a=m.Atom()
    await a.initialize(m.AtomContext(662,{'allocation_pct':1},L(),b.publish,b.subscribe))
    await a.start()
    await a._on({'accounts':[{'account_id':'A','equity':100}],'sequence':1,'pulse_id':'SYS_SECOND|1'})
    await a._on({'accounts':[{'account_id':'A','equity':200}],'sequence':2,'pulse_id':'SYS_SECOND|2'})
    publishes=[p for n,p in b.e if n==m.EVENT_OUT]
    assert len(publishes)==2, "تغيّر حقيقي بالمحتوى يجب أن يُنشر، الفعلي: %d"%len(publishes)
    print('OK — تغيّر حقيقي يُنشر دائماً')


async def test_forwards_sequence_and_pulse_id_from_source():
    # لا epoch مخترَع -- ننقل sequence/pulse_id الواصلين من 650 كما هما.
    b=B();a=m.Atom()
    await a.initialize(m.AtomContext(662,{'allocation_pct':1},L(),b.publish,b.subscribe))
    await a.start()
    await a._on({'accounts':[{'account_id':'A','equity':100}],'sequence':7,'pulse_id':'SYS_SECOND|7'})
    p=[p for n,p in b.e if n==m.EVENT_OUT][-1]
    assert p['sequence']==7 and p['pulse_id']=='SYS_SECOND|7', p
    print('OK — sequence/pulse_id منقولان من المصدر بلا اختراع')


async def main():
    b=B();a=m.Atom();await a.initialize(m.AtomContext(662,{'allocation_pct':1},L(),b.publish,b.subscribe));await a.start();await a._on({'accounts':[{'account_id':'A','equity':100}]});assert [p for n,p in b.e if n==m.EVENT_OUT][-1]['allocations'][0]['allocated_capital']==100;print('662 capital tests passed')
    await test_no_republish_when_allocations_unchanged()
    await test_republishes_when_allocations_actually_change()
    await test_forwards_sequence_and_pulse_id_from_source()
if __name__=='__main__':asyncio.run(main())
