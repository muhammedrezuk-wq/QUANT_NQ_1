from __future__ import annotations

import asyncio
import importlib.util
import inspect
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

from core.contracts.atom import AtomContext, HealthState

ROOT = Path(__file__).resolve().parents[1]
from build_registry.paths import RegistryAtomRoot
ATOM_ROOT = RegistryAtomRoot(ROOT)



def load(atom_id: int):
    folder = next((ATOM_ROOT).glob(f"{atom_id}_*"))
    sys.path.insert(0, str(folder))
    name = f"paper910_{atom_id}"
    spec = importlib.util.spec_from_file_location(name, folder / "atom.py")
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


M508=load(508);M513=load(513);M517=load(517);M524=load(524);M552=load(552)
M563=load(563);M578=load(578);M579=load(579);M609=load(609);M611=load(611);M618=load(618)


class Log:
    def __getattr__(self, name): return lambda *args, **kwargs: None


class Bus:
    def __init__(self): self.events=[]; self.handlers={}
    def subscribe(self,name,handler): self.handlers.setdefault(name,[]).append(handler)
    async def publish(self,name,payload):
        self.events.append((name,payload))
        for handler in list(self.handlers.get(name,[])):
            result=handler(payload)
            if inspect.isawaitable(result): await result
    def rows(self,name): return [payload for event,payload in self.events if event==name]


async def start(module, atom_id, config, bus=None):
    bus=bus or Bus();atom=module.Atom()
    await atom.initialize(AtomContext(atom_id,config,Log(),bus.publish,bus.subscribe));await atom.start()
    return atom,bus


def make_positions_db(path: Path, optional=True, commission=1.0, updated=100.0):
    connection=sqlite3.connect(path)
    extra=", account_id TEXT, commission REAL" if optional else ""
    connection.execute("CREATE TABLE positions_v2 (ticket INTEGER,symbol TEXT,side TEXT,volume REAL,entry_price REAL,current_price REAL,stop_loss REAL,take_profit REAL,profit REAL,swap REAL,magic INTEGER,opened_at REAL,updated_at REAL"+extra+")")
    columns="ticket,symbol,side,volume,entry_price,current_price,profit,swap,updated_at"+(",account_id,commission" if optional else "")
    values=(1,"NQ","BUY",1,100,101,5,0,updated)+(("A",commission) if optional else ())
    marks=",".join("?" for _ in values);connection.execute(f"INSERT INTO positions_v2 ({columns}) VALUES ({marks})",values)
    connection.commit();connection.close()


async def position_reader(path: Path, stale=5.0):
    bus=Bus();atom=M609.Atom();cfg={"db_path":str(path),"table_name":"positions_v2","poll_interval_s":60,"stale_after_s":stale}
    await atom.initialize(AtomContext(609,cfg,Log(),bus.publish,bus.subscribe));atom._running=True
    await atom._on_account({"account_id":"A","broker":"BR"});return atom,bus


@pytest.mark.asyncio
async def test_01_609_no_picture_is_unknown():
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/"p.db";make_positions_db(path)
        atom,_=await position_reader(path)
        assert (await atom.health_check()).state==HealthState.UNKNOWN


@pytest.mark.asyncio
async def test_02_609_recent_complete_picture_is_healthy():
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/"p.db";make_positions_db(path)
        atom,bus=await position_reader(path);await atom._on_pulse({"official_time":100});await atom._read_once()
        row=bus.rows(M609.EVENT_OUT)[-1]
        assert row["snapshot_status"]=="READY" and row["usable_for_new_exposure"]
        assert (await atom.health_check()).state==HealthState.HEALTHY


@pytest.mark.asyncio
async def test_03_609_successful_read_of_frozen_picture_is_stale():
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/"p.db";make_positions_db(path,updated=100)
        atom,bus=await position_reader(path);await atom._on_pulse({"official_time":110});await atom._read_once()
        row=bus.rows(M609.EVENT_OUT)[-1]
        assert row["snapshot_status"]=="STALE" and not row["usable_for_new_exposure"] and row["usable_for_protection"]
        assert (await atom.health_check()).message=="POSITIONS_STALE"


@pytest.mark.asyncio
async def test_04_609_optional_query_failure_is_named_unavailable():
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/"p.db";make_positions_db(path)
        atom,bus=await position_reader(path);original=atom._query_optional
        def fail(connection,column,tickets):
            if column=="account_id": raise sqlite3.OperationalError("forced")
            return original(connection,column,tickets)
        atom._query_optional=fail;await atom._on_pulse({"official_time":100});await atom._read_once()
        assert (await atom.health_check()).message=="ACCOUNT_ID_UNAVAILABLE"
        assert bus.rows(M609.EVENT_OUT)[-1]["unknown_positions"][0]["account_id_status"]=="UNAVAILABLE"


@pytest.mark.asyncio
async def test_05_609_legacy_schema_announced_once_and_picture_diagnostic():
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/"p.db";make_positions_db(path,optional=False)
        atom,bus=await position_reader(path);await atom._on_pulse({"official_time":100})
        await atom._read_once();await atom._read_once()
        assert len(bus.rows(M609.EVENT_SCHEMA))==2
        row=bus.rows(M609.EVENT_OUT)[-1]
        assert row["snapshot_status"]=="INCOMPLETE" and not row["usable_for_new_exposure"]


@pytest.mark.asyncio
async def test_06_stale_positions_block_open_but_not_reduction_in_578():
    import tempfile as _tf
    # v5.3.1: عزل مخزن الأزواج الدائم — بلا العزل يقرأ الفحص أزواج الإنتاج
    # الحية فيحكم تضاربًا مع صورة وسيطه الوهمية ويجمّد المسار.
    cfg={"lot_step":.01,"min_volume":.01,"reward_risk":2,"max_attempts":2,"resend_hold_s":2,
         "catastrophe_stop_multiple":3,"fallback_stop_frac":.02,
         "pair_store_path":str(Path(_tf.mkdtemp())/"pairs.db")}
    atom,bus=await start(M578,578,cfg);await atom._on_external({"official_time":100,"account_id":"A","broker":"BR","trade_allowed":True})
    await atom._on_positions({"account_id":"A","broker":"BR","positions":[],"usable_for_new_exposure":False,"usable_for_protection":True})
    await atom._on_quality({"account_id":"A","broker":"BR","symbol":"NQ","status":"HEALTHY"});await atom._on_divergence({"account_id":"A","broker":"BR","symbol":"NQ","status":"SYNCED"})
    base={"account_id":"A","broker":"BR","symbol":"NQ","status":"READY","produced_at":100,"snapshot_id":"s","reference_price":100,"usable_for_new_exposure":True,"usable_for_protection":True}
    await atom._on_target({**base,"action":"ADD","delta_buy":1,"delta_sell":0});assert not bus.rows(M578.EVENT_REQUEST)
    await atom._on_target({**base,"action":"REDUCE","delta_buy":-1,"delta_sell":0,"current_legs":[{"ticket":1,"side":"BUY","volume":1,"current_price":100}]})
    assert bus.rows(M578.EVENT_REQUEST)[-1]["action"]=="CLOSE_PARTIAL"


async def extraction_pair(max_attempts=1):
    bus=Bus();cfg524={"milestone_mult":2,"extract_fraction":.5,"full_targets":{},"default_full_target":0}
    owner,_=await start(M524,524,cfg524,bus);executor,_=await start(M579,579,{"lot_step":.01,"confirmation_timeout_s":10,"max_attempts":max_attempts},bus)
    await executor._on_positions({"positions":[{"account_id":"A","symbol":"NQ","ticket":1,"side":"BUY","volume":1,"profit":100}]})
    await owner._on_ledger({"ledgers":[{"account_id":"A","symbol":"NQ","R":50,"budgeted":True,"realized_gross":50}]})
    return owner,executor,bus


@pytest.mark.asyncio
async def test_07_extraction_failure_changes_524_operational_state():
    owner,executor,bus=await extraction_pair();command=bus.rows(M579.EVENT_MANAGE)[-1]
    await executor._on_command_failed({**command,"reason":"BROKER_FAIL"})
    request=next(iter(owner._pending.values()))
    assert request["status"]=="FAILED" and request["failure_reason"]=="BROKER_FAIL"
    assert bus.rows(M524.EVENT_STATE)[-1]["status"]=="FAILED"


@pytest.mark.asyncio
async def test_08_duplicate_extraction_failure_does_not_duplicate_state_or_money():
    owner,executor,bus=await extraction_pair();command=bus.rows(M579.EVENT_MANAGE)[-1]
    failure={**command,"reason":"BROKER_FAIL"};await executor._on_command_failed(failure);count=len(bus.rows(M524.EVENT_STATE));await executor._on_command_failed(failure)
    assert len(bus.rows(M524.EVENT_STATE))==count and not bus.rows(M524.EVENT_EXTRACTED)


@pytest.mark.asyncio
async def test_09_extraction_failure_survives_restart():
    owner,executor,bus=await extraction_pair();command=bus.rows(M579.EVENT_MANAGE)[-1]
    await executor._on_command_failed({**command,"reason":"BROKER_FAIL"});state=await executor.snapshot()
    restored,_=await start(M579,579,{"lot_step":.01,"confirmation_timeout_s":10,"max_attempts":1})
    await restored.restore(state);pending=next(iter(restored._pending_by_ticket.values()))
    assert pending["status"]=="FAILED" and pending["failure_reason"]=="BROKER_FAIL"


@pytest.mark.asyncio
async def test_10_no_random_retry_and_explicit_retry_policy():
    owner,executor,bus=await extraction_pair(max_attempts=2);command=bus.rows(M579.EVENT_MANAGE)[-1]
    await executor._on_command_failed({**command,"reason":"BROKER_FAIL"});before=len(bus.rows(M579.EVENT_MANAGE));await executor._on_pulse({"official_time":999})
    assert len(bus.rows(M579.EVENT_MANAGE))==before
    await executor._on_retry({"extraction_id":command["extraction_id"],"ticket":command["ticket"]})
    assert len(bus.rows(M579.EVENT_MANAGE))==before+1 and bus.rows(M579.EVENT_MANAGE)[-1]["request_id"].endswith("-a2")


@pytest.mark.asyncio
async def test_11_extraction_success_requires_actual_trade_event():
    owner,executor,bus=await extraction_pair();command=bus.rows(M579.EVENT_MANAGE)[-1]
    assert not bus.rows(M524.EVENT_EXTRACTED)
    await executor._on_trade({"event_type":"PARTIAL","account_id":"A","symbol":"NQ","ticket":1,"request_id":command["request_id"],"profit":25})
    assert bus.rows(M524.EVENT_EXTRACTED)[-1]["amount"]==25


async def outcome_atom(path: Path, timeout=.3):
    atom,bus=await start(M517,517,{"consumer_db_path":str(path),"cost_wait_timeout_s":timeout})
    await atom._on_truth_equity({"account_id":"A","broker":"BR","equity":1000});await atom._on_account({"account_id":"A","broker":"BR"});return atom,bus


def outcome(event_id, **extra):
    return {"event_id":event_id,"trade_id":"T1","account_id":"A","broker":"BR","symbol":"NQ","profit":-10,**extra}


@pytest.mark.asyncio
async def test_12_partial_outcome_waits_then_completes():
    with tempfile.TemporaryDirectory() as td:
        atom,bus=await outcome_atom(Path(td)/"j.db")
        await atom._on_outcome(outcome("r1",commission=None,swap=0,fee=0));assert atom._pending and not bus.rows(M517.EVENT_OUT)
        await atom._on_outcome(outcome("r2",commission=-1,swap=0,fee=0));row=bus.rows(M517.EVENT_OUT)[-1]
        assert row["completeness"]=="COMPLETE" and row["pnl"]==-11 and not atom._pending;await atom.stop()


@pytest.mark.asyncio
async def test_13_partial_timeout_is_announced_not_dropped():
    with tempfile.TemporaryDirectory() as td:
        atom,bus=await outcome_atom(Path(td)/"j.db",.06)
        await atom._on_outcome(outcome("r1",commission=None,swap=0,fee=0));await asyncio.sleep(.14)
        row=bus.rows(M517.EVENT_OUT)[-1];assert row["completeness"]=="INCOMPLETE" and row["pnl"] is None;await atom.stop()


@pytest.mark.asyncio
async def test_14_partial_outcome_survives_snapshot_restart():
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/"j.db";atom,_=await outcome_atom(path,1)
        await atom._on_outcome(outcome("r1",commission=None,swap=0,fee=0));state=await atom.snapshot();await atom.stop()
        restored,bus=await outcome_atom(path,1);await restored.restore(state)
        await restored._on_outcome(outcome("r2",commission=-1,swap=0,fee=0));assert bus.rows(M517.EVENT_OUT)[-1]["completeness"]=="COMPLETE";await restored.stop()


@pytest.mark.asyncio
async def test_15_zero_cost_is_known_and_complete():
    with tempfile.TemporaryDirectory() as td:
        atom,bus=await outcome_atom(Path(td)/"j.db")
        await atom._on_outcome(outcome("r1",commission=0,swap=0,fee=0));assert bus.rows(M517.EVENT_OUT)[-1]["costs_complete"];await atom.stop()


@pytest.mark.asyncio
async def test_16_563_accepts_cost_revision_for_same_trade_row():
    with tempfile.TemporaryDirectory() as td:
        atom,bus=await start(M563,563,{"dedupe_db_path":str(Path(td)/"j.db")});await atom._on_account({"account_id":"A","broker":"BR"})
        base={"account_id":"A","broker":"BR","event_type":"CLOSED","source_row_id":7,"trade_id":"T1","symbol":"NQ","profit":-10,"swap":0,"fee":0}
        await atom._on_event({**base,"commission":None});await atom._on_event({**base,"commission":-1})
        rows=bus.rows(M563.EVENT_OUT);assert len(rows)==2 and rows[0]["trade_identity"]==rows[1]["trade_identity"]=="T1"


@pytest.mark.asyncio
async def test_17_611_republishes_late_cost_revision():
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/"t.db";connection=sqlite3.connect(path)
        connection.execute("CREATE TABLE trade_events_v2 (id INTEGER,event_type TEXT,ticket INTEGER,symbol TEXT,side TEXT,volume REAL,entry_price REAL,exit_price REAL,open_time REAL,close_time REAL,reason TEXT,account_id TEXT,profit REAL,request_id TEXT,commission REAL,swap REAL,fee REAL,trade_id TEXT)")
        connection.execute("INSERT INTO trade_events_v2 VALUES (1,'CLOSED',1,'NQ','BUY',1,100,99,1,2,'x','A',-10,'r',NULL,0,0,'T1')");connection.commit();connection.close()
        bus=Bus();atom=M611.Atom();cfg={"db_path":str(path),"table_name":"trade_events_v2","poll_interval_s":1,"batch_limit":10,"max_age_s":60}
        await atom.initialize(AtomContext(611,cfg,Log(),bus.publish,bus.subscribe));await atom._drain_once()
        connection=sqlite3.connect(path);connection.execute("UPDATE trade_events_v2 SET commission=-1 WHERE id=1");connection.commit();connection.close();await atom._drain_once()
        rows=bus.rows(M611.EVENT_OUT);assert len(rows)==2 and rows[0]["commission"] is None and rows[1]["commission"]==-1


async def exposure_atom():
    atom,bus=await start(M508,508,{"max_exposure_pct":50,"specs_max_age_s":60})
    await atom._on_truth_equity({"account_id":"A","broker":"BR","equity":1000});await atom._on_account({"account_id":"A","broker":"BR"})
    await atom._on_specs({"account_id":"A","broker":"BR","symbols":[{"account_id":"A","symbol":"NQ","contract_size":1}]})
    return atom,bus


@pytest.mark.asyncio
async def test_18_unknown_position_is_preserved_and_blocks_exposure():
    atom,bus=await exposure_atom();picture={"account_id":"A","broker":"BR","source":"609","positions":[{"account_id":"A","broker":"BR","ticket":1,"symbol":"NQ","volume":None,"current_price":100}],"usable_for_new_exposure":False,"usable_for_protection":True}
    await atom._on_positions(picture);row=bus.rows(M508.EVENT_OUT)[-1]
    assert row["status"]=="UNKNOWN_POSITION" and row["unknown_position_count"]==1 and atom._unknown_positions


@pytest.mark.asyncio
async def test_19_late_position_data_recalculates_exposure_automatically():
    atom,bus=await exposure_atom();base={"account_id":"A","broker":"BR","source":"609","usable_for_protection":True}
    await atom._on_positions({**base,"positions":[{"account_id":"A","broker":"BR","ticket":1,"symbol":"NQ","volume":None,"current_price":100}],"usable_for_new_exposure":False})
    await atom._on_positions({**base,"positions":[{"account_id":"A","broker":"BR","ticket":1,"symbol":"NQ","volume":2,"current_price":100}],"usable_for_new_exposure":True})
    row=bus.rows(M508.EVENT_OUT)[-1];assert row["status"]=="OK" and row["notional"]==200 and not atom._unknown_positions


@pytest.mark.asyncio
async def test_20_unknown_position_survives_restart():
    atom,_=await exposure_atom();await atom._on_positions({"account_id":"A","broker":"BR","source":"609","positions":[{"ticket":1,"symbol":"NQ"}],"usable_for_new_exposure":False,"usable_for_protection":True});state=await atom.snapshot()
    restored,_=await start(M508,508,{"max_exposure_pct":50,"specs_max_age_s":60});await restored.restore(state);assert restored._unknown_positions


@pytest.mark.asyncio
async def test_21_552_blocks_open_on_unknown_exposure_but_allows_management():
    import clock as official_clock
    official_clock.reset_for_tests();official_clock.configure(max_accepted_offset_s=5,max_sample_age_s=30,stale_after_s=900,max_slew_per_second=.05);official_clock.accept_sample({"median_offset_s":.1,"measured_at":time.time(),"quorum":True},writer="003")
    atom,bus=await start(M552,552,{"enabled":True,"max_spread_points":0});await atom._on_account({"account_id":"A","broker":"BR"});await atom._on_whitelist({"allowed_by_account":{"A":["NQ"]}});await atom._on_reconcile({"account_id":"A","broker":"BR","symbol":"*","status":"MATCH_EMPTY_ACCOUNT"});await atom._on_reference({"symbol":"NQ","state":"HEALTHY"});await atom._on_exposure({"account_id":"A","broker":"BR","usable_for_new_exposure":False})
    # عقد T3 (دخل على 552 بعد كتابة الاختبار): الفتح يحتاج قرارًا أبًا + حكم هامش 585 معتمدًا
    await atom._on_margin_verdict({"account_id":"A","request_id":"o","approved":True,"reason":"OK"})
    order={"magic":20260801,"account_id":"A","broker":"BR","request_id":"o","decision_id":"d1","action":"OPEN","symbol":"NQ","side":"BUY","volume":1,"reference_price":100,"stop_loss":99,"take_profit":102}
    await atom._on_built(order);assert bus.rows(M552.EVENT_REJECTED)[-1]["reason"]=="EXPOSURE_STATE_NOT_USABLE"
    await atom._on_built({**order,"request_id":"m","action":"MODIFY_SL","ticket":1});assert bus.rows(M552.EVENT_FINAL)[-1]["request_id"]=="m"


async def sizing_atom(max_age=60):
    cfg={"risk_per_trade_pct":1,"default_stop_pct":.5,"min_lot":.01,"max_lot":1,"lot_step":.01,"specs_max_age_s":max_age}
    atom,bus=await start(M513,513,cfg);await atom._on_truth_equity({"account_id":"A","broker":"BR","equity":1000});await atom._on_account({"account_id":"A","broker":"BR"});return atom,bus


def spec(account="A",broker="BR",symbol="NQ"):
    return {"account_id":account,"broker":broker,"symbols":[{"account_id":account,"broker":broker,"symbol":symbol,"tick_value":1,"tick_size":.01}]}


def tick(account="A",broker="BR",symbol="NQ"):
    # 513 تِكّية منذ تحويل القسم: الحمولة كما في اختبار الذرّة نفسها (بلا شمعة)
    return {"account_id":account,"broker":broker,"symbol":symbol,"timeframe":"tick","sequence":"0","timestamp":1.0,"price":100}


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign",[
    spec(symbol="ES"),spec(account="B",broker="BR"),spec(account="A",broker="OTHER")])
async def test_22_513_other_symbol_or_scope_specs_do_not_size(foreign):
    atom,bus=await sizing_atom();await atom._on_specs(foreign);await atom._on_tick(tick())
    assert not bus.rows(M513.EVENT_OUT) and bus.rows(M513.EVENT_REJECTED)[-1]["reason"]=="SIZING_UNAVAILABLE_FOR_SYMBOL"


@pytest.mark.asyncio
async def test_23_513_stale_specs_do_not_size():
    atom,bus=await sizing_atom(max_age=1);await atom._on_specs(spec());key=("A","BR","NQ");atom._specs[key]["received_monotonic"]-=2
    await atom._on_tick(tick());assert bus.rows(M513.EVENT_REJECTED)[-1]["reason"]=="STALE_ACCOUNT_SYMBOL_SPECS"
    assert (await atom.health_check()).state==HealthState.DEGRADED


@pytest.mark.asyncio
async def test_24_513_valid_active_scope_is_healthy_and_sizes():
    atom,bus=await sizing_atom();await atom._on_specs(spec());await atom._on_tick(tick())
    assert bus.rows(M513.EVENT_OUT) and (await atom.health_check()).state==HealthState.HEALTHY


@pytest.mark.asyncio
async def test_25_618_publishes_spec_timestamps():
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/"b.db";connection=sqlite3.connect(path)
        connection.execute("CREATE TABLE symbol_specs_v2 (account_id TEXT,symbol TEXT,contract_size REAL,tick_value REAL,tick_size REAL)");connection.execute("INSERT INTO symbol_specs_v2 VALUES ('A','NQ',20,.5,.25)");connection.commit();connection.close()
        atom,bus=await start(M618,618,{"db_path":str(path),"table_name":"ticks_v2","spec_table":"symbol_specs_v2","spec_refresh_s":300,"poll_interval_s":1,"batch_limit":10,"delete_consumed":False,"max_age_s":30});await atom.stop();atom._context=AtomContext(618,{},Log(),bus.publish,bus.subscribe);await atom._refresh_specs()
        row=bus.rows(M618.EVENT_SPECS)[-1];assert row["published_at"] and row["symbols"][0]["spec_observed_monotonic"]>0


def test_26_sys_15min_is_registered_unused_without_consumer():
    source=(ROOT/"governance/checks/check_events.py").read_text()
    assert '"SYS_15MIN": "UNUSED_EVENT"' in source
    import yaml
    manifests=[yaml.safe_load(path.read_text()) for path in (ATOM_ROOT).glob("*/manifest.yaml")]
    assert any("SYS_15MIN" in (item.get("publishes") or []) for item in manifests)
    assert not any("SYS_15MIN" in (item.get("subscribes") or []) for item in manifests)

@pytest.mark.asyncio
async def test_27_late_cost_after_terminal_timeout_cannot_double_publish():
    with tempfile.TemporaryDirectory() as td:
        atom,bus=await outcome_atom(Path(td)/"j.db",.06)
        await atom._on_outcome(outcome("r1",commission=None,swap=0,fee=0));await asyncio.sleep(.14)
        assert len(bus.rows(M517.EVENT_OUT))==1
        await atom._on_outcome(outcome("r2",commission=-1,swap=0,fee=0))
        assert len(bus.rows(M517.EVENT_OUT))==1 and not atom._storage_error and not atom._pending
        await atom.stop()

@pytest.mark.asyncio
async def test_30_508_active_position_with_stale_specs_is_unknown():
    atom,bus=await exposure_atom();key=("A","BR","NQ");atom._specs[key]["received_monotonic"]-=120
    await atom._on_positions({"account_id":"A","broker":"BR","source":"609","positions":[{"account_id":"A","broker":"BR","ticket":1,"symbol":"NQ","volume":1,"current_price":100}],"usable_for_new_exposure":True,"usable_for_protection":True})
    row=bus.rows(M508.EVENT_OUT)[-1]
    assert row["status"]=="UNKNOWN_POSITION" and "STALE_ACCOUNT_SYMBOL_SPECS" in row["unknown_positions"][0]["unknown_reasons"]

@pytest.mark.asyncio
async def test_31_524_failed_operational_state_survives_restart_without_credit():
    owner,executor,bus=await extraction_pair();command=bus.rows(M579.EVENT_MANAGE)[-1]
    await executor._on_command_failed({**command,"reason":"BROKER_FAIL"});state=await owner.snapshot()
    restored,_=await start(M524,524,{"milestone_mult":2,"extract_fraction":.5,"full_targets":{},"default_full_target":0});await restored.restore(state)
    request=next(iter(restored._pending.values()))
    assert request["status"]=="FAILED" and restored._confirmations==0

@pytest.mark.asyncio
async def test_32_609_empty_position_table_is_not_financial_field_failure():
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/"p.db";make_positions_db(path);connection=sqlite3.connect(path);connection.execute("DELETE FROM positions_v2");connection.commit();connection.close()
        atom,bus=await position_reader(path);await atom._on_pulse({"official_time":100});await atom._read_once()
        row=bus.rows(M609.EVENT_OUT)[-1]
        assert row["open_count"]==0 and row["snapshot_status"]=="READY" and not row["missing_components"]
        assert (await atom.health_check()).state==HealthState.HEALTHY
