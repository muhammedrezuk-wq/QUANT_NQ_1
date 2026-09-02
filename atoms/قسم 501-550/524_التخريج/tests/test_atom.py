import asyncio
import importlib.util
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
folder = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("_atom524_final", folder / "atom.py")
mod = importlib.util.module_from_spec(spec); sys.modules["_atom524_final"] = mod; spec.loader.exec_module(mod)

class Logger:
    def debug(self,*a,**k): pass
    def info(self,*a,**k): pass
    def warning(self,*a,**k): pass
    def error(self,*a,**k): pass
    def critical(self,*a,**k): pass

class Bus:
    def __init__(self): self.events=[]
    def subscribe(self,*a): pass
    async def publish(self,name,payload): self.events.append((name,payload))
    def context(self,cfg=None):
        return mod.AtomContext(524,cfg or {"milestone_mult":2,"extract_fraction":.5,
            "full_targets":{},"default_full_target":0},Logger(),self.publish,self.subscribe)

def ledger(gross, R=50, floating=0, account="A", symbol="X"):
    return {"account_id":account,"symbol":symbol,"R":R,"budget":R,"budgeted":True,
            "realized_gross":gross,"K":gross,"X":0,"floating_economic":floating}

def events(bus,name): return [p for n,p in bus.events if n==name]


async def test_milestones_and_confirmation_flow():
    print("\n--- test_milestones_and_confirmation_flow ---")
    b=Bus(); a=mod.Atom(); await a.initialize(b.context()); await a.start()
    for gross in (50,100,200): await a._on_ledger({"ledgers":[ledger(gross)]})
    assert [p["amount"] for p in events(b,mod.EVENT_PARTIAL)] == [25,50,100]
    await a._on_ledger({"ledgers":[ledger(200)]})
    assert len(events(b,mod.EVENT_PARTIAL)) == 3
    print("OK — R/2R/4R: 25 ثم 50 ثم 100، وكل مرحلة مرة واحدة")

    b2=Bus(); a2=mod.Atom(); await a2.initialize(b2.context()); await a2.start()
    await a2._on_ledger({"ledgers":[ledger(400)]})
    req=events(b2,mod.EVENT_PARTIAL)
    assert [p["amount"] for p in req] == [25,50,100,200]
    for p in req: await a2._on_confirm({"extraction_id":p["extraction_id"],"actual_amount":p["amount"]})
    extracted=events(b2,mod.EVENT_EXTRACTED)
    assert sum(p["amount"] for p in extracted)==375
    assert not a2._pending
    print("OK — قفزة إلى 400 تُخرج 25+50+100+200 = 375 بعد تأكيد فعلي")

    b3=Bus(); a3=mod.Atom(); await a3.initialize(b3.context()); await a3.start()
    await a3._on_ledger({"ledgers":[ledger(0,floating=999)]})
    assert not events(b3,mod.EVENT_PARTIAL)
    print("OK — الربح العائم وحده لا يطلق التخريج")

    b4=Bus(); a4=mod.Atom(); await a4.initialize(b4.context()); await a4.start()
    await a4._on_ledger({"ledgers":[ledger(300)]})
    req=events(b4,mod.EVENT_PARTIAL); assert [p["amount"] for p in req]==[25,50,100]
    await a4._on_ledger({"ledgers":[ledger(350)]})
    assert len(events(b4,mod.EVENT_PARTIAL))==3
    print("OK — التكرار/التراجع لا يعيدان مرحلة صُدرت")

    b5=Bus(); a5=mod.Atom(); await a5.initialize(b5.context({"milestone_mult":2,"extract_fraction":.25,"full_targets":{"A|X":300},"default_full_target":0})); await a5.start()
    await a5._on_ledger({"ledgers":[ledger(300)]})
    assert [p["amount"] for p in events(b5,mod.EVENT_PARTIAL)]==[12.5,25,50]
    assert len(events(b5,mod.EVENT_FULL))==1
    print("OK — مقابض R والتخريج الكامل الاختياري")

    b6=Bus(); a6=mod.Atom(); await a6.initialize(b6.context()); assert (await a6.health_check()).state==mod.HealthState.UNHEALTHY; await a6.start(); assert (await a6.health_check()).state==mod.HealthState.DEGRADED; await a6._on_ledger({"ledgers":[ledger(1)]}); assert (await a6.health_check()).state==mod.HealthState.HEALTHY
    print("OK — الصحة UNHEALTHY→DEGRADED→HEALTHY")


async def test_restore_raises_on_corrupt_non_dict_state():
    """Item 16/27 of the 27-atom review ("silent restore on data
    corruption"): restore() used to silently `return` on a non-dict
    state -- self quietly stayed at its empty __init__ defaults, no
    error, no log. Since self._issued/_full_issued is what stops a
    milestone from being extracted TWICE, silently losing it on a
    corrupt snapshot risks re-issuing real extraction requests for
    milestones already issued or confirmed. Must now raise, loudly."""
    print("\n--- test_restore_raises_on_corrupt_non_dict_state ---")
    b=Bus(); a=mod.Atom(); await a.initialize(b.context()); await a.start()
    await a._on_ledger({"ledgers":[ledger(200)]})
    before_issued=dict(a._issued)
    threw=False
    try:
        await a.restore("not-a-dict-state")
    except ValueError:
        threw=True
    assert threw, "restore() لم ينهر رغم حالة ليست قاموساً -- استعادة صامتة لا تزال قائمة"
    assert a._issued==before_issued, "الحالة تغيّرت رغم انهيار restore() -- ليست صامتة فقط بل ممزَّقة أيضاً"
    print("OK — restore() على حالة ليست قاموساً ينهر بوضوح، لا يصمت ولا يفرّغ الحالة")


async def test_restore_leaves_state_untouched_on_partial_failure():
    """Item 16/27: restore() mutated self._issued/_confirmed/_pending/...
    through a sequence of field parses -- a bad value in a LATER field
    (e.g. a non-numeric "failures" count) raised AFTER earlier fields
    (issued/confirmed/pending) had already been committed to self,
    leaving a torn mix of old and new milestone-issuance bookkeeping.
    Every field is now built into a local first; a failure must leave
    self byte-for-byte as it was before restore() was called."""
    print("\n--- test_restore_leaves_state_untouched_on_partial_failure ---")
    b=Bus(); a=mod.Atom(); await a.initialize(b.context()); await a.start()
    await a._on_ledger({"ledgers":[ledger(100)]})
    before_issued=dict(a._issued); before_pending=dict(a._pending)
    before_full_issued=set(a._full_issued); before_failures=a._failures
    bad_state={"issued":{"A|X":[0,1,2]}, "confirmed":{}, "pending":[],
               "full_issued":[], "failure_ids":[], "failures":"not-a-number"}
    threw=False
    try:
        await a.restore(bad_state)
    except (TypeError, ValueError):
        threw=True
    assert threw, "restore() لم ينهر رغم failures غير رقمي -- الاختبار لا يثبت شيئاً بلا هذا"
    assert a._issued==before_issued, ("حالة ممزَّقة: _issued تغيّر (%r) رغم فشل الاستعادة (كان %r)"
                                      % (a._issued, before_issued))
    assert a._pending==before_pending, "حالة ممزَّقة: _pending تغيّر رغم فشل الاستعادة"
    assert a._full_issued==before_full_issued, "حالة ممزَّقة: _full_issued تغيّر رغم فشل الاستعادة"
    assert a._failures==before_failures, "حالة ممزَّقة: _failures تغيّر رغم فشل الاستعادة"
    print("OK — فشل جزئي بالاستعادة لا يمزّق دفاتر إصدار المراحل -- كلّه يبقى أو يتغيّر معاً")


async def main():
    tests=[test_milestones_and_confirmation_flow,
           test_restore_raises_on_corrupt_non_dict_state,
           test_restore_leaves_state_untouched_on_partial_failure]
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

if __name__ == "__main__": asyncio.run(main())
