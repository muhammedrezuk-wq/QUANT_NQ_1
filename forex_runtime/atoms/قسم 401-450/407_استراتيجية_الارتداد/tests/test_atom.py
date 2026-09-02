import json
import pytest
from tests.learning_test_support import make_atom,manifest_config,validated_tick
@pytest.mark.asyncio
async def test_descriptive_tick_contract():
 module,atom,bus=await make_atom(407,manifest_config(407))
 for i in range(80):
  price=100.0+i*.03
  await atom._on_tick(validated_tick(i,price=price))
 rows=bus.payloads("strategy.pullback.state");assert len(rows)==80
 card=rows[-1];assert card["timeframe"]=="tick" and card["analysis_mode"]=="live_tick"
 assert str(card["signal"]).upper() not in ("BUY","SELL","ENTRY","EXIT","CLOSE")
 for key in ("direction","strength","confidence","current_depth","required_depth","weight","weight_applied","ratio","ready","state"):
  assert key in card
 assert -100<=card["direction"]<=100 and 0<=card["confidence"]<=100
 json.dumps(await atom.snapshot(),allow_nan=False)
