import asyncio,importlib.util,sys,tempfile,threading
from pathlib import Path
root=Path(__file__).resolve().parents[3];folder=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));s=importlib.util.spec_from_file_location('t563',folder/'atom.py');m=importlib.util.module_from_spec(s);sys.modules['t563']=m;s.loader.exec_module(m)
class L:
 def __getattr__(self,n):return lambda *a,**k:None
class B:
 def __init__(self):self.e=[]
 def subscribe(self,*a):pass
 async def publish(self,n,p):self.e.append((n,p))


async def test_durable_confirmation_flow():
    print("\n--- test_durable_confirmation_flow ---")
    with tempfile.TemporaryDirectory() as td:
        b=B();a=m.Atom();await a.initialize(m.AtomContext(563,{'dedupe_db_path':str(Path(td)/'j.db')},L(),b.publish,b.subscribe));await a.start();await a._on_account({'account_id':'A','broker':'BR'});await a._on_specs({'symbols':[{'account_id':'A','symbol':'NQ','point':.25,'tick_value':5,'tick_size':.25}]});await a._on_requested({'account_id':'A','broker':'BR','request_id':'r','symbol':'NQ','side':'BUY','reference_price':100});event={'account_id':'A','broker':'BR','request_id':'r','event_type':'OPENED','source_row_id':1,'symbol':'NQ','entry_price':101,'volume':1};await a._on_event(event);assert [p for n,p in b.e if n==m.EVENT_ACK][-1]['slippage_points']==4;await a._on_event(event);assert a._duplicates==1;await a._on_event({'account_id':'A','broker':'BR','event_type':'CLOSED','source_row_id':2,'symbol':'NQ','profit':10,'commission':0,'swap':0,'fee':0});assert [p for n,p in b.e if n==m.EVENT_OUT][-1]['profit']==10;await a._on_event({'account_id':'A','broker':'BR','event_type':'OPENED'});assert [p for n,p in b.e if n==m.EVENT_REJECTED][-1]['reason']=='MISSING_DURABLE_EVENT_ID_OR_SCOPE'
        # بند 22 حزمة ت (ت١): حدث الوسيط لا يحمل الهوية — تُستَرجَع من سجل الطلب
        # الدائم (قرار→طلب→أمر→نتيجة صامد على أي إقلاع)، والغائب None + إنذار.
        await a._on_requested({'account_id':'A','broker':'BR','request_id':'r2','symbol':'NQ','side':'BUY','reference_price':100,'decision_id':'D-5','gate_request_id':'G-5'})
        await a._on_event({'account_id':'A','broker':'BR','request_id':'r2','event_type':'OPENED','source_row_id':3,'symbol':'NQ','entry_price':100,'volume':1})
        ack=[p for n,p in b.e if n==m.EVENT_ACK][-1];assert ack['decision_id']=='D-5' and ack['gate_request_id']=='G-5' and 'identity_warnings' not in ack,ack
        await a._on_event({'account_id':'A','broker':'BR','request_id':'r2','event_type':'CLOSED','source_row_id':4,'symbol':'NQ','profit':-5,'commission':0,'swap':0,'fee':0})
        out=[p for n,p in b.e if n==m.EVENT_OUT][-1];assert out['decision_id']=='D-5' and out['gate_request_id']=='G-5' and 'identity_warnings' not in out,out
        # الحدث المغلق الأول (بلا request_id) خرج بهوية None ومُعلَنة — لا اختراع
        first_out=[p for n,p in b.e if n==m.EVENT_OUT][0]
        assert first_out['decision_id'] is None and first_out['identity_warnings']==['identity_incomplete'],first_out
    print('OK — تدفّق التأكيد الدائم كامل: زحلقة قياسة، تكرار مرفوض، هوية القرار مستَرجَعة أو مُعلَنة غياباً')


async def test_journal_ensure_runs_off_the_event_loop_thread():
    """Item 24/27 of the 27-atom review ("blocking I/O at boot"):
    initialize() called self._journal.ensure() (mkdir + open a raw
    sqlite connection + run schema + commit) directly on the event loop
    -- unlike every OTHER Journal call in this same atom, which already
    runs via asyncio.to_thread. Must now run off the loop thread too."""
    print("\n--- test_journal_ensure_runs_off_the_event_loop_thread ---")
    main_thread = threading.current_thread()
    seen = {}

    class RecordingJournal:
        def __init__(self, path): self.path = path
        def ensure(self): seen["thread"] = threading.current_thread()

    original = m.Journal
    m.Journal = RecordingJournal
    try:
        with tempfile.TemporaryDirectory() as td:
            b=B(); a=m.Atom()
            await a.initialize(m.AtomContext(563, {'dedupe_db_path': str(Path(td)/'j.db')},
                                             L(), b.publish, b.subscribe))
    finally:
        m.Journal = original
    assert seen.get("thread") is not None, "ensure() لم يُستدعَ إطلاقاً"
    assert seen["thread"] is not main_thread, (
        "ensure() نُفِّذ على خيط الحلقة الرئيسي عند الإقلاع -- سيجمّد كل الذرّات الأخرى")
    print("OK — ensure() بالإقلاع يعمل على خيط منفصل لا خيط الحلقة الرئيسي")


async def test_restore_rejects_corrupt_counter_without_tearing_state():
    """Item 24/27 ("restore() without guarding"): the top-level dict
    check was already fine, but the four counters were
    int(state.get(...) or 0) with no try/except -- a corrupted
    non-numeric value raised a raw, uncontrolled ValueError instead of
    the same clean INVALID_EXECUTION_CONFIRM_STATE the top-level check
    already uses, and a failure partway through left self torn (earlier
    counters already committed)."""
    print("\n--- test_restore_rejects_corrupt_counter_without_tearing_state ---")
    with tempfile.TemporaryDirectory() as td:
        b=B(); a=m.Atom()
        await a.initialize(m.AtomContext(563, {'dedupe_db_path': str(Path(td)/'j.db')},
                                         L(), b.publish, b.subscribe))
        a._seen = 3; a._opened = 2; a._realized = 1; a._duplicates = 0
        before = (a._seen, a._opened, a._realized, a._duplicates)
        bad_state = {"seen": 99, "opened": "not-a-number", "realized": 1, "duplicates": 0}
        threw = False
        try:
            await a.restore(bad_state)
        except ValueError as exc:
            threw = True
            assert "INVALID_EXECUTION_CONFIRM_STATE" in str(exc), exc
        assert threw, "restore() لم ينهر رغم عدّاد غير رقمي"
        assert (a._seen, a._opened, a._realized, a._duplicates) == before, (
            "حالة ممزَّقة: بعض العدّادات تغيّرت رغم فشل الاستعادة: %r (كان %r)"
            % ((a._seen, a._opened, a._realized, a._duplicates), before))
    print("OK — عدّاد فاسد يُطلق الخطأ النظيف نفسه، ولا يمزّق حالة الذرّة")


async def main():
    tests=[test_durable_confirmation_flow,
           test_journal_ensure_runs_off_the_event_loop_thread,
           test_restore_rejects_corrupt_counter_without_tearing_state]
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
