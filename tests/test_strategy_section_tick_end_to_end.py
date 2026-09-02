import pytest
from core.contracts.atom import AtomContext
from core.event_bus import EventBus
from tests.learning_test_support import Logger,load_atom,manifest_config
@pytest.mark.asyncio
async def test_622_to_112_to_descriptive_strategy_section():
 bus=EventBus();atoms=[]
 for atom_id in (613,112,400,401,402,404,405,406,407,408,409,410,411,412,413):
  m=load_atom(atom_id);a=m.Atom();await a.initialize(AtomContext(atom_id,manifest_config(atom_id),Logger(),bus.publish,bus.subscribe));atoms.append(a)
 for atom in atoms:await atom.start()
 rows=[]
 async def capture(p):rows.append(dict(p))
 bus.subscribe("strategy.section.live",capture,subscriber="strategy-e2e")
 for i in range(120):
  price=100+i*.03
  await bus.publish("feed.ctrader.tick",{"provider":"CTRADER","account_id":"A","broker":"Raw Trading Ltd","symbol":"NQ","bid":price-.01,"ask":price+.01,"price":price,"volume":10+i,"timestamp":1800000000+i/1000,"exchange_timestamp":1800000000+i/1000},publisher="622")
 # (nq seal 2026-08-25: EventBus 1.18.0 enqueues to per-handler mailboxes;
 # drain the bus so the 613->112->400.. cascade finishes before asserting)
 assert await bus.drain(timeout_s=30.0) is True
 assert rows
 last=rows[-1];assert last["timeframe"]=="tick" and last["complete"] is True
 assert len(last["results"])==12
 for key in ("direction","strength","confidence","current_depth","required_depth","weight","ratio","state"):
  assert key in last
 assert str(last["signal"]).upper() not in ("BUY","SELL","ENTRY","EXIT","CLOSE")
 assert bus.stats()["published"]["market.tick.validated"]==120
