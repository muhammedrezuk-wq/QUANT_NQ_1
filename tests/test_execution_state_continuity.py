from __future__ import annotations
import asyncio,importlib.util,inspect,sqlite3,sys,tempfile
from pathlib import Path
import pytest
from core.contracts.atom import AtomContext
from shared.durable_execution_journal import Journal
ROOT=Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)

def load(i):
 d=next((ATOM_ROOT).glob(f'{i}_*'));sys.path.insert(0,str(d));name=f'p8_{i}';s=importlib.util.spec_from_file_location(name,d/'atom.py');m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
M516=load(516);M517=load(517);M518=load(518);M550=load(550);M560=load(560);M563=load(563);M570=load(570);M578=load(578);M583=load(583);M586=load(586);M707=load(707)
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
async def start(m,i,cfg=None,b=None):
 b=b or Bus();a=m.Atom();await a.initialize(AtomContext(i,cfg or {},Log(),b.publish,b.subscribe));await a.start();return a,b
def order(account='A',broker='BR',rid='r'):
 return {'account_id':account,'broker':broker,'request_id':rid,'symbol':'NQ','side':'BUY','action':'OPEN','volume':1,'reference_price':100,'stop_loss':99,'take_profit':102}
@pytest.mark.asyncio
async def test_01_final_is_not_sent():
 a,b=await start(M550,550);await a._on_final(order());row=b.rows(M550.EVENT_OUT)[-1];assert row['orders']['A\x1fr']['stage']=='DECISION_FINALIZED';assert 'sent' not in row['counts']
@pytest.mark.asyncio
async def test_02_bridge_write_is_queued():
 a,b=await start(M550,550);await a._on_bridge_written(order());assert b.rows(M550.EVENT_OUT)[-1]['orders']['A\x1fr']['stage']=='QUEUED_TO_BRIDGE'
@pytest.mark.asyncio
async def test_03_ack_is_broker_acknowledged():
 a,b=await start(M550,550);await a._on_ack(order());assert b.rows(M550.EVENT_OUT)[-1]['counts']['broker_acknowledged']==1
@pytest.mark.asyncio
async def test_04_trade_event_is_fill():
 a,b=await start(M550,550);await a._on_trade({**order(),'event_type':'OPENED'});assert b.rows(M550.EVENT_OUT)[-1]['orders']['A\x1fr']['stage']=='FILLED_OPEN'
@pytest.mark.asyncio
async def test_05_halt_is_account_scoped():
 a,b=await start(M550,550);await a._on_halt({'account_id':'A','reason':'RISK'});assert a._halted_accounts=={'A':'RISK'};await a._on_reset({'account_id':'B'});assert 'A' in a._halted_accounts
@pytest.mark.asyncio
async def test_06_550_restart_preserves_lifecycle():
 a,_=await start(M550,550);await a._on_final(order());snap=await a.snapshot();c,_=await start(M550,550);await c.restore(snap);assert c._counts['decision_finalized']==1 and 'A\x1fr' in c._orders
@pytest.mark.asyncio
async def test_07_707_migrates_legacy_sent():
 with tempfile.TemporaryDirectory() as td:
  path=str(Path(td)/'d.db');conn=sqlite3.connect(path);conn.execute(M707._SCHEMA);conn.execute("INSERT INTO decisions(stage,request_id,account_id) VALUES('SENT','r','A')");conn.commit();conn.close();a,_=await start(M707,707,{'db_path':path,'keep_full_payload':False,'max_rows':100,'max_db_bytes':1000000});r=sqlite3.connect(path)
  # ويندوز يرفض حذف ملفّ مفتوح (WinError 32) — بخلاف لينكس. الإغلاق شرط لا تجميل.
  try:stage=r.execute('select stage from decisions').fetchone()[0]
  finally:r.close()
  assert stage=='DECISION_FINALIZED';await a.stop()
async def snapshot_atom(omit=(),advance=0,protection=False):
 a,b=await start(M583,583,{'ledger_max_age_s':30,'portfolio_max_age_s':30,'price_max_age_s':5,'specs_max_age_s':600,'dial_max_age_s':3600});await a._on_account({'account_id':'A','broker':'BR'});await a._on_pulse({'official_time':100});
 if 'ledger' not in omit:await a._on_ledger({'ledgers':[{'account_id':'A','broker':'BR','symbol':'NQ','R':50}]})
 if 'portfolio' not in omit:await a._on_portfolio({'portfolios':[{'account_id':'A','broker':'BR','symbol':'NQ','state':'NORMAL'}]})
 if 'price' not in omit:await a._on_tick({'account_id':'A','broker':'BR','symbol':'NQ','price':100,'bid':99,'ask':101,'timestamp':100})
 if 'specs' not in omit:await a._on_specs({'symbols':[{'account_id':'A','symbol':'NQ','point':.25,'tick_size':.25}]})
 if 'dial' not in omit:await a._on_dial({'profiles':[{'account_id':'A','broker':'BR','symbol':'NQ','dial':50}]})
 if advance:await a._on_pulse({'official_time':100+advance})
 target={'account_id':'A','broker':'BR','symbol':'NQ','status':'READY','action':'ADD','delta_buy':1,'delta_sell':0}
 if protection:target.update({'action':'REDUCE','delta_buy':-1,'delta_sell':0,'current_legs':[{'ticket':1,'side':'BUY','volume':1}]})
 await a._on_target(target);return a,b,b.rows(M583.EVENT_OUT)[-1]
@pytest.mark.asyncio
async def test_08_snapshot_without_price_is_incomplete():
 _,_,r=await snapshot_atom({'price'});assert r['snapshot_status']=='INCOMPLETE' and not r['usable_for_new_exposure']
@pytest.mark.asyncio
async def test_09_snapshot_without_specs_is_incomplete():
 _,_,r=await snapshot_atom({'specs'});assert r['snapshot_status']=='INCOMPLETE' and 'specs' in r['missing_components']
@pytest.mark.asyncio
async def test_10_snapshot_without_dial_blocks_add():
 _,_,r=await snapshot_atom({'dial'});assert not r['usable_for_new_exposure']
@pytest.mark.asyncio
async def test_11_stale_component_is_stale():
 _,_,r=await snapshot_atom(advance=6);assert r['snapshot_status']=='STALE' and 'price' in r['stale_components']
@pytest.mark.asyncio
async def test_12_complete_snapshot_is_ready():
 a,_,r=await snapshot_atom();assert r['snapshot_status']=='READY' and r['usable_for_new_exposure'];assert (await a.health_check()).state==M583.HealthState.HEALTHY
@pytest.mark.asyncio
async def test_13_protection_only_allows_reduction():
 _,_,r=await snapshot_atom({'price','specs','dial'},protection=True);assert r['snapshot_status']=='PROTECTION_ONLY' and r['usable_for_protection'] and not r['usable_for_new_exposure']
@pytest.mark.asyncio
async def test_14_incomplete_atomic_open_is_blocked_in_578():
 a,b=await start(M578,578,{'lot_step':.01,'min_volume':.01,'reward_risk':2,'max_attempts':2,'resend_hold_s':2,'catastrophe_stop_multiple':3,'fallback_stop_frac':.02});await a._on_external({'official_time':100,'account_id':'A','broker':'BR','trade_allowed':True});await a._on_positions({'account_id':'A','broker':'BR','positions':[],'usable_for_new_exposure':True,'usable_for_protection':True});await a._on_quality({'account_id':'A','broker':'BR','symbol':'NQ','status':'HEALTHY'});await a._on_divergence({'account_id':'A','broker':'BR','symbol':'NQ','status':'SYNCED'});await a._on_target({'account_id':'A','broker':'BR','symbol':'NQ','status':'READY','action':'REBALANCE','delta_buy':1,'delta_sell':-1,'produced_at':100,'snapshot_id':'s','usable_for_new_exposure':False,'usable_for_protection':True});assert not b.rows(M578.EVENT_REQUEST) and a._snapshot_blocked==1
@pytest.mark.asyncio
async def test_15_586_times_out_without_sys_second():
 a,b=await start(M586,586,{'resolution_timeout_s':.1});await a._on_order(order());await asyncio.sleep(.14);assert b.rows(M586.EVENT_REJECTED)[-1]['reason']=='SYMBOL_RESOLUTION_TIMEOUT';await a.stop()
@pytest.mark.asyncio
async def test_16_586_restart_matches_restored_request():
 a,_=await start(M586,586,{'resolution_timeout_s':1});await a._on_order(order());snap=await a.snapshot();await a.stop();c,b=await start(M586,586,{'resolution_timeout_s':1});await c.stop();await c.restore(snap);await c.start();await c._on_result({'request_id':'r','account_id':'A','logical_symbol':'NQ','broker_symbol':'NQ.cash','asset_canonical':'NQ','approved':True,'status':'RESOLVED','spec':{'point':.25}});assert b.rows(M586.EVENT_OUT)[-1]['symbol']=='NQ.cash';await c.stop()
@pytest.mark.asyncio
async def test_17_586_restored_expired_rejects_once():
 a,b=await start(M586,586,{'resolution_timeout_s':.1});await a.stop();await a.restore({'pending':[{'request_id':'r','account_id':'A','logical_symbol':'NQ','order':order(),'remaining_monotonic_s':0,'official_created_at':None,'official_deadline':None}], 'official_time':0});await a.start();await asyncio.sleep(.02);assert len(b.rows(M586.EVENT_REJECTED))==1;await a.stop()
@pytest.mark.asyncio
async def test_18_586_orphan_is_announced():
 a,b=await start(M586,586,{'resolution_timeout_s':1});await a._on_result({'request_id':'x'});assert b.rows(M586.EVENT_ORPHAN)[-1]['reason']=='UNKNOWN_OR_EXPIRED_REQUEST_ID';await a.stop()
async def quality_two_accounts(missing=False):
 a,b=await start(M560,560,{'max_adverse_points':50,'max_reject_rate':.5,'min_samples':1});await a._on_account({'account_id':'A','broker':'B1'});await a._on_account({'account_id':'B','broker':'B2'});rows=[{'account_id':'A','symbol':'NQ','point':None if missing else .25},{'account_id':'B','symbol':'NQ','point':1}];await a._on_specs({'symbols':rows});await a._on_request(order('A','B1','a'));await a._on_request(order('B','B2','b'));await a._on_trade({'account_id':'A','request_id':'a','event_type':'OPENED','entry_price':101});await a._on_trade({'account_id':'B','request_id':'b','event_type':'OPENED','entry_price':101});return a,b
@pytest.mark.asyncio
async def test_19_560_point_is_account_broker_scoped():
 _,b=await quality_two_accounts();rows=b.rows(M560.EVENT_OUT);assert rows[0]['adverse_max_points']==4 and rows[1]['adverse_max_points']==1
@pytest.mark.asyncio
async def test_20_560_missing_point_has_no_default():
 _,b=await quality_two_accounts(True);a=[x for x in b.rows(M560.EVENT_OUT) if x['account_id']=='A'][0];assert a['status']=='BLOCKED' and a['unmeasurable']==1 and a['last']['reason']=='SLIPPAGE_UNMEASURABLE'
async def confirm_atom(path):
 a,b=await start(M563,563,{'dedupe_db_path':str(path)});await a._on_account({'account_id':'A','broker':'BR'});await a._on_specs({'symbols':[{'account_id':'A','symbol':'NQ','point':.25,'tick_value':5,'tick_size':.25}]});await a._on_requested(order());return a,b
@pytest.mark.asyncio
async def test_21_563_slippage_uses_scoped_request_spec():
 with tempfile.TemporaryDirectory() as td:
  a,b=await confirm_atom(Path(td)/'j.db');await a._on_event({'account_id':'A','broker':'BR','request_id':'r','event_type':'OPENED','source_row_id':1,'symbol':'NQ','entry_price':101,'volume':1});assert b.rows(M563.EVENT_ACK)[-1]['slippage_points']==4
@pytest.mark.asyncio
async def test_22_563_old_event_survives_more_than_10000():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'j.db';j=Journal(str(path));j.ensure();conn=j.connect();conn.execute("INSERT INTO processed_trade_events VALUES (?,?,?,?,?)",('A|BR|OPENED|old','A','OPENED','old','{}'));conn.executemany("INSERT INTO processed_trade_events VALUES (?,?,?,?,?)",[(f'A|BR|OPENED|{i}','A','OPENED',str(i),'{}') for i in range(10001)]);conn.commit();conn.close();a,b=await confirm_atom(path);await a._on_event({'account_id':'A','broker':'BR','request_id':'r','event_type':'OPENED','source_row_id':'old','symbol':'NQ','entry_price':101,'volume':1});assert a._duplicates==1 and not b.rows(M563.EVENT_ACK)
@pytest.mark.asyncio
async def test_23_563_restart_rejects_old_duplicate():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'j.db';a,b=await confirm_atom(path);event={'account_id':'A','broker':'BR','request_id':'r','event_type':'OPENED','source_row_id':1,'symbol':'NQ','entry_price':101,'volume':1};await a._on_event(event);c,b2=await confirm_atom(path);await c._on_event(event);assert c._duplicates==1 and not b2.rows(M563.EVENT_ACK)
@pytest.mark.asyncio
async def test_24_563_missing_durable_id_is_rejected():
 with tempfile.TemporaryDirectory() as td:
  a,b=await confirm_atom(Path(td)/'j.db');await a._on_event({'account_id':'A','broker':'BR','request_id':'r','event_type':'OPENED','symbol':'NQ','entry_price':101});assert b.rows(M563.EVENT_REJECTED)[-1]['reason']=='MISSING_DURABLE_EVENT_ID_OR_SCOPE'
@pytest.mark.asyncio
async def test_25_563_outbox_recovers_pending_output():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'j.db';j=Journal(str(path));j.ensure();j.commit_event('i','A','OPENED','1',{},[('stable','execution.command.ack',{'account_id':'A','request_id':'r'})]);a,b=await start(M563,563,{'dedupe_db_path':str(path)});assert b.rows(M563.EVENT_ACK)[-1]['event_id']=='stable' and j.counts()['outbox_pending']==0
@pytest.mark.asyncio
async def test_26_563_storage_failure_is_fail_closed():
 with tempfile.TemporaryDirectory() as td:
  bad=Path(td)/'directory';bad.mkdir();a,b=await start(M563,563,{'dedupe_db_path':str(bad)});await a._on_event({'account_id':'A','broker':'BR','event_type':'OPENED','source_row_id':1});assert b.rows(M563.EVENT_REJECTED)[-1]['reason']=='DURABLE_DEDUPE_UNAVAILABLE';assert (await a.health_check()).state==M563.HealthState.UNHEALTHY


def risk_cfg(path):
 return {'max_daily_loss_pct':5,'max_consecutive_losses':3,'max_daily_trades':20,
         'max_open_trades':5,'consumer_db_path':str(path)}

@pytest.mark.asyncio
async def test_27_517_durable_outcome_consumer_rejects_restart_replay():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'c.db';cfg={'consumer_db_path':str(path)}
  a,b=await start(M517,517,cfg);await a._on_truth_equity({'account_id':'A','broker':'BR','equity':1000});await a._on_account({'account_id':'A','broker':'BR'})
  event={'event_id':'outcome:1','trade_id':'trade:1','account_id':'A','broker':'BR','symbol':'NQ','profit':-10,'commission':0,'swap':0,'fee':0}
  await a._on_outcome(event);assert len(b.rows(M517.EVENT_OUT))==1
  c,b2=await start(M517,517,cfg);await c._on_truth_equity({'account_id':'A','broker':'BR','equity':1000});await c._on_account({'account_id':'A','broker':'BR'});await c._on_outcome(event)
  assert c._duplicates==1 and not b2.rows(M517.EVENT_OUT)

@pytest.mark.asyncio
async def test_28_516_financial_projection_is_atomic_across_restart():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'c.db';event={'event_id':'trade-result:1','account_id':'A','completeness':'COMPLETE','loss_pct':3,'is_loss':True}
  a,_=await start(M516,516,risk_cfg(path));await a._on_truth_equity({'account_id':'A','broker':'BR','equity':1000});await a._on_account({'account_id':'A','broker':'BR'});await a._on_loss(event)
  c,_=await start(M516,516,risk_cfg(path));await c._on_truth_equity({'account_id':'A','broker':'BR','equity':1000});await c._on_account({'account_id':'A','broker':'BR'});await c._on_loss(event)
  assert c.book('A')['daily_loss_pct']==3 and c.book('A')['daily_trade_count']==1 and c._duplicates==1

@pytest.mark.asyncio
async def test_29_516_halt_outbox_recovers_after_publish_crash():
 class FailHalt(Bus):
  async def publish(self,n,p):
   if n==M516.EVENT_HALT:raise RuntimeError('simulated publish crash')
   await super().publish(n,p)
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'c.db';bad=FailHalt();a,_=await start(M516,516,risk_cfg(path),bad)
  await a._on_truth_equity({'account_id':'A','broker':'BR','equity':1000});await a._on_account({'account_id':'A','broker':'BR'})
  await a._on_loss({'event_id':'trade-result:halt','account_id':'A','completeness':'COMPLETE','loss_pct':6,'is_loss':True})
  assert a._storage_error and Journal(str(path)).counts()['outbox_pending']==1
  c,b=await start(M516,516,risk_cfg(path));assert b.rows(M516.EVENT_HALT)[-1]['event_id']=='risk-halt:trade-result:halt'
  assert Journal(str(path)).counts()['outbox_pending']==0

@pytest.mark.asyncio
async def test_30_518_financial_ledger_rejects_restart_replay():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'c.db';cfg={'default_risk_budget':0,'count_realized':True,'max_seen_trades':100,'consumer_db_path':str(path)}
  event={'event_id':'trade-result:ledger','account_id':'A','broker':'BR','symbol':'NQ','pnl':10,'gross_pnl':12,'completeness':'COMPLETE'}
  a,_=await start(M518,518,cfg);await a._on_account({'account_id':'A','broker':'BR'});await a._on_trade(event)
  c,_=await start(M518,518,cfg);await c._on_account({'account_id':'A','broker':'BR'});await c._on_trade(event)
  key=next(iter(c._realized));assert c._realized[key]==10 and c._realized_gross[key]==12 and c._duplicates==1

@pytest.mark.asyncio
async def test_31_downstream_financial_consumers_fail_closed_without_event_id():
 with tempfile.TemporaryDirectory() as td:
  a,_=await start(M516,516,risk_cfg(Path(td)/'a.db'));await a._on_loss({'account_id':'A','completeness':'COMPLETE','loss_pct':9,'is_loss':True});assert a.book('A')['daily_loss_pct']==0
  c,_=await start(M518,518,{'default_risk_budget':0,'count_realized':True,'max_seen_trades':100,'consumer_db_path':str(Path(td)/'b.db')});await c._on_account({'account_id':'A','broker':'BR'});await c._on_trade({'account_id':'A','broker':'BR','symbol':'NQ','pnl':9,'completeness':'COMPLETE'});assert not c._realized and c._missing_event_ids==1

@pytest.mark.asyncio
async def test_32_late_ack_cannot_regress_confirmed_fill_stage():
 a,b=await start(M550,550);payload=order();await a._on_trade({**payload,'event_type':'OPENED'});await a._on_ack(payload)
 assert b.rows(M550.EVENT_OUT)[-1]['orders']['A\x1fr']['stage']=='FILLED_OPEN'

@pytest.mark.asyncio
async def test_33_bridge_write_failure_has_distinct_truth_counter():
 a,b=await start(M550,550);await a._on_bridge_failed({**order(),'error':'DISK'});row=b.rows(M550.EVENT_OUT)[-1]
 assert row['counts']['bridge_write_failed']==1 and row['counts']['broker_command_failed']==0

@pytest.mark.asyncio
async def test_34_management_state_commits_only_after_command_ack():
 a,b=await start(M570,570);await a._on_pulse({'official_time':100})
 await a._on_intent({'account_id':'A','ticket':7,'symbol':'NQ','side':'BUY','action':'MODIFY_SL','stop_loss':100,'reason':'protect'})
 command=b.rows(M570.EVENT_OUT)[-1];await a._on_written(command)
 assert a._state['A|7']['best_sl'] is None and a._pending[command['request_id']]['status']=='QUEUED_TO_BRIDGE'
 await a._on_ack(command);assert a._state['A|7']['best_sl']==100
