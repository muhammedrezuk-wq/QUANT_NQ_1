import asyncio
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[3]; folder=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root)); sys.path.insert(0,str(folder))
spec=importlib.util.spec_from_file_location("_atom520_work",folder/"atom.py"); mod=importlib.util.module_from_spec(spec); sys.modules["_atom520_work"]=mod; spec.loader.exec_module(mod)
class Log:
    def debug(self,*a,**k): pass
    def info(self,*a,**k): pass
    def warning(self,*a,**k): pass
    def error(self,*a,**k): pass
    def critical(self,*a,**k): pass
class Bus:
    def __init__(self): self.events=[]
    def subscribe(self,*a): pass
    async def publish(self,n,p): self.events.append((n,p))
    def ctx(self,p): return mod.AtomContext(520,{"state_path":p},Log(),self.publish,self.subscribe)


async def test_p0_desired_actual_ack_reconciliation_flow():
    print("\n--- test_p0_desired_actual_ack_reconciliation_flow ---")
    with tempfile.TemporaryDirectory() as t:
        p=str(Path(t)/"desired.json"); b=Bus(); a=mod.Atom(); await a.initialize(b.ctx(p)); await a.start()
        await a._on_desired({"account_id":"A","broker":"BR","symbol":"NQ","version":1,"timestamp":1,"legs":[{"ticket":"7","side":"BUY","volume":1,"stop_loss":99}]})
        assert json.loads(Path(p).read_text())["desired"]
        await a._on_actual({"source":"broker","timestamp":2,"positions":[{"account_id":"A","broker":"BR","symbol":"NQ","ticket":"7","side":"BUY","volume":2,"stop_loss":99}]})
        assert a.state(mod.scope("A","NQ","BR"))["classification_counts"]["MISMATCH"]==1
        # v3.2.0: a pending leg (no ticket) is intent, not loss -- gate stays open
        await a._on_desired({"account_id":"A","broker":"BR","symbol":"GC","version":1,"timestamp":3,"legs":[{"leg_id":"L1","request_id":"L1","side":"BUY","volume":1}]})
        await a._on_actual({"source":"broker","timestamp":4,"account_id":"A","broker":"BR","positions":[]})
        st=a.state(mod.scope("A","GC","BR"))
        assert st["status"]=="MATCH" and st["classification_counts"]["PENDING_OPEN"]==1 and st["warnings"]==["PENDING_OPEN_LEGS"]
        # the ack binds the broker ticket onto the desired leg
        await a._on_ack({"command_id":"L1","ticket":"55","account_id":"A","broker":"BR","symbol":"GC"})
        await a._on_actual({"source":"broker","timestamp":5,"account_id":"A","broker":"BR","positions":[{"account_id":"A","broker":"BR","symbol":"GC","ticket":"55","side":"BUY","volume":1}]})
        st=a.state(mod.scope("A","GC","BR"))
        assert st["status"]=="MATCH" and st["classification_counts"]["MATCH"]==1
        # a TICKETED leg vanishing at the broker still alarms
        await a._on_actual({"source":"broker","timestamp":6,"account_id":"A","broker":"BR","positions":[]})
        st=a.state(mod.scope("A","GC","BR"))
        assert st["status"]=="ATTENTION" and st["classification_counts"]["MISSING_AT_BROKER"]==1
        # binding survives persistence
        assert any(x.get("ticket")=="55" for rec in json.loads(Path(p).read_text())["desired"] for x in rec["legs"])
    print("OK — تسلسل desired/actual/ack كامل يطابق ويثبت التذكرة عبر الحفظ")


async def test_restore_leaves_state_untouched_on_partial_failure():
    """Item 15/27 of the 27-atom review ("torn state on restore
    failure"): restore() used to mutate self._desired/_actual/_stamps/...
    through a sequence of field parses -- a bad value partway through
    (e.g. a non-numeric stamp) raised AFTER earlier fields had already
    been committed to self, leaving a torn mix of old and new state.
    Every field is now built into a local first; self is only touched
    once ALL of them parse. A failure must leave self byte-for-byte as
    it was before restore() was called."""
    print("\n--- test_restore_leaves_state_untouched_on_partial_failure ---")
    with tempfile.TemporaryDirectory() as t:
        p=str(Path(t)/"desired.json"); b=Bus(); a=mod.Atom(); await a.initialize(b.ctx(p)); await a.start()
        await a._on_desired({"account_id":"A","broker":"BR","symbol":"NQ","version":1,"timestamp":1,
                              "legs":[{"ticket":"7","side":"BUY","volume":1}]})
        before_desired=dict(a._desired); before_actual=dict(a._actual)
        before_stamps=dict(a._stamps); before_brokers=dict(a._brokers)
        bad_state={"desired":[{"account_id":"A","broker":"BR","symbol":"XAU","version":1,
                               "legs":[{"ticket":"9","side":"SELL","volume":1}]}],
                   "actual":[], "actual_seen":[], "account_actual_seen":[],
                   "stamps":{"any-key":"not-a-number"},
                   "acks":{}, "brokers":{}}
        threw=False
        try:
            await a.restore(bad_state)
        except (TypeError, ValueError):
            threw=True
        assert threw, "restore() لم ينهر رغم stamp غير رقمي -- الاختبار لا يثبت شيئًا بلا هذا"
        assert a._desired==before_desired, ("حالة ممزَّقة: _desired تغيّر رغم فشل الاستعادة: %r vs %r"
                                            % (a._desired, before_desired))
        assert a._actual==before_actual, "حالة ممزَّقة: _actual تغيّر رغم فشل الاستعادة"
        assert a._stamps==before_stamps, "حالة ممزَّقة: _stamps تغيّر رغم فشل الاستعادة"
        assert a._brokers==before_brokers, "حالة ممزَّقة: _brokers تغيّر رغم فشل الاستعادة"
    print("OK — فشل جزئي بالاستعادة لا يمزّق الحالة -- كل شيء يبقى كما كان أو يتغيّر كله معًا")


async def test_desired_batch_with_leg_shaped_items_not_dropped():
    """Item 15/27 ("3 payload shapes"): desired_records' old heuristic
    treated an explicit "desired" list as a batch ONLY if its items
    carried a "legs"/"positions" key -- a batch of plain leg-shaped dicts
    fell back to [payload] (which has neither key), silently dropping
    every leg with zero error. An explicit "desired" list is now always
    a batch, regardless of item shape."""
    print("\n--- test_desired_batch_with_leg_shaped_items_not_dropped ---")
    payload={"desired":[{"account_id":"A","broker":"BR","symbol":"NQ",
                         "ticket":"7","leg_id":"L1","side":"BUY","volume":1}]}
    records=mod.desired_records(payload)
    assert records and records[0]["legs"], ("الدفعة فُقدت بصمت: %r" % records)
    assert records[0]["legs"][0]["ticket"]=="7", records
    print("OK — دفعة desired من قواميس شبيهة بالساق لا تُفقَد بصمت")


async def main():
    tests=[test_p0_desired_actual_ack_reconciliation_flow,
           test_restore_leaves_state_untouched_on_partial_failure,
           test_desired_batch_with_leg_shaped_items_not_dropped]
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
if __name__=="__main__": asyncio.run(main())
