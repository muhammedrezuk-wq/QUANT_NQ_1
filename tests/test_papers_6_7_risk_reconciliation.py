from __future__ import annotations
import importlib.util,inspect,os,sqlite3,sys,tempfile,time
from pathlib import Path
import pytest
from core.contracts.atom import AtomContext
ROOT=Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

def load(i):
 d=next((ATOM_ROOT).glob(f'{i}_*'));name=f'paper67_{i}';sys.path.insert(0,str(d));spec=importlib.util.spec_from_file_location(name,d/'atom.py');m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
M500=load(500);M508=load(508);M516=load(516);M517=load(517);M518=load(518);M519=load(519);M520=load(520);M521=load(521);M523=load(523);M525=load(525);M551=load(551);M552=load(552);M576=load(576);M578=load(578);M703=load(703);M704=load(704);M707=load(707)
import clock
class Log:
 def __getattr__(self,n):return lambda *a,**k:None
class Bus:
 def __init__(self):self.events=[];self.handlers={}
 def subscribe(self,n,h):self.handlers.setdefault(n,[]).append(h)
 async def publish(self,n,p):
  self.events.append((n,p))
  for h in list(self.handlers.get(n,[])):
   r=h(p)
   if inspect.isawaitable(r):await r
 def rows(self,n):return [p for name,p in self.events if name==n]
async def start(m,i,cfg,b=None):
 b=b or Bus();a=m.Atom();await a.initialize(AtomContext(i,cfg,Log(),b.publish,b.subscribe));await a.start();return a,b
CFG516={"max_daily_loss_pct":5,"max_consecutive_losses":3,"max_daily_trades":20,"max_open_trades":5,"max_reserved_risk_pct":100}
async def ready516():
 td=tempfile.TemporaryDirectory();cfg=dict(CFG516,consumer_db_path=str(Path(td.name)/'consumer.db'))
 a,b=await start(M516,516,cfg);a._test_tmp=td;await b.publish("portfolio.equity.state",{"account_id":"A","broker":"BR","equity":1000});await b.publish(M516.EVENT_ACCOUNT,{"account_id":"A","broker":"BR","stale":False});await b.publish(M516.EVENT_TERMINAL,{"account_id":"A","connected":True,"trade_allowed":True,"expert_allowed":True});await b.publish(M516.EVENT_LEDGER,{"ledgers":[{"account_id":"A","broker":"BR","symbol":"NQ","R":100,"loss_exposure":0}]});return a,b
@pytest.mark.asyncio
async def test_516_rejects_missing_identity_instead_of_default():
 a,b=await ready516();await a._on_validate({"request_id":"x","symbol":"NQ","approved":True});out=b.rows(M516.EVENT_VALIDATED)[-1];assert out["approved"] is False and out["reason"]=="MISSING_ACCOUNT_ID";assert set(a._books)=={"A"}
@pytest.mark.asyncio
async def test_516_reservations_prevent_concurrent_budget_reuse():
 a,b=await ready516();base={"account_id":"A","broker":"BR","symbol":"NQ","action":"OPEN","approved":True,"side":"BUY"};await a._on_validate({**base,"request_id":"r1","risk_budget":60});await a._on_validate({**base,"request_id":"r2","risk_budget":60});rows=b.rows(M516.EVENT_VALIDATED);assert rows[-2]["approved"] is True;assert rows[-1]["reason"]=="RISK_BUDGET_EXCEEDED"
@pytest.mark.asyncio
async def test_incomplete_result_with_numeric_loss_moves_breaker_and_is_declared():
 # (nq seal 2026-08-25: 516 v5.1.0 reversed the old guard on purpose — the
 # completeness gate was fail-OPEN: any break in the cost chain meant no loss
 # ever tripped the breaker. Now a loss that ARRIVED as a number counts even
 # with incomplete costs (declared via incomplete_costs + audit reason
 # TRADE_RESULT_COSTS_INCOMPLETE); only loss_pct=None is ignored, loudly.)
 a,b=await ready516()
 before=dict(a.book("A"))
 await a._on_loss({"event_id":"result:no-number","account_id":"A","broker":"BR","completeness":"INCOMPLETE","loss_pct":None,"is_loss":True})
 assert a.book("A")==before and a._incomplete_ignored==1
 assert b.rows(M516.EVENT_STATE)[-1]["reason"]=="LOSS_UNKNOWN_IGNORED"
 await a._on_loss({"event_id":"result:incomplete","account_id":"A","broker":"BR","completeness":"INCOMPLETE","loss_pct":99,"is_loss":True})
 book=a.book("A")
 assert book["daily_loss_pct"]==99 and book["consecutive_losses"]==1 and book["daily_trade_count"]==1
 assert book["incomplete_costs"]==1
 assert book["kill"] is True and book["reason"]=="RISK_DAILY_LIMIT"
 assert b.rows(M516.EVENT_AUDIT)[-1]["reason"]=="TRADE_RESULT_COSTS_INCOMPLETE"
@pytest.mark.asyncio
async def test_hard_stop_is_account_scoped_and_not_time_released():
 a,b=await ready516();await b.publish("portfolio.equity.state",{"account_id":"B","broker":"BR2","equity":1000});await b.publish(M516.EVENT_ACCOUNT,{"account_id":"B","broker":"BR2"});await b.publish(M516.EVENT_TERMINAL,{"account_id":"B","connected":True,"trade_allowed":True,"expert_allowed":True});await a._on_loss({"event_id":"result:hard-stop","account_id":"A","broker":"BR","completeness":"COMPLETE","loss_pct":6,"is_loss":True});assert a.book("A")["kill"] and not a.book("B")["kill"];await a._on_day({"pulse_id":"SYS_DAY|1","bucket_start":1,"official_time":1});assert a.book("A")["kill"];await a._on_reset({"account_id":"A"});assert not a.book("A")["kill"]
@pytest.mark.asyncio
async def test_517_explicit_completeness_and_snapshot():
 with tempfile.TemporaryDirectory() as td:
  cfg={"consumer_db_path":str(Path(td)/'consumer.db'),"cost_wait_timeout_s":1};a,b=await start(M517,517,cfg);await a._on_truth_equity({"account_id":"A","broker":"BR","equity":10000});await a._on_account({"account_id":"A","broker":"BR"});await a._on_outcome({"event_id":"outcome:incomplete","trade_id":"trade:1","account_id":"A","broker":"BR","symbol":"NQ","profit":-100,"commission":None,"swap":0,"fee":0});assert not b.rows(M517.EVENT_OUT) and a._pending;await a._on_outcome({"event_id":"outcome:complete","trade_id":"trade:1","account_id":"A","broker":"BR","symbol":"NQ","profit":-100,"commission":-2,"swap":-1,"fee":0});row=b.rows(M517.EVENT_OUT)[-1];assert row["completeness"]=="COMPLETE" and row["pnl"]==-103;state=await a.snapshot();c,_=await start(M517,517,cfg);await c.restore(state);assert c._truth.get("A","equity")==10000;await a.stop();await c.stop()
@pytest.mark.asyncio
async def test_500_is_read_only_516_aggregator():
 a,b=await start(M500,500,{});await b.publish(M500.EVENT_RISK,{"account_id":"A","status":"HALTED"});row=b.rows(M500.EVENT_OUT)[-1];assert row["authority"]=="516" and row["accounts"]["A"]["risk"]["status"]=="HALTED";assert not hasattr(a,"_kill")
@pytest.mark.asyncio
async def test_520_snapshot_restores_actual_and_empty_account_gate():
 with tempfile.TemporaryDirectory() as td:
  cfg={"state_path":str(Path(td)/'desired.json')};a,b=await start(M520,520,cfg);await a._on_account({"account_id":"A","broker":"BR"});await a._on_actual({"account_id":"A","broker":"BR","source":"broker","positions":[],"complete":True,"timestamp":1});assert b.rows(M520.EVENT_OUT)[-1]["status"]=="MATCH_EMPTY_ACCOUNT";snap=await a.snapshot();c,_=await start(M520,520,cfg);await c.restore(snap);assert ("A","BR") in c._account_actual_seen
@pytest.mark.asyncio
async def test_520_detects_mismatch_with_scoped_identity():
 with tempfile.TemporaryDirectory() as td:
  a,_=await start(M520,520,{"state_path":str(Path(td)/'x.json')});await a._on_account({"account_id":"A","broker":"BR"});await a._on_desired({"account_id":"A","broker":"BR","symbol":"NQ","version":1,"legs":[{"ticket":7,"volume":1}]});await a._on_actual({"account_id":"A","broker":"BR","source":"broker","timestamp":2,"positions":[{"account_id":"A","broker":"BR","symbol":"NQ","ticket":7,"volume":2}]});assert a.state(M520.scope("A","NQ","BR"))["status"]=="ATTENTION"
@pytest.mark.asyncio
async def test_552_requires_reconciliation_and_reference_but_management_passes():
 clock.reset_for_tests();clock.configure(max_accepted_offset_s=5,max_sample_age_s=30,stale_after_s=900,max_slew_per_second=.05);clock.accept_sample({"median_offset_s":.1,"measured_at":time.time(),"quorum":True},writer="003")
 a,b=await start(M552,552,{"enabled":True,"max_spread_points":0,"spread_ttl_s":5});await a._on_account({"account_id":"A","broker":"BR"});await a._on_whitelist({"allowed_by_account":{"A":["NQ"]}});order={"magic":20260801,"account_id":"A","broker":"BR","request_id":"r","decision_id":"d1","action":"OPEN","symbol":"NQ","side":"BUY","volume":1,"reference_price":100,"stop_loss":99,"take_profit":102};await a._on_margin_verdict({"account_id":"A","request_id":"r","approved":True,"reason":"OK"});await a._on_built(order);assert b.rows(M552.EVENT_REJECTED)[-1]["reason"]=="RECONCILIATION_NOT_MATCHED";await a._on_reconcile({"account_id":"A","broker":"BR","symbol":"*","status":"MATCH_EMPTY_ACCOUNT"});await a._on_reference({"symbol":"NQ","state":"HEALTHY"});await a._on_exposure({"account_id":"A","broker":"BR","usable_for_new_exposure":True});await a._on_built(order);assert b.rows(M552.EVENT_FINAL)[-1]["request_id"]=="r";management={**order,"request_id":"m","action":"MODIFY_SL","ticket":7};a._reference["NQ"]="STALE";await a._on_built(management);assert b.rows(M552.EVENT_FINAL)[-1]["request_id"]=="m"
@pytest.mark.asyncio
async def test_552_halt_A_does_not_halt_B():
 a,b=await start(M552,552,{"enabled":True,"max_spread_points":0});await a._on_halt({"account_id":"A","reason":"RISK"});assert "A" in a._halted_accounts and "B" not in a._halted_accounts;await a._on_reset({"account_id":"A"});assert not a._halted_accounts
@pytest.mark.asyncio
async def test_521_reference_state_has_operational_consumer():
 manifest=(next((ATOM_ROOT).glob('552_*'))/'manifest.yaml').read_text();assert 'reference.health.state' in manifest
@pytest.mark.asyncio
async def test_525_vpu_is_account_broker_scoped():
 a,b=await start(M525,525,{"min_abs_v_net":1e-9});await a._on_account({"account_id":"A","broker":"B1"});await a._on_account({"account_id":"B","broker":"B2"});await a._on_specs({"symbols":[{"account_id":"A","symbol":"NQ","tick_value":10,"tick_size":1},{"account_id":"B","symbol":"NQ","tick_value":1,"tick_size":1}]});await a._on_ledger({"ledgers":[{"account_id":"A","broker":"B1","symbol":"NQ","budgeted":True,"budget":50,"v_net":1,"w":100},{"account_id":"B","broker":"B2","symbol":"NQ","budgeted":True,"budget":50,"v_net":1,"w":100}]});st=b.rows(M525.EVENT_OUT)[-1]["stops"];assert st[0]["vpu"]==10 and st[1]["vpu"]==1
@pytest.mark.asyncio
async def test_523_snapshot_persists_dials():
 a,_=await start(M523,523,{});await a._on_account({"account_id":"A","broker":"BR"});await a._on_command({"account_id":"A","broker":"BR","symbol":"NQ","dial":30});snap=await a.snapshot();c,_=await start(M523,523,{});await c.restore(snap);assert c._dials==a._dials

# ويندوز يرفض حذف ملفّ مفتوح (WinError 32) — بخلاف لينكس. فكلّ واصف ملفّ وكلّ
# اتّصال sqlite يُفتح هنا يجب أن يُغلق صراحةً، وإلّا سقط الاختبار على كود سليم.
# سندها: قاعدة المالك «صلّح الكود غير المتوافق مع ويندوز، لا تلتفّ عليه بلينكس».
def dbpath():
 fd,path=tempfile.mkstemp(suffix='.db');os.close(fd);Path(path).unlink();return path
def q1(path,sql):
 c=sqlite3.connect(path)
 try:return c.execute(sql).fetchone()
 finally:c.close()
@pytest.mark.asyncio
async def test_703_deduplicates_account_request():
 path=dbpath();cfg={"db_path":path,"flush_size":1,"retention_days":0,"flush_interval_s":30,"max_rows":100,"max_db_bytes":1000000};a,_=await start(M703,703,cfg);p={"account_id":"A","request_id":"r","symbol":"NQ"};await a._on_order(p);await a._on_order(p);assert q1(path,'select count(*) from orders')[0]==1 and a.duplicate_count==1;await a.stop();Path(path).unlink(missing_ok=True)
@pytest.mark.asyncio
async def test_704_deduplicates_event_id():
 path=dbpath();cfg={"db_path":path,"watch_events":["x"],"flush_size":1,"retention_days":0,"flush_interval_s":30,"immediate_events":[],"max_rows":100,"max_db_bytes":1000000};a,_=await start(M704,704,cfg);await a._record('x',{"event_id":"e1"});await a._record('x',{"event_id":"e1"});assert q1(path,'select count(*) from timeline')[0]==1 and a.duplicate_count==1;await a.stop();Path(path).unlink(missing_ok=True)
@pytest.mark.asyncio
async def test_707_deduplicates_stage_request_but_keeps_distinct_stages():
 path=dbpath();cfg={"db_path":path,"keep_full_payload":False,"max_rows":100,"max_db_bytes":1000000};a,_=await start(M707,707,cfg);p={"account_id":"A","request_id":"r"};await a._store('APPROVED',p);await a._store('APPROVED',p);await a._store('DECISION_FINALIZED',p);assert q1(path,'select count(*) from decisions')[0]==2 and a.duplicate_count==1;await a.stop();Path(path).unlink(missing_ok=True)
