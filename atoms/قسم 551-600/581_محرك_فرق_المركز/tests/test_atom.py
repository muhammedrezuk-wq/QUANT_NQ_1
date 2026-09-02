import asyncio
import importlib.util
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[4];folder=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root));sys.path.insert(0,str(folder));spec=importlib.util.spec_from_file_location("_atom581",folder/"atom.py");mod=importlib.util.module_from_spec(spec);sys.modules["_atom581"]=mod;spec.loader.exec_module(mod)
BANDS={"0.0":0.0,"0.2":0.1,"0.4":0.25,"0.6":0.5,"0.9":1.0}
HEDGE={"0.0":1.0,"0.2":0.7,"0.4":0.4,"0.6":0.2,"0.9":0.0}
class L:
    def debug(self,*a,**k):pass
    def info(self,*a,**k):pass
    def warning(self,*a,**k):pass
    def error(self,*a,**k):pass
    def critical(self,*a,**k):pass
class B:
    def __init__(self):self.e=[];self.subs={}
    def subscribe(self,n,h):self.subs[n]=h
    async def publish(self,n,p):self.e.append((n,p))
    def c(self):return mod.AtomContext(581,{"bands":BANDS,"hedge_bands":HEDGE,"s_enter":0.20,"s_exit":0.15,"max_target_volume":20,"max_step_volume":1,"min_volume":0.01},L(),self.publish,self.subscribe)
async def legs(a,buy,sell):
    rows=[]
    if buy>0:rows.append({"account_id":"A","symbol":"GOLD","side":"BUY","ticket":1,"volume":buy,"entry_price":100})
    if sell>0:rows.append({"account_id":"A","symbol":"GOLD","side":"SELL","ticket":2,"volume":sell,"entry_price":100})
    await a._on_positions({"source":"t","account_id":"A","positions":rows})
    await a._on_ledger({"ledgers":[{"account_id":"A","symbol":"GOLD","risk_budget":50,"v_net":buy-sell}]})
def gate(payload,cycle):
    """حمولة decision.gate.passed كما ينشرها 467: الجانب + الهوية + معرفا القرار والطلب."""
    return dict(payload,cycle_id=cycle,decision_side=payload.get("direction"),approved=True,
                decision_id="d-"+cycle,gate_request_id="d-"+cycle+":req1")
async def decide(a, payload, approved=True, cycle="GOLD|60s|1"):
    """بند ب٤ (ق٩): مادة قرار الدخول تصل 581 من البوابة decision.gate.passed حصرًا.
    حكم 466 المباشر يبقى تأكيدًا إضافيًا (فيتو) لا مصدرًا."""
    payload = dict(payload, cycle_id=cycle)
    await a._on_verdict({"symbol": payload["symbol"], "cycle_id": cycle,
                         "metadata": {"approved": approved}})
    await a._on_gate_passed(gate(payload, cycle))


async def prime(b=None):
    b=b if b is not None else B();a=mod.Atom();await a.initialize(b.c());await a.start()
    a._risk_dial=lambda:100.0  # عزل عن سجل العيارات الحي — سلوك اليوم الكامل (v1.1: البدء 100)
    await a._on_portfolio({"account_id":"A","symbol":"GOLD","state":"ACTIVE","system_alive":True,"account_mode":"HEDGING"})
    await a._on_specs({"symbols":[{"account_id":"A","symbol":"GOLD","tick_value":1,"tick_size":1}]})
    await a._on_tick({"account_id":"A","symbol":"GOLD","price":100,"bid":99.5,"ask":100.5})
    await a._on_dial({"profiles":[{"account_id":"A","symbol":"GOLD","stop_distance_frac":0.05}]})
    await a._on_ledger({"ledgers":[{"account_id":"A","symbol":"GOLD","risk_budget":50,"v_net":0}]})
    return a,b


async def main():
    b=B();a,_=await prime(b)
    # ب٤: الاشتراكات — البوابة بأحداثها الثلاثة مصدر القرار، و458 سياق، و466 تأكيد.
    for name in (mod.EVENT_GATE, mod.EVENT_GATE_BLOCKED, mod.EVENT_GATE_RECORDED,
                 mod.EVENT_CONTEXT, mod.EVENT_VERDICT):
        assert name in b.subs, name
    # Owner's final contract 2026-08-13. capacity = 50/(100*0.05*1) = 10.
    # S=0.8 -> E=0.5 -> gross=5 ; H=0.2 -> net = 5*0.8 = 4 ; legs 4.5/0.5.
    await decide(a, {"account_id":"A","symbol":"GOLD","direction":"buy","score":80,"strength":0.8});p=b.e[-1][1]
    assert p["action"]=="ADD" and p["target_gross"]==5.0 and p["target_net"]==4.0,(p["action"],p["target_gross"],p["target_net"])
    assert p["target_buy"]==4.5 and p["target_sell"]==0.5,(p["target_buy"],p["target_sell"])
    assert p["exposure_fraction"]==0.5 and p["hedge_fraction"]==0.2,(p["exposure_fraction"],p["hedge_fraction"])
    assert p["target_net"]==p["target_buy"]-p["target_sell"] and p["target_gross"]==p["target_buy"]+p["target_sell"]
    # قبول ق٩ (٥): طلب التنفيذ الصادر من 581 يحمل معرف القرار ومعرف طلب البوابة.
    assert p["decision_id"]=="d-GOLD|60s|1",p["decision_id"]
    assert p["gate_request_id"]=="d-GOLD|60s|1:req1",p["gate_request_id"]

    # His result column, verbatim: net as a share of capacity per band.
    for s,want in ((0.2,0.03),(0.4,0.15),(0.6,0.40),(0.9,1.00)):
        b2=B();a2,_=await prime(b2)
        await decide(a2, {"account_id":"A","symbol":"GOLD","direction":"buy","score":100,"strength":s})
        q=b2.e[-1][1];assert abs(abs(q["target_net"])/10.0-want)<1e-9,(s,q["target_net"],want)

    # Hedging thins a weak direction and never cancels a strong one:
    # H falls as S rises, |net| never falls while S rises.
    prev_net=-1.0;prev_h=2.0
    for s in (0.2,0.4,0.6,0.9,1.0):
        await decide(a, {"account_id":"A","symbol":"GOLD","direction":"buy","score":100,"strength":s});q=b.e[-1][1]
        assert abs(q["target_net"])>=prev_net-1e-9,(s,q["target_net"],prev_net)
        assert q["hedge_fraction"]<=prev_h+1e-9,(s,q["hedge_fraction"],prev_h)
        prev_net=abs(q["target_net"]);prev_h=q["hedge_fraction"]
    assert q["hedge_fraction"]==0.0 and abs(q["target_net"]-q["target_gross"])<1e-9
    assert q["target_sell"]==0.0 and q["target_buy"]>0,(q["target_buy"],q["target_sell"])

    # S below entry: net zero, but the GROSS IS KEPT -- neutral inside the
    # market, never flat and never CLOSE_ALL.
    b3=B();a3,_=await prime(b3)
    await legs(a3,3.0,3.0)
    await decide(a3, {"account_id":"A","symbol":"GOLD","direction":"buy","score":0,"strength":0.10});r=b3.e[-1][1]
    assert r["target_net"]==0.0 and r["target_gross"]==6.0,(r["target_net"],r["target_gross"])
    assert r["target_buy"]==3.0 and r["target_sell"]==3.0 and r["action"]=="HOLD",(r["target_buy"],r["target_sell"],r["action"])
    assert r["reason"]==mod.REASON_NEUTRAL_KEEP,r["reason"]

    # قبول ق٩ (٢) عند 581: قرار محجوب يصل عبر decision.gate.blocked لا يفتح شيئًا،
    # وقرار قوي وصل حكمه لدورة أخرى فقط (بلا بوابة) يبقى مقفولًا FILTER_PENDING.
    b4=B();a4,_=await prime(b4)
    await a4._on_verdict({"symbol":"GOLD","cycle_id":"GOLD|60s|9","metadata":{"approved":False}})
    await a4._on_gate_blocked({"account_id":"A","symbol":"GOLD","cycle_id":"GOLD|60s|9",
                               "decision_side":"buy","score":100,"strength":0.9})
    v=b4.e[-1][1]
    assert v["filter_verdict"]==mod.FILTER_BLOCKED,(v["filter_verdict"],)
    assert v["target_net"]==0.0 and v["delta_buy"]==0.0 and v["delta_sell"]==0.0,v
    assert v["exposure_fraction"]==0.0 and v["hedge_fraction"]==1.0,v
    assert v["strength"]==0.9 and v["direction"]=="wait",(v["strength"],v["direction"])
    b5=B();a5,_=await prime(b5)
    await a5._on_verdict({"symbol":"GOLD","cycle_id":"GOLD|60s|OTHER","metadata":{"approved":True}})
    await a5._on_context({"account_id":"A","symbol":"GOLD","cycle_id":"GOLD|60s|9",
                          "direction":"buy","score":100,"strength":0.9})
    w=b5.e[-1][1]
    assert w["filter_verdict"]==mod.FILTER_PENDING,(w["filter_verdict"],)
    assert w["target_net"]==0.0 and w["delta_buy"]==0.0 and w["delta_sell"]==0.0,w
    assert w["strength"]==0.9 and w["direction"]=="wait",(w["strength"],w["direction"])

    # تأكيد 466 بشكل العقد الجديد (approved أعلى الحمولة بلا metadata): لا فيتو كاذب —
    # الحكم الموافق بعد البوابة يبقي FILTER_PASSED (علة صادتها سلسلة الإثبات الموصولة).
    b45=B();a45,_=await prime(b45)
    await a45._on_gate_passed(gate({"account_id":"A","symbol":"GOLD","direction":"buy",
                                    "score":80,"strength":0.8},"GOLD|60s|1"))
    await a45._on_verdict({"symbol":"GOLD","account_id":"A","cycle_id":"GOLD|60s|1","approved":True})
    c45=b45.e[-1][1]
    assert c45["filter_verdict"]==mod.FILTER_PASSED and c45["target_net"]==4.0,(c45["filter_verdict"],c45["target_net"])
    # والفيتو حي: حكم لاحق غير موافق بنفس النطاق والدورة يحجب رغم عبور البوابة.
    await a45._on_verdict({"symbol":"GOLD","account_id":"A","cycle_id":"GOLD|60s|1","approved":False})
    c46=b45.e[-1][1]
    assert c46["filter_verdict"]==mod.FILTER_BLOCKED and c46["target_net"]==0.0,(c46["filter_verdict"],c46["target_net"])

    # النشر المتدرج (466 القائم بلا account_id): سياق 458 المعنون بالحساب لا يحجب
    # قرار بوابة بلا حساب لنفس الدورة — الجانب يدخل من البوابة رغم فرق النطاق.
    b47=B();a47,_=await prime(b47)
    await a47._on_context({"account_id":"A","symbol":"GOLD","cycle_id":"GOLD|60s|1",
                           "direction":"buy","score":80,"strength":0.8})
    await a47._on_verdict({"symbol":"GOLD","cycle_id":"GOLD|60s|1",
                           "metadata":{"approved":True}})
    await a47._on_gate_passed({"symbol":"GOLD","cycle_id":"GOLD|60s|1","decision_side":"buy",
                               "approved":True,"score":80,"strength":0.8,
                               "decision_id":"L1","gate_request_id":"L1:req1"})
    c47=b47.e[-1][1]
    assert c47["held_direction"]=="buy" and c47["target_net"]==4.0,(c47["held_direction"],c47["target_net"])
    assert c47["gate_request_id"]=="L1:req1",c47.get("gate_request_id")

    # ب٤ بحده الحاد: مخرج 458 المباشر صار سياقًا بلا جانب — حتى مع حكم مطابق
    # موافق، قرار اتجاهي من 458 لا يعطي 581 اتجاهًا؛ الجانب من البوابة وحدها.
    b6=B();a6,_=await prime(b6)
    await a6._on_verdict({"symbol":"GOLD","cycle_id":"GOLD|60s|9","metadata":{"approved":True}})
    await a6._on_context({"account_id":"A","symbol":"GOLD","cycle_id":"GOLD|60s|9",
                          "direction":"buy","score":100,"strength":0.9})
    x=b6.e[-1][1]
    assert x["filter_verdict"]==mod.FILTER_PASSED,(x["filter_verdict"],)
    assert x["direction"]=="wait" and x["held_direction"]=="wait",(x["direction"],x["held_direction"])
    assert x["target_net"]==0.0,x["target_net"]
    # ثم تصل البوابة بنفس الدورة → الجانب يدخل ولا يمحوه سياق لاحق من 458.
    await a6._on_gate_passed(gate({"account_id":"A","symbol":"GOLD","direction":"buy",
                                   "score":100,"strength":0.9},"GOLD|60s|9"))
    y=b6.e[-1][1]
    assert y["held_direction"]=="buy" and y["target_net"]>0,(y["held_direction"],y["target_net"])
    await a6._on_context({"account_id":"A","symbol":"GOLD","cycle_id":"GOLD|60s|9",
                          "direction":"sell","score":100,"strength":0.9})
    z=b6.e[-1][1]
    assert z["held_direction"]=="buy" and z["gate_request_id"]=="d-GOLD|60s|9:req1",(z["held_direction"],z.get("gate_request_id"))

    # قبول ق٩ (٣) عند 581: انتظار عبر decision.gate.recorded = حالة قرار لا أمر.
    b7=B();a7,_=await prime(b7)
    await a7._on_gate_recorded({"account_id":"A","symbol":"GOLD","cycle_id":"GOLD|60s|2",
                                "decision_side":"wait","score":0,"strength":0.1})
    t=b7.e[-1][1]
    assert t["direction"]=="wait" and t["target_net"]==0.0 and t["delta_buy"]==0.0 and t["delta_sell"]==0.0,t
    print("581 gate-contract tests passed (E ladder + falling H + gross kept at neutral"
          " + gate.passed sole entry source + 458 demoted to context + ids on every target)")
if __name__=="__main__":asyncio.run(main())
