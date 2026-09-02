import pytest
from shared.strategy_contract import ALL_IDS,DIRECTIONAL_IDS,EQUAL_WEIGHT
from tests.learning_test_support import make_atom,manifest_config
@pytest.mark.asyncio
async def test_weighted_descriptive_aggregate_has_no_decision():
 module,atom,bus=await make_atom(413,manifest_config(413))
 for i,sid in enumerate(ALL_IDS):
  directional=sid in DIRECTIONAL_IDS
  await atom._on_component({"account_id":"A","broker":"B","symbol":"NQ","cycle_id":"t1","period_start":"t1","strategy_id":sid,"id":sid,"direction":80 if directional and i<5 else -40 if directional else 0,"strength":80,"confidence":80,"current_depth":90,"weight":EQUAL_WEIGHT if directional else 0,"weight_applied":EQUAL_WEIGHT if directional else 0,"ready":True,"context_factor":1.0})
 card=bus.payloads(module.EVENT_OUT)[-1]
 assert card["ready"] is True and card["direction"]>0
 assert card["signal"]=="positive_strategic_lean"
 assert "BUY" not in str(card).upper() and "SELL" not in str(card).upper()
 assert card["active_weight"]>0 and card["weight"]==0

@pytest.mark.asyncio
async def test_undefined_confidence_when_no_ready_contributor():
 module,atom,bus=await make_atom(413,manifest_config(413))
 for sid in ALL_IDS:
  directional=sid in DIRECTIONAL_IDS
  await atom._on_component({"account_id":"A","broker":"B","symbol":"NQ","cycle_id":"t2","period_start":"t2","strategy_id":sid,"id":sid,"direction":0,"strength":0,"confidence":80,"current_depth":90,"weight":EQUAL_WEIGHT if directional else 0,"weight_applied":EQUAL_WEIGHT if directional else 0,"ready":False,"context_factor":1.0})
 card=bus.payloads(module.EVENT_OUT)[-1]
 assert card["confidence"] is None and card["confidence_defined"] is False
 assert card["ready"] is False and "NO_READY_CONTRIBUTOR" in card["warnings"]
 assert "readiness_pct" in card
