import asyncio
import importlib.util
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[4]; folder=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root)); spec=importlib.util.spec_from_file_location("_atom521_work",folder/"atom.py")
mod=importlib.util.module_from_spec(spec); sys.modules["_atom521_work"]=mod; spec.loader.exec_module(mod)

# ٢٠٢٦-٠٩-٠١: الذرّة صارت تقرأ «الآن» من السلطة الزمنيّة لا من حمولة النبضة.
# تُحقن هنا ساعةٌ مضبوطة في مساحة أسماء هذه الذرّة وحدها، فتبقى الطوابع
# صغيرة مقروءة (11 · 12 …) ولا تُمَسّ الساعة العامّة.
class _Clock:
    value = 12.0
    def now(self): return self.value
    def mono(self): return self.value
    def quality(self): return "SYNCED"
    def state(self): return {"quality": "SYNCED"}
CLOCK = _Clock(); mod.clock = CLOCK
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
    def ctx(self): return mod.AtomContext(521,{"symbols":[],"max_data_age_s":5,"fallback_max_age_s":30,"max_sane_spread":2,"max_tick_jump":10,"min_dwell_s":0},Log(),self.publish,self.subscribe)
async def main():
    b=Bus(); a=mod.Atom(); await a.initialize(b.ctx()); await a.start()
    await a._on_primary({"symbol":"NQ","bid":99.5,"ask":100.5,"price":100,"exchange_timestamp":10}); await a._on_pulse({"official_time":10})
    assert a.state("NQ")["state"]=="HEALTHY"
    await a._on_primary({"symbol":"NQ","bid":149.5,"ask":150.5,"price":150,"exchange_timestamp":11})
    assert a.state("NQ")["primary"]["reason"]=="TICK_JUMP"
    print("521 P1 tests passed")
# ٢٠٢٦-٠٩-٠١: كان سكربتًا بلا دالّة `test_`، فيجمع منه pytest صفرًا ويعلن
# «no tests ran» — ذرّةٌ حارسةٌ للمرجع بلا تغطية فعليّة وهي تبدو مغطّاة.
def test_521_reference_health(): asyncio.run(main())

if __name__=="__main__": asyncio.run(main())
