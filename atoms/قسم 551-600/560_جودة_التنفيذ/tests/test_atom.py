import asyncio,importlib.util,sys
from pathlib import Path
root=Path(__file__).resolve().parents[3];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));s=importlib.util.spec_from_file_location('t560',folder/'atom.py');m=importlib.util.module_from_spec(s);sys.modules['t560']=m;s.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))


async def _new():
    b=B();a=m.Atom();await a.initialize(m.AtomContext(560,{'max_adverse_points':50,'max_reject_rate':.25,'min_samples':1},L(),b.publish,b.subscribe));await a.start()
    return a,b


async def test_scoped_point_and_unmeasurable():
    print("\n--- test_scoped_point_and_unmeasurable ---")
    a,b=await _new()
    await a._on_account({'account_id':'A','broker':'BR'});await a._on_specs({'symbols':[{'account_id':'A','symbol':'BTCUSD','point':1}]})
    await a._on_request({'account_id':'A','broker':'BR','symbol':'BTCUSD','request_id':'r','side':'BUY','reference_price':100});await a._on_trade({'event_type':'OPENED','request_id':'r','entry_price':102})
    out=[p for n,p in b.e if n==m.EVENT_OUT][-1];assert out['adverse_max_points']==2 and out['broker']=='BR'
    await a._on_request({'account_id':'A','broker':'BR','symbol':'X','request_id':'x','side':'BUY','reference_price':100});await a._on_trade({'event_type':'OPENED','request_id':'x','entry_price':101})
    assert [p for n,p in b.e if n==m.EVENT_OUT][-1]['unmeasurable']==1
    print('OK — نقطة قياس بنطاق حسابه الصحيح، ورمز بلا مواصفة يُعلَن غير قابل للقياس')


async def test_restore_raises_on_non_dict_top_level_state():
    """Item 23/27 of the 27-atom review ("restore() crashes if the stored
    value is not a dict"): the top-level guard was already present
    (isinstance check -> ValueError) but had zero test coverage. Locks
    the guarantee in."""
    print("\n--- test_restore_raises_on_non_dict_top_level_state ---")
    a,_=await _new()
    threw=False
    try:
        await a.restore("not-a-dict")
    except ValueError:
        threw=True
    assert threw, "restore() لم ينهر رغم حالة ليست قاموساً أصلاً"
    print("OK — حالة ليست قاموساً على الإطلاق تُطلق ValueError كما هو متوقّع")


async def test_restore_ignores_corrupt_nested_field_without_crashing():
    """Item 23/27: the top-level state WAS a dict, but its nested fields
    (brokers/requests/stats) were never type-checked before .items() --
    a corrupted nested field (e.g. a list instead of a dict) raised a raw
    AttributeError instead of degrading gracefully like every other
    field in this atom's own restore() already did. Also proves a valid
    sibling field still restores correctly -- one corrupt field must not
    drop the rest."""
    print("\n--- test_restore_ignores_corrupt_nested_field_without_crashing ---")
    a,_=await _new()
    bad_state={"version": m.ATOM_VERSION, "brokers": ["not", "a", "dict"],
               "requests": {"r1": {"account_id": "A", "broker": "BR", "symbol": "X",
                                   "side": "BUY", "reference_price": 100, "point": 1}},
               "stats": {}}
    await a.restore(bad_state)  # must not raise
    assert a._brokers == {}, ("حقل fاسد (brokers=قائمة) يجب أن يتساهل لقاموس فارغ لا أن ينهار: %r"
                              % a._brokers)
    assert "r1" in a._requests, ("حقل سليم مجاور (requests) يجب أن يُستعاد رغم فساد brokers: %r"
                                 % a._requests)
    print("OK — حقل متداخل فاسد لا يُسقط الاستعادة كلّها، والحقل السليم المجاور يُستعاد")


async def main():
    tests=[test_scoped_point_and_unmeasurable,
           test_restore_raises_on_non_dict_top_level_state,
           test_restore_ignores_corrupt_nested_field_without_crashing]
    failed=[]
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            failed.append((t.__name__,str(e))); print(f"FAILED: {t.__name__}: {e}")
        except Exception as e:
            failed.append((t.__name__,repr(e))); print(f"ERROR: {t.__name__}: {e!r}")
    print("\n"+"="*60)
    if failed:
        print(f"فشل {len(failed)} من أصل {len(tests)}"); sys.exit(1)
    print(f"نجح كل الاختبارات ({len(tests)}/{len(tests)})")
if __name__=='__main__':asyncio.run(main())
