from __future__ import annotations

import asyncio,importlib.util,inspect,json,sqlite3,sys,tempfile
from pathlib import Path
import pytest
from core.contracts.atom import AtomContext,HealthState

ROOT=Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)


def load(atom_id):
 folder=next((ATOM_ROOT).glob(f'{atom_id}_*'));sys.path.insert(0,str(folder));name=f'p11_{atom_id}'
 spec=importlib.util.spec_from_file_location(name,folder/'atom.py');module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
M575=load(575);M601=load(601);M609=load(609);M611=load(611);M618=load(618);M619=load(619)
class Log:
 def __getattr__(self,name):return lambda *args,**kwargs:None
class Bus:
 def __init__(self):self.events=[];self.handlers={}
 def subscribe(self,name,handler):self.handlers.setdefault(name,[]).append(handler)
 async def publish(self,name,payload):
  self.events.append((name,payload))
  for handler in list(self.handlers.get(name,[])):
   result=handler(payload)
   if inspect.isawaitable(result):await result
 def rows(self,name):return [payload for event,payload in self.events if event==name]
async def start(module,atom_id,cfg,bus=None):
 bus=bus or Bus();atom=module.Atom();await atom.initialize(AtomContext(atom_id,cfg,Log(),bus.publish,bus.subscribe));await atom.start();return atom,bus

# ويندوز يرفض حذف مجلّد فيه ملفّ مفتوح (WinError 32) — بخلاف لينكس. فأيّ اتّصال
# sqlite أو ذرّة تفتح القاعدة يجب إغلاقها قبل خروج TemporaryDirectory، وإلّا سقط
# الاختبار على كود سليم. سندها: قاعدة المالك «الكود ويندوز-سليم، لا التفاف بلينكس».
def q1(path,sql):
 c=sqlite3.connect(path)
 try:return c.execute(sql).fetchone()
 finally:c.close()

def account_schema(connection,table):
 connection.execute(f"CREATE TABLE {table} (account_id TEXT PRIMARY KEY,balance REAL,equity REAL,margin REAL,free_margin REAL,margin_level REAL,currency TEXT,leverage INTEGER,open_count INTEGER,updated_at REAL,connected INTEGER,trade_allowed INTEGER,expert_allowed INTEGER,bridge_beat REAL,broker TEXT,account_server TEXT,margin_mode INTEGER)")

def add_account(connection,table,account,broker,equity=1000):
 connection.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(account,1000,equity,0,equity,0,'USD',100,0,100,1,1,1,100,broker,broker+'-SERVER',1))

@pytest.mark.asyncio
async def test_01_619_reads_account_v2_only_and_keeps_two_accounts_isolated():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'b.db';c=sqlite3.connect(path);account_schema(c,'account');account_schema(c,'account_v2');add_account(c,'account','OLD','WRONG');add_account(c,'account_v2','A','B1');add_account(c,'account_v2','B','B2');c.commit();c.close()
  atom,bus=await start(M619,619,{'db_path':str(path),'table_name':'account_v2','poll_interval_s':60,'max_age_s':300});await atom._on_pulse({'official_time':100});await atom._read_once();await atom.stop()
  # `stop()` يُلغي الانتظار لا الخيط — بايثون لا يقتل خيطًا. فقراءة جارية داخل
  # `asyncio.to_thread` تبقى ماسكة ملفّ القاعدة لحظات بعد رجوع `stop()`، وويندوز
  # يرفض حذف المجلّد وقتها (WinError 32/267) بينما لينكس يسمح. الذرّة سليمة —
  # تُغلق اتّصالها دائمًا في `finally` — والسباق في الاختبار وحده.
  # هذا السطر ينتظر خيوط المجمّع فعليًّا: حتميّ، بلا `sleep`، وبلا إسقاط أيّ تغطية.
  await asyncio.get_running_loop().shutdown_default_executor()
  rows=bus.rows(M619.EVENT_OUT);assert {(row['account_id'],row['broker']) for row in rows}=={('A','B1'),('B','B2')}

@pytest.mark.asyncio
async def test_02_619_never_falls_back_to_legacy_account():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'b.db';c=sqlite3.connect(path);account_schema(c,'account');add_account(c,'account','OLD','WRONG');c.commit();c.close()
  bus=Bus();atom=M619.Atom();await atom.initialize(AtomContext(619,{'db_path':str(path),'table_name':'account','poll_interval_s':1,'max_age_s':300},Log(),bus.publish,bus.subscribe));atom._running=True;await atom._read_once()
  assert not bus.rows(M619.EVENT_OUT) and atom._table=='account_v2' and atom._last_error

@pytest.mark.asyncio
async def test_03_601_requires_account_v2_and_never_legacy_account():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'b.db';c=sqlite3.connect(path);account_schema(c,'account');add_account(c,'account','A','OLD');c.commit();c.close()
  atom=M601.Atom();await atom.initialize(AtomContext(601,{'account_id':'A','db_path':str(path),'heartbeat_interval_s':60,'magic':20260801},Log(),Bus().publish,Bus().subscribe))
  assert atom._read_bridge_accounts() is None

@pytest.mark.asyncio
async def test_04_601_rejects_missing_magic_and_writes_scoped_magic():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'b.db';c=sqlite3.connect(path);account_schema(c,'account_v2');add_account(c,'account_v2','A','BR');c.commit();c.close()
  atom,bus=await start(M601,601,{'account_id':'A','db_path':str(path),'heartbeat_interval_s':60,'magic':20260801})
  base={'account_id':'A','request_id':'r','symbol':'NQ','side':'BUY','volume':1,'reference_price':100,'stop_loss':99,'take_profit':102}
  await atom._on_final_decision(base);assert bus.rows(M601.EVENT_WRITE_FAILED)[-1]['reason']=='MISSING_OR_FOREIGN_MAGIC'
  await atom._on_final_decision({**base,'magic':20260801});await atom.stop()
  row=q1(path,"SELECT account_id,request_id,magic FROM commands");assert row==('A','r',20260801)

@pytest.mark.asyncio
async def test_05_575_rejects_foreign_account_ticket_and_magic():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'b.db';c=sqlite3.connect(path);c.execute("CREATE TABLE positions_v2(account_id TEXT,ticket INTEGER,symbol TEXT,magic INTEGER)");c.execute("INSERT INTO positions_v2 VALUES ('A',7,'NQ',20260801)");c.commit();c.close()
  atom,bus=await start(M575,575,{'enabled':True,'db_path':str(path),'magic':20260801});await atom._on_pulse({'official_time':100})
  await atom._on_command({'account_id':'B','magic':20260801,'request_id':'x','action':'MODIFY_SL','ticket':7,'symbol':'NQ','side':'BUY','stop_loss':99})
  assert bus.rows(M575.EVENT_FAILED)[-1]['reason']=='POSITION_OWNERSHIP_MISMATCH'
  await atom._on_command({'account_id':'A','magic':9,'request_id':'y','action':'MODIFY_SL','ticket':7,'symbol':'NQ','side':'BUY','stop_loss':99})
  assert bus.rows(M575.EVENT_FAILED)[-1]['reason']=='MISSING_OR_FOREIGN_MAGIC'

@pytest.mark.asyncio
async def test_06_611_rejects_trade_event_without_account_identity():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'b.db';c=sqlite3.connect(path);c.execute("CREATE TABLE trade_events_v2(id INTEGER,event_type TEXT,ticket INTEGER,symbol TEXT,side TEXT,volume REAL,entry_price REAL,exit_price REAL,open_time REAL,close_time REAL,reason TEXT,account_id TEXT,profit REAL,request_id TEXT,commission REAL,swap REAL,fee REAL,trade_id TEXT)");c.execute("INSERT INTO trade_events_v2 VALUES (1,'CLOSED',1,'NQ','BUY',1,100,99,1,2,'x',NULL,-1,'r',0,0,0,'T')");c.commit();c.close()
  bus=Bus();atom=M611.Atom();await atom.initialize(AtomContext(611,{'db_path':str(path),'table_name':'trade_events_v2','poll_interval_s':1,'batch_limit':10,'max_age_s':60,'cost_refresh_timeout_s':60},Log(),bus.publish,bus.subscribe));await atom._drain_once()
  assert not bus.rows(M611.EVENT_OUT) and atom.identity_rejected==1

# ذرّة 617 (قارئ ملفّ جسر سي-تريدر) أُرشفت بيد المالك 2026-08-21 بعد أن
# حلّت محلّها الذرّة 622 (FIX مباشر). خمسة اختبارات كانت تحرس قارئ الملفّ
# أُزيلت معها -- حارس بلا محروس ليس حارسًا. ومعانيها كلّها محروسة اليوم
# داخل اختبارات 622 نفسها (التسلسل، حالات الصحّة، اللقطة والاسترجاع).
# ويبقى اختبار مصدر الـcBot أدناه: الملفّ ما زال بالشجرة، وحراسة
# «لا يتداول» تبقى سارية ما دام موجودًا.
def test_12_ctrader_source_is_market_only_and_sequences_every_line():
 source=(ROOT/'ctrader/QuantNQ_Feed.cs').read_text();assert '_sequence++' in source and 'WithSequence' in source
 assert not any(token in source for token in ('ExecuteMarketOrder(','PlaceLimitOrder(','ClosePosition(','ModifyPosition('))

def test_13_mt5_preserves_full_broker_execution_evidence():
 source=(ROOT/'mt5/QUANT_NQ.mq5').read_text();assert all(token in source for token in ('TradeResultOk(false)','Trade.ResultDeal()','DEAL_POSITION_ID','fill <= 0','NO_CONFIRMED_DEAL'))

def test_14_mt5_operational_writes_are_v2_only():
 source=(ROOT/'mt5/QUANT_NQ.mq5').read_text();assert 'INSERT INTO trade_events_v2' in source and 'INSERT INTO positions_v2' in source and 'INSERT INTO ticks_v2' in source and 'REPLACE INTO symbol_specs_v2' in source and 'UPDATE account_v2' in source
 assert not any(token in source for token in ('INSERT INTO trade_events (','REPLACE INTO symbol_specs (','INSERT INTO account (','UPDATE account SET','INSERT INTO positions (','INSERT INTO ticks (','INSERT INTO prices ('))

def test_15_mt5_ticket_ownership_includes_account_symbol_magic_ticket():
 source=(ROOT/'mt5/QUANT_NQ.mq5').read_text();assert all(token in source for token in ('account_id != current_account','command_magic != InpMagic','PositionGetString(POSITION_SYMBOL) != sym','PositionGetInteger(POSITION_MAGIC) != command_magic','ticket == 0'))

def test_16_all_python_mt5_readers_are_pinned_to_v2():
 import yaml
 expected={609:('table_name','positions_v2'),611:('table_name','trade_events_v2'),619:('table_name','account_v2')}
 for atom_id,(key,value) in expected.items():
  data=yaml.safe_load((next((ATOM_ROOT).glob(f'{atom_id}_*'))/'manifest.yaml').read_text());assert data['config'][key]==value
 data=yaml.safe_load((next((ATOM_ROOT).glob('618_*'))/'manifest.yaml').read_text());assert data['config']['table_name']=='ticks_v2' and data['config']['spec_table']=='symbol_specs_v2'

def test_17_divergence_is_account_broker_symbol_scoped():
 source=(next((ATOM_ROOT).glob('578_*'))/'atom.py').read_text();assert '_key(account,broker,symbol)+SEP+timeframe' in source

def test_18_bridge_proof_is_17_of_17():
 import subprocess
 result=subprocess.run([sys.executable,str(ROOT/'governance/scripts/proof_bridges.py'),str(ROOT)],capture_output=True,text=True)
 assert result.returncode==0 and 'PROOF_BRIDGES=17 PASS 0 FAIL' in result.stdout

@pytest.mark.asyncio
async def test_19_restart_keeps_scoped_request_in_bridge_database():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'b.db';c=sqlite3.connect(path);account_schema(c,'account_v2');add_account(c,'account_v2','A','BR');c.commit();c.close()
  cfg={'account_id':'A','db_path':str(path),'heartbeat_interval_s':60,'magic':20260801}
  first,_=await start(M601,601,cfg);await first._on_final_decision({'account_id':'A','magic':20260801,'request_id':'persist-r','symbol':'NQ','side':'BUY','volume':1,'reference_price':100,'stop_loss':99,'take_profit':102});await first.stop()
  second=M601.Atom();await second.initialize(AtomContext(601,cfg,Log(),Bus().publish,Bus().subscribe))
  row=q1(path,"SELECT account_id,request_id,magic,status FROM commands");await second.stop();assert row==('A','persist-r',20260801,'PENDING')

@pytest.mark.asyncio
async def test_20_restart_rereads_scoped_position_from_v2_truth():
 with tempfile.TemporaryDirectory() as td:
  path=Path(td)/'b.db';c=sqlite3.connect(path);c.execute("CREATE TABLE positions_v2(ticket INTEGER,symbol TEXT,side TEXT,volume REAL,entry_price REAL,current_price REAL,stop_loss REAL,take_profit REAL,profit REAL,swap REAL,magic INTEGER,opened_at REAL,updated_at REAL,account_id TEXT,commission REAL)");c.execute("INSERT INTO positions_v2 VALUES (1,'NQ','BUY',1,100,101,99,0,1,0,20260801,90,100,'A',0)");c.commit();c.close()
  cfg={'db_path':str(path),'table_name':'positions_v2','poll_interval_s':60,'stale_after_s':10}
  for _ in range(2):
   bus=Bus();atom=M609.Atom();await atom.initialize(AtomContext(609,cfg,Log(),bus.publish,bus.subscribe));atom._running=True;await atom._on_account({'account_id':'A','broker':'BR'});await atom._on_pulse({'official_time':100});await atom._read_once();assert bus.rows(M609.EVENT_OUT)[-1]['positions'][0]['ticket']==1
