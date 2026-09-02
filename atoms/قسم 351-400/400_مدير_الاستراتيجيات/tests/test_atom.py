import pytest
from shared.strategy_contract import ALL_IDS,DIRECTIONAL_IDS,EQUAL_WEIGHT
from shared.tick_contract import as_validated_tick
from tests.learning_test_support import make_atom,manifest_config,validated_tick
@pytest.mark.asyncio
async def test_manager_publishes_one_descriptive_section_card():
 module,atom,bus=await make_atom(400,manifest_config(400));canonical={"account_id":"A","broker":"Raw Trading Ltd","symbol":"NQ","cycle_id":"A|Raw Trading Ltd|NQ|60s|1000","period_start":1000.0}
 for sid in ALL_IDS:
  directional=sid in DIRECTIONAL_IDS
  await atom._on_component({"account_id":"A","broker":"Raw Trading Ltd","symbol":"NQ","cycle_id":canonical["cycle_id"],"period_start":canonical["period_start"],"strategy_id":sid,"id":sid,"direction":60 if directional else 0,"strength":80,"confidence":80,"current_depth":90,"weight":EQUAL_WEIGHT if directional else 0,"weight_applied":EQUAL_WEIGHT if directional else 0,"ready":True})
 await atom._on_component({"account_id":"A","broker":"Raw Trading Ltd","symbol":"NQ","cycle_id":canonical["cycle_id"],"period_start":canonical["period_start"],"strategy_id":"strategy_aggregate","id":"strategy_aggregate","direction":60,"strength":80,"confidence":80,"current_depth":90,"ready":True,"active_weight":100,"available_weight":100,"missing_weight":0,"context_factor":1})
 card=bus.payloads(module.EVENT_LIVE)[-1]
 assert card["complete"] is True and card["ready"] is True
 assert card["signal"]=="positive_strategic_lean"
 assert "BUY" not in str(card).upper() and "SELL" not in str(card).upper()
 assert card["weight"]==pytest.approx(100/6,abs=1e-5)

@pytest.mark.asyncio
async def test_undefined_confidence_passes_through_as_none():
 module,atom,bus=await make_atom(400,manifest_config(400));canonical={"account_id":"A","broker":"Raw Trading Ltd","symbol":"NQ","cycle_id":"A|Raw Trading Ltd|NQ|60s|2000","period_start":2000.0}
 for sid in ALL_IDS:
  directional=sid in DIRECTIONAL_IDS
  await atom._on_component({"account_id":"A","broker":"Raw Trading Ltd","symbol":"NQ","cycle_id":canonical["cycle_id"],"period_start":canonical["period_start"],"strategy_id":sid,"id":sid,"direction":0,"strength":0,"confidence":80,"current_depth":90,"weight":EQUAL_WEIGHT if directional else 0,"weight_applied":EQUAL_WEIGHT if directional else 0,"ready":False})
 await atom._on_component({"account_id":"A","broker":"Raw Trading Ltd","symbol":"NQ","cycle_id":canonical["cycle_id"],"period_start":canonical["period_start"],"strategy_id":"strategy_aggregate","id":"strategy_aggregate","direction":0,"strength":0,"confidence":None,"current_depth":90,"ready":False,"active_weight":0,"available_weight":100,"missing_weight":100,"context_factor":1})
 card=bus.payloads(module.EVENT_LIVE)[-1]
 assert card["confidence"] is None and card["confidence_defined"] is False
 assert card["ready"] is False and "CONFIDENCE_UNDEFINED" in card["warnings"]
 assert "readiness_pct" in card

@pytest.mark.asyncio
async def test_units_panel_self_declared():
 module,atom,bus=await make_atom(400,manifest_config(400))
 canonical={"account_id":"A","broker":"Raw Trading Ltd","symbol":"NQ","cycle_id":"A|Raw Trading Ltd|NQ|60s|3000","period_start":3000.0}
 for sid in ALL_IDS:
  directional=sid in DIRECTIONAL_IDS
  await atom._on_component({"account_id":"A","broker":"Raw Trading Ltd","symbol":"NQ","cycle_id":canonical["cycle_id"],"period_start":3000.0,"timeframe":"60s","strategy_id":sid,"id":sid,"direction":40 if directional else 0,"confidence":75,"current_depth":80,"weight":EQUAL_WEIGHT if directional else 0,"ready":True})
 panels=bus.payloads(module.EVENT_PANEL)
 assert panels
 p1=panels[-1]
 trend=next(r for r in p1["units"] if r["id"]==ALL_IDS[0])
 assert trend["present"] is True and trend["deliveries"]==1 and trend["confidence"]==75
 assert trend["confidence_defined"] is True

@pytest.mark.asyncio
async def test_timeout_forwards_partial_declared():
 import clock as _clock
 module,atom,bus=await make_atom(400,manifest_config(400))
 await atom._on_component({"account_id":"A","broker":"Raw Trading Ltd","symbol":"NQ","cycle_id":"A|Raw Trading Ltd|NQ|60s|4000","period_start":4000.0,"timeframe":"60s","strategy_id":ALL_IDS[0],"id":ALL_IDS[0],"direction":10,"confidence":70,"ready":True})
 assert atom._cycles
 # دفع الوقت عبر SYS_SECOND بالمهل المضمّنة في الإعداد
 cfg_timeout=float(manifest_config(400).get("timeout_seconds",5))
 await atom._on_time({"official_time":0})   # يحدّث _now عبر clock داخليًا — نستخدم المهلة الحقيقية
 import asyncio as _aio
 await _aio.sleep(0)
 # نستدعي المهلة عبر تجاوز زمن: نعدّل open_time يدويًا لقيمة قديمة موجبة
 # (_on_time يستخدم clock.now() الحقيقي — قيمة قديمة صغيرة تتجاوز المهلة حتمًا)
 for cyc in atom._cycles.values(): cyc["open_time"]=1.0
 await atom._on_time({"official_time":0})
 outs=bus.payloads(module.EVENT_OUT)
 assert outs and outs[-1]["complete"] is False and outs[-1]["missing"]
