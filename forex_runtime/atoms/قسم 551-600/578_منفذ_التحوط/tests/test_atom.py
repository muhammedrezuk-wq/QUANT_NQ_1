import asyncio
import importlib.util
import sys
import tempfile
import time
from pathlib import Path

root=Path(__file__).resolve().parents[3]; folder=Path(__file__).resolve().parents[1]
# v5.3.0: مخزن الأزواج الدائم معزول لكل عملية فحص — لا لمس لمخزن الإنتاج.
_STORE_DIR = tempfile.TemporaryDirectory()
_STORE_SEQ = [0]
sys.path.insert(0,str(root)); sys.path.insert(0,str(folder)); spec=importlib.util.spec_from_file_location("_atom578_final",folder/"atom.py"); mod=importlib.util.module_from_spec(spec); sys.modules["_atom578_final"]=mod; spec.loader.exec_module(mod)
class L:
    def debug(self,*a,**k):pass
    def info(self,*a,**k):pass
    def warning(self,*a,**k):pass
    def error(self,*a,**k):pass
    def critical(self,*a,**k):pass
class B:
    def __init__(self):self.e=[]
    def subscribe(self,*a):pass
    async def publish(self,n,p):self.e.append((n,p))
    def c(self,cfg=None):
        cfg=dict(cfg or {"lot_step":.01,"min_volume":.01,"reward_risk":2,"max_attempts":2,"catastrophe_stop_multiple":3.0,"fallback_stop_frac":.02})
        if "pair_store_path" not in cfg:
            _STORE_SEQ[0]+=1
            cfg["pair_store_path"]=str(Path(_STORE_DIR.name)/f"pairs{_STORE_SEQ[0]}.db")
        return mod.AtomContext(578,cfg,L(),self.publish,self.subscribe)
def ev(b,n):return [p for x,p in b.e if x==n]
async def new(clock=1000.0):
    b=B();a=mod.Atom();await a.initialize(b.c());await a.start()
    # البند ٦٣ (أ-٢): كل هدف حقيقيّ يصل من 583 بلقطة، والزمن الرسميّ يصل من
    # الناقل. بلا أيّهما يُرفض الإرسال عمدًا — العدّاد وحده لا يميّز نسخة عن
    # التي قبلها لأنه يعود صفرًا مع كل ترقية حيّة.
    if clock is not None: await a._on_external({"official_time":clock})
    await a._on_external({"account_id": "A", "broker": "BR", "trade_allowed": True})
    await a._on_positions({"account_id":"A","broker":"BR","positions": [],
                           "usable_for_new_exposure":True,"usable_for_protection":True})
    await a._on_quality({"account_id": "A", "symbol": "X", "status": "READY"})
    await a._on_divergence({"account_id": "A", "symbol": "X", "status": "SYNCED"})
    # v5.2.0: الدخول الجديد يشترط هوية قرار موثّقة عند البوابة (467) —
    # المحكّ يسجّل قرارًا عابرًا ويحمّله على كل هدف، مطابقًا العقد الحيّ.
    await a._on_gate_passed({"decision_id": "D-test", "gate_request_id": "G-test"})
    original_target = a._on_target
    async def compatible_target(payload):
        payload = dict(payload)
        payload.setdefault("usable_for_new_exposure", True)
        payload.setdefault("usable_for_protection", True)
        payload.setdefault("decision_id", "D-test")
        payload.setdefault("gate_request_id", "G-test")
        await original_target(payload)
    a._on_target = compatible_target
    return a,b
async def main():
    a,b=await new()
    # عقد ٤-١٠ (حكم المالك ٢٠٢٦-٠٨-١٥): لا إرسال قبل وصول ساعة حيّة، ولقطة
    # بلا `produced_at` تُرفض fail-closed لأنّها لا تُثبت أنّها ليست إعادة بثّ
    # لما قبل انهيار. فالاختبار يخاطب العقد الجديد: نبضة رسميّة أوّلًا، ثمّ
    # لقطات مختومة. `new()` تمنح الساعة الرسميّة `1000.0` وعندها تُثبَّت علامة
    # الاستئناف مرّة واحدة، فكل ختم هنا يجب أن يكون ≥ `1000.0`. لم يُمَسّ 578
    # ولا 583 — الترحيل في المحكّ وحده.
    await a._on_target({"account_id":"A","symbol":"X","status":"READY","action":"ADD","target_net":2,"current_net":0,"delta_net":1,"reference_price":100,"stop_distance_frac":.05,"produced_at":1000.0,"producer_epoch":1000.0,"sequence":1})
    req=ev(b,mod.EVENT_REQUEST);assert len(req)==1 and req[0]["side"]=="BUY" and req[0]["volume"]==1
    # حكم المالك ٢٠٢٦-٠٨-١٣ (البند ٢، الخيار ج): الستوب الفيزيائيّ يعود عند
    # الفتح ملاذًا أخيرًا واسعًا — الميزانيّة تبقى المدير، والهدف وحده مستثنى
    # لأنّ المركز الدائم لا يُغلق بهدف. المسافة = 3 × مسافة الميزانيّة العاملة.
    assert req[0]["take_profit"] is None, "لا هدف على رجل دائمة أبدًا"
    assert req[0]["stop_loss"]==85.0, "ستوب شراء = 100 − (100×0.05×3)"
    assert req[0]["stop_is_last_resort"] is True and req[0]["catastrophe_multiple"]==3.0
    assert req[0]["stop_source"]=="CATASTROPHE_FROM_CAPACITY"
    assert req[0]["protection_mode"]=="PERPETUAL_BUDGET" and req[0]["origin"]=="perpetual-delta"
    assert req[0]["asset_stop_distance"]==5.0, "مسافة الميزانيّة العاملة تبقى معلومةً كما هي"
    assert req[0]["catastrophe_distance"]==15.0, "ملاذ أخير أوسع من الميزانيّة دائمًا"
    assert req[0]["stop_loss"] < req[0]["reference_price"] - req[0]["asset_stop_distance"], \
        "الميزانيّة لازم تضرب قبل الوسيط"
    await a._on_target({"account_id":"A","symbol":"X","status":"READY","action":"ADD","target_net":2,"current_net":0,"delta_net":1,"reference_price":100,"stop_distance_frac":.05,"produced_at":1000.0,"producer_epoch":1000.0,"sequence":1})
    assert len(ev(b,mod.EVENT_REQUEST))==1
    # تغيّر السعر/الهدف وحده لا يعيد الطلب قبل resend_hold_s ما دامت صورة الوسيط نفسها.
    changed=dict({"account_id":"A","symbol":"X","status":"READY","action":"ADD","target_net":3,"current_net":0,"delta_net":2,"reference_price":101,"stop_distance_frac":.05,"produced_at":1000.0,"producer_epoch":1000.0,"sequence":2})
    await a._on_target(changed)
    assert len(ev(b,mod.EVENT_REQUEST))==1 and a._flood_guard.suppressed >= 1
    await a._on_external({"account_id":"A","trade_allowed":False})
    await a._on_target(dict(changed, target_net=4, delta_net=3))
    assert len(ev(b,mod.EVENT_REQUEST))==1
    print("OK — محرك الفرق يرسل الفرق فقط وبستوب/هدف صحيحين وحارس الفيضان")

    a4,b4=await new(); await a4._on_external({"official_time":100.0})
    moving={"account_id":"A","symbol":"X","status":"READY","action":"ADD","target_buy":1,"target_sell":0,"target_net":1,"current_buy":0,"current_sell":0,"delta_buy":1,"delta_sell":0,"delta_net":1,"reference_price":100,"stop_distance_frac":.05,"produced_at":1100.0,"producer_epoch":1100.0,"sequence":1,"current_legs":[{"ticket":7,"side":"BUY","volume":1,"current_price":100}]}
    await a4._on_target(moving); await a4._on_external({"official_time":101.0}); await a4._on_target({**moving,"target_buy":1.1,"reference_price":101,"produced_at":1101.0,"sequence":2,"current_legs":[{"ticket":7,"side":"BUY","volume":1,"current_price":101}]})
    assert len(ev(b4,mod.EVENT_REQUEST))==1 and a4._flood_guard.suppressed==1
    print("OK — تغيّر سعر الرجل لا يغيّر صورة المركز ولا يعيد الإرسال")

    a2,b2=await new();base={"request_id":"p-buy-a1","account_id":"A","symbol":"X","side":"BUY","volume":1,"reference_price":100,"pair_id":"p","leg_role":"BUY","attempt":1,"pair_required":True,"pair_volume":1,"protection_mode":"NEUTRAL_HEDGE"};sell=dict(base,request_id="p-sell-a1",side="SELL",leg_role="SELL");await a2._on_requested(base);await a2._on_requested(sell);await a2._on_rejected({"request_id":"p-sell-a1","reason":"BROKER_REJECT"});retry=ev(b2,mod.EVENT_REQUEST);assert len(retry)==1 and retry[0]["request_id"]=="p-sell-a2";print("OK — فشل SELL يعيد SELL وحده بنفس pair_id")
    await a2._on_trade({"event_type":"OPENED","request_id":"p-buy-a1","ticket":1,"account_id":"A","symbol":"X"});await a2._on_trade({"event_type":"OPENED","request_id":"p-sell-a2","ticket":2,"account_id":"A","symbol":"X"});assert ev(b2,mod.EVENT_PAIR_STATE)[-1]["status"]=="COMPLETE";print("OK — نجاح الرجلين → COMPLETE")

    a3,b3=await new();base={"request_id":"p-buy-a1","account_id":"A","symbol":"X","side":"BUY","volume":1,"reference_price":100,"pair_id":"p","leg_role":"BUY","attempt":1,"pair_required":True,"pair_volume":1,"protection_mode":"NEUTRAL_HEDGE"};await a3._on_requested(base);await a3._on_rejected({"request_id":"p-buy-a1","reason":"NO_STOP"});retry=ev(b3,mod.EVENT_REQUEST)[-1];await a3._on_requested(retry);await a3._on_rejected({"request_id":"p-buy-a2","reason":"NO_STOP"});assert ev(b3,mod.EVENT_ESCALATION) and ev(b3,mod.EVENT_ASSET_COMMAND)[-1]["command"]=="pause";print("OK — نفاد المحاولات → تصعيد وتجميد الأصل")

    a5,b5=await new(); await a5._on_external({"official_time":200.0})
    t={"account_id":"A","symbol":"X","status":"READY","action":"ADD","target_buy":1,"target_sell":0,"target_net":1,"current_buy":0,"current_sell":0,"delta_buy":1,"delta_sell":0,"delta_net":1,"reference_price":100,"stop_distance_frac":.05,"produced_at":1200.0,"producer_epoch":1200.0,"sequence":1}
    await a5._on_target(t); assert len(ev(b5,mod.EVENT_REQUEST))==1
    first_request_id=ev(b5,mod.EVENT_REQUEST)[-1]["request_id"]
    await a5._on_send_failure({"account_id":"A","symbol":"X","request_id":first_request_id,"reason":"OWNER_HALT"})
    await a5._on_external({"official_time":203.0})
    await a5._on_target({**t,"target_buy":2,"delta_buy":2,"target_net":2,"delta_net":2})
    assert len(ev(b5,mod.EVENT_REQUEST))==1, "بعد فشل الأمر لا إرسال قبل انقضاء التهدئة"
    await a5._on_trade({"event_type":"OPENED","account_id":"A","symbol":"X","request_id":first_request_id,"ticket":99})
    await a5._on_target({**t,"target_buy":3,"delta_buy":3,"target_net":3,"delta_net":3})
    assert len(ev(b5,mod.EVENT_REQUEST))==2, "النجاح يصفّر التهدئة ويسمح بالإرسال"
    print("OK — فشل الأوامر يفعّل تهدئة أُسّية والنجاح يصفّرها")

    a6,b6=await new()
    # الساعة واللقطة المختومة تُمنَحان هنا عمداً حتى يكون المانع الوحيد الباقي هو
    # `trade_allowed=0` — وإلّا لمرّ الاختبار بسبب حاجز ٤-١٠ وصار أجوف.
    await a6._on_external({"account_id":"A","trade_allowed":0})
    reduce={"account_id":"A","symbol":"X","status":"READY","action":"REDUCE","target_buy":0,"target_sell":0,"target_net":0,"current_buy":1,"current_sell":0,"delta_buy":-1,"delta_sell":0,"delta_net":-1,"reference_price":100,"stop_distance_frac":.05,"produced_at":1400.0,"producer_epoch":1400.0,"sequence":1,"current_legs":[{"ticket":9,"side":"BUY","volume":1,"current_price":100}]}
    await a6._on_target(reduce)
    assert len(ev(b6,mod.EVENT_REQUEST))==0, "إيقاف التداول يمنع حتى أوامر التخفيض (وقيمة 0/1 تُفهم)"
    print("OK — إيقاف التداول يمنع الكل والتقاطه يقبل 0/1")

    a7,b7=await new(); await a7._on_external({"official_time":300.0})
    await a7._on_divergence({"account_id":"A","symbol":"X","status":"DIVERGED"})
    sell_open={"account_id":"A","symbol":"X","status":"READY","action":"ADD","target_buy":0,"target_sell":1,"target_net":-1,"current_buy":0,"current_sell":0,"delta_buy":0,"delta_sell":1,"delta_net":-1,"reference_price":100,"stop_distance_frac":.05,"produced_at":1300.0,"producer_epoch":1300.0,"sequence":1}
    await a7._on_target(sell_open)
    assert len(ev(b7,mod.EVENT_REQUEST))==0, "فتح البيع يُحجب تحت الانحراف (ثغرة المرحلة ٤ مقفولة)"
    close_only={"account_id":"A","symbol":"X","status":"READY","action":"REDUCE","target_buy":0,"target_sell":0,"target_net":0,"current_buy":1,"current_sell":0,"delta_buy":-1,"delta_sell":0,"delta_net":-1,"reference_price":100,"stop_distance_frac":.05,"produced_at":1300.0,"producer_epoch":1300.0,"sequence":2,"current_legs":[{"ticket":5,"side":"BUY","volume":1,"current_price":100}]}
    await a7._on_target(close_only)
    reqs=ev(b7,mod.EVENT_REQUEST)
    assert len(reqs)==1 and reqs[0]["action"]=="CLOSE_PARTIAL", "الإغلاق يمرّ دائماً تحت الانحراف"
    print("OK — الحراسة على مستوى الإصدار: الفتح محجوب والإغلاق حرّ")

    # القفل الميّت الذي شُخِّص على النواة الحيّة، مُعادًا حرفيًّا: أوّل نيّة
    # تُرفض تحت المرجع، فلا يُختم sent_at أبدًا. وصورة الوسيط لا تتغيّر لأنّ
    # لا مركز يُفتح. قبل 2.4.0 كان الحارس يقرأ «ما أُرسل قطّ» كأنّها «مهلة
    # سارية» فيكبت كل ما بعدها إلى الأبد — ٣٨٥ نشر ١٠٨٣ لقطة و٥٧٨ أرسل صفرًا.
    a8,b8=await new(); await a8._on_external({"official_time":400.0})
    await a8._on_divergence({"account_id":"A","symbol":"X","status":"DIVERGED"})
    first={"account_id":"A","symbol":"X","status":"READY","action":"ADD","target_buy":1,"target_sell":0,
           "target_net":1,"current_buy":0,"current_sell":0,"delta_buy":1,"delta_sell":0,"delta_net":1,
           "reference_price":100,"stop_distance_frac":.05,
           "produced_at":1400.0,"producer_epoch":1400.0,"sequence":1}
    await a8._on_target(first)
    assert len(ev(b8,mod.EVENT_REQUEST))==0, "المحاولة الأولى تُحجب بالانحراف"
    await a8._on_divergence({"account_id":"A","symbol":"X","status":"SYNCED"})
    await a8._on_external({"official_time":401.0})
    await a8._on_target(first)
    assert len(ev(b8,mod.EVENT_REQUEST))==1, "بعد زوال الانحراف يجب أن يخرج الأمر — لا قفل ميّت"
    await a8._on_target(first)
    assert len(ev(b8,mod.EVENT_REQUEST))==1, "وبعد إرسال فعليّ تعود المهلة للعمل"
    print("OK — لا قفل ميّت: «ما أُرسل قطّ» تعني اسمح، والمهلة تسري بعد إرسال حقيقيّ")

    # التصفية الانتقاميّة التي وقعت حيًّا: أمر تحوّط برجلين — إغلاق 0.02 وفتح
    # 0.02 — نُفِّذ نصفه فقط لأنّ الفتح محجوب والإغلاق حرّ، فبقي المركز ينزف
    # حتى الصفر بدل أن يتحوّط. ورقة المالك §3.3 تمنع هذا صراحةً.
    a9,b9=await new(); await a9._on_external({"official_time":500.0})
    await a9._on_divergence({"account_id":"A","symbol":"X","status":"DIVERGED"})
    hedge={"account_id":"A","symbol":"X","status":"READY","action":"REBALANCE",
           "target_buy":0.02,"target_sell":0.02,"target_net":0.0,
           "current_buy":0.0,"current_sell":0.04,"delta_buy":0.02,"delta_sell":-0.02,
           "delta_net":0.02,"reference_price":100,"stop_distance_frac":.05,
           "produced_at":1500.0,"producer_epoch":1500.0,"sequence":1,
           "current_legs":[{"ticket":11,"side":"SELL","volume":0.04,"current_price":100}]}
    await a9._on_target(hedge)
    assert len(ev(b9,mod.EVENT_REQUEST))==0, "أمر التحوّط ذرّيّ: إن حُجب الفتح لا يُرسَل الإغلاق وحده"

    # وبزوال الحجب تُنفَّذ الرجلان معًا — لا واحدة.
    await a9._on_divergence({"account_id":"A","symbol":"X","status":"SYNCED"})
    await a9._on_external({"official_time":501.0})
    await a9._on_target(hedge)
    reqs=ev(b9,mod.EVENT_REQUEST)
    kinds={r["action"] for r in reqs}
    assert len(reqs)==2 and kinds=={"OPEN","CLOSE_PARTIAL"}, f"يجب أن تخرج الرجلان معًا: {reqs}"

    # والتخفيض الخالص (بلا رجل فتح) لا يُحبس أبدًا حتى تحت الحجب.
    a10,b10=await new(); await a10._on_external({"official_time":600.0})
    await a10._on_divergence({"account_id":"A","symbol":"X","status":"DIVERGED"})
    await a10._on_target({"account_id":"A","symbol":"X","status":"READY","action":"REDUCE",
        "target_buy":0.0,"target_sell":0.0,"target_net":0.0,"current_buy":0.0,"current_sell":0.04,
        "delta_buy":0.0,"delta_sell":-0.04,"delta_net":0.04,"reference_price":100,
        "stop_distance_frac":.05,"produced_at":1600.0,"producer_epoch":1600.0,"sequence":1,
        "current_legs":[{"ticket":12,"side":"SELL","volume":0.04,"current_price":100}]})
    only=ev(b10,mod.EVENT_REQUEST)
    assert len(only)==1 and only[0]["action"]=="CLOSE_PARTIAL", "التخفيض الخالص يمرّ دائمًا"
    print("OK — أمر التحوّط ذرّيّ: لا نصف تنفيذ، ولا تصفية انتقاميّة (§3.3)")

    # حكم المالك ٢٠٢٦-٠٨-١٤ (البند ٥٨، الخيار أ): فشل أمر الفرق كان غير مرئيّ
    # أصلًا — تفاصيل الصحّة متطابقة حرفيًّا قبل الرفض وبعده. صار يُعَدّ، ولا
    # شيء آخر يتحرّك: لا إرسال ولا إعادة ولا تصعيد.
    a11,b11=await new(); await a11._on_external({"official_time":700.0,"account_id":"A","trade_allowed":True})
    await a11._on_target({"account_id":"A","symbol":"X","status":"READY","action":"ADD","target_net":1,
                          "current_net":0,"delta_net":1,"reference_price":100,"stop_distance_frac":.05,
                          "produced_at":1700.0,"producer_epoch":1700.0,"sequence":1})
    rid=ev(b11,mod.EVENT_REQUEST)[0]["request_id"]; before=len(b11.e)
    await a11._on_rejected({"request_id":rid,"account_id":"A","symbol":"X","reason":"RISK"})
    d=(await a11.health_check()).details
    assert d["delta_failed"]==1 and d["delta_last_reason"]=={"A|X":"RISK"}
    assert len(b11.e)==before, "الرفض لا يولّد حدثًا واحدًا — لا إعادة ولا تصعيد"
    assert d["pairs"]==0 and d["guard_failing"]=={"A|X":0}, "الرفض لا يحرّك عدّاد الحارس أصلًا"
    await a11._on_send_failure({"request_id":rid,"account_id":"A","symbol":"X","reason":"BRIDGE"})
    d=(await a11.health_check()).details
    assert d["delta_failed"]==2 and d["guard_failing"]=={"A|X":1}
    assert d["guard_backoff_s"]=={"A|X":4.0}, "التراجع المعروض من الحارس نفسه: 2s × 2¹"
    # وفشل ساق زوج يبقى للزوج، لا يُحسب فرقًا.
    a12,b12=await new()
    await a12._on_requested({"request_id":"p-buy-a1","account_id":"A","symbol":"X","side":"BUY",
                             "volume":1,"pair_id":"p","leg_role":"BUY","attempt":1,
                             "pair_required":True,"protection_mode":"NEUTRAL_HEDGE"})
    await a12._on_rejected({"request_id":"p-buy-a1","account_id":"A","symbol":"X","reason":"X"})
    assert (await a12.health_check()).details["delta_failed"]==0, "ساق الزوج ليست فرقًا"
    print("OK — فشل الفرق صار مرئيًّا، ولا شيء آخر تحرّك")

    # حكم المالك ٢٠٢٦-٠٨-١٤ (البند ٦٣، الخيار أ-٢): المعرّف يحمل هويّة اللقطة
    # ثمّ الزمن الرسميّ. `_counter` يعود صفرًا مع كل ترقية حيّة بينما يبقى حجز
    # 585 حيًّا، فلا يجوز أن يكون هو المميِّز بين نسخة وأخرى.
    t63={"account_id":"A","symbol":"X","status":"READY","action":"ADD","target_net":1,"current_net":0,
         "delta_net":1,"reference_price":100,"stop_distance_frac":.05,"snapshot_id":"snapshot-A\x1fX-7",
         "produced_at":1000.0,"producer_epoch":1000.0,"sequence":1}
    a13,b13=await new(); await a13._on_target(dict(t63))
    first=ev(b13,mod.EVENT_REQUEST)[0]["request_id"]
    assert "snapshotAX7" in first and "1000" in first, first
    assert "\x1f" not in first, "لا حرف تحكّم يعبر إلى الجسر أو الإكسبرت أبدًا"
    a14,b14=await new()   # ترقية حيّة لـ578 وحدها: عدّادها صفر، ولقطة 583 تقدّمت
    assert ev(b14,mod.EVENT_REQUEST)==[]
    await a14._on_target(dict(t63,snapshot_id="snapshot-A\x1fX-8"))
    assert ev(b14,mod.EVENT_REQUEST)[0]["request_id"] != first, "الهويّة تختلف بعد الترقية"
    # وبلا لقطة ولا زمن: يُرفض الإرسال — لا رجوع إلى العدّاد وحده.
    a15,b15=await new(clock=None)
    await a15._on_target({"account_id":"A","symbol":"X","status":"READY","action":"ADD","target_net":1,
                          "current_net":0,"delta_net":1,"reference_price":100,"stop_distance_frac":.05})
    assert len(ev(b15,mod.EVENT_REQUEST))==0, "بلا مميِّز لا يخرج أمر"
    # حكم المالك ٢٠٢٦-٠٨-١٥ في تعارض ٦٣⟷٤-١٠: العقد ٦٣ نفسه عُدِّل إلى
    # «`official_time=None` ⇒ لا إرسال»، فحاجز ٤-١٠ يسبق حساب الهويّة ويردّ
    # أوّلًا. النتيجة واحدة (صفر أمر) والعدّاد الذي يشهد صار عدّاد الاستئناف.
    assert a15._replay_skipped==1 and (await a15.health_check()).details["no_identity_skipped"]==0
    print("OK — الهويّة تحمل اللقطة والزمن، وبلا أيّهما يُرفض الإرسال")

    # بند 22 حزمة ت (ت١): خيط هوية القرار — الحقلان يمران بكل طلب أمر كما
    # وصلا باللقطة، والغائب يمر None مع إنذار identity_incomplete (لا اختراع).
    a16,b16=await new()
    # الوحدة 1 (5.1.1): الهوية المزعومة يجب أن تكون عبرت البوابة فعلًا — نغذي 467 أولًا.
    await a16._on_gate_passed({"decision_id":"D-7","gate_request_id":"G-7"})
    withid={"account_id":"A","symbol":"X","status":"READY","action":"ADD","target_net":1,"current_net":0,
            "delta_net":1,"reference_price":100,"stop_distance_frac":.05,"snapshot_id":"snap-9",
            "decision_id":"D-7","gate_request_id":"G-7",
            "produced_at":1800.0,"producer_epoch":1800.0,"sequence":1}
    await a16._on_target(withid)
    r=ev(b16,mod.EVENT_REQUEST)[0]
    assert r["decision_id"]=="D-7" and r["gate_request_id"]=="G-7", r
    assert r["snapshot_id"]=="snap-9", "اللقطة تُحمَل بالطلب حتى تتحقق منها بوابة 552"
    assert "identity_warnings" not in r, "هوية كاملة بلا إنذار"
    # والقرار الذي لم يعبر البوابة يُحجب بعدّاد ظاهر — لا نشر بأي حال.
    # (ذرّة جديدة: حارس الفيضان عند a16 يبتلع الإرسال الثاني قبل حارس البوابة — فنعزل الاختبار.)
    a16f,b16f=await new()
    await a16f._on_gate_passed({"decision_id":"D-7","gate_request_id":"G-7"})
    forged={**withid,"decision_id":"D-FORGED","gate_request_id":"G-FORGED","produced_at":1850.0,"sequence":2}
    await a16f._on_target(forged)
    assert len(ev(b16f,mod.EVENT_REQUEST))==0, "القرار غير النافذ لا يفتح"
    assert (await a16f.health_check()).details["gate_blocked_unverified"]==1
    # v5.2.0: هدفٌ بلا أي هوية قرار لا يفتح تعرّضًا — يُحجب ويُعدّ معلَنًا
    # (العقد القديم «يمرّر None معلنة» نُسخ بحكم القفل الجانبي المقيس).
    a17,b17=await new()
    noid={"account_id":"A","symbol":"X","status":"READY","action":"ADD","target_net":1,"current_net":0,
          "delta_net":1,"reference_price":100,"stop_distance_frac":.05,
          "decision_id":None,"gate_request_id":None,
          "produced_at":1900.0,"producer_epoch":1900.0,"sequence":1}
    await a17._on_target(noid)
    assert len(ev(b17,mod.EVENT_REQUEST))==0, "بلا هوية = لا فتح (v5.2.0)"
    assert (await a17.health_check()).details["no_identity_entries"]==1
    # والتخفيض (إدارة) يحمل الخيط أيضًا حين يتوفر
    a18,b18=await new()
    reduce_id={"account_id":"A","symbol":"X","status":"READY","action":"REDUCE","target_buy":0,"target_sell":0,
               "target_net":0,"current_buy":1,"current_sell":0,"delta_buy":-1,"delta_sell":0,"delta_net":-1,
               "reference_price":100,"stop_distance_frac":.05,"decision_id":"D-8","gate_request_id":"G-8",
               "produced_at":2000.0,"producer_epoch":2000.0,"sequence":1,
               "current_legs":[{"ticket":3,"side":"BUY","volume":1,"current_price":100}]}
    await a18._on_target(reduce_id)
    r=ev(b18,mod.EVENT_REQUEST)[0]
    assert r["action"]=="CLOSE_PARTIAL" and r["decision_id"]=="D-8" and r["gate_request_id"]=="G-8", r
    print("OK — ت١: الحقلان يمران بطلبات الفتح والتخفيض، والغائب None + إنذار")

    print("OK — العقد يعمل دون نبضات وهمية")

    # v5.4.0: كل تغيّر زوج كان يكتب المخزن الدائم بنداء متزامن حاجب (وُجد
    # أثناء تدقيق ٣٠٤ ذرّة). لفّه بـasyncio.to_thread وحده كان سيفتح نافذة
    # سباق جديدة: كتابتان متتاليتان (زوج A ثم زوج B) لو رُحّلتا لخيطين بلا
    # قفل، وتأخّرت الأولى، لكانت تُكتب فوق الثانية الأحدث فتضيع B من القرص.
    # القفل لكل الذرّة (مورد واحد، ملف واحد) يضمن أن الكتابات تصل القرص
    # بنفس ترتيب صدورها بالضبط.
    a19, b19 = await new()
    real_save = mod.pair_store.save
    write_order = []

    def ordered_save(path, sealed):
        pairs = sealed["payload"]["pairs"]
        if "P-B" not in pairs:
            time.sleep(0.15)  # يحاكي كتابة أولى بطيئة (قرص مزدحم)
        write_order.append(sorted(pairs))
        return real_save(path, sealed)

    mod.pair_store.save = ordered_save
    try:
        t1 = asyncio.create_task(a19._on_requested(
            {"pair_id": "P-A", "leg_role": "BUY", "request_id": "ra",
             "account_id": "A", "symbol": "X"}))
        await asyncio.sleep(0.01)
        t2 = asyncio.create_task(a19._on_requested(
            {"pair_id": "P-B", "leg_role": "BUY", "request_id": "rb",
             "account_id": "A", "symbol": "Y"}))
        await asyncio.gather(t1, t2)
    finally:
        mod.pair_store.save = real_save
    assert write_order == [["P-A"], ["P-A", "P-B"]], write_order
    on_disk = mod.pair_store.load(a19._pair_store_path)["payload"]["pairs"]
    assert "P-B" in on_disk, ("الكتابة الأحدث ضاعت من القرص بعد كتابة"
                              " أقدم متأخّرة: %r" % on_disk)
    print("OK — القفل يمنع كتابة أقدم متأخّرة من الكتابة فوق أحدث منها بالقرص")

    # وحلقة الحدث نفسها لا تتجمّد أثناء تلك الكتابة (asyncio.to_thread فعليًّا،
    # لا لفّ شكليّ) — نفس منهج الإثبات المستخدم بذرّتَي 516 و580.
    a20, b20 = await new()
    real_save2 = mod.pair_store.save

    def slow_save(path, sealed):
        time.sleep(0.2)
        return real_save2(path, sealed)

    mod.pair_store.save = slow_save
    order = []

    async def other_task():
        await asyncio.sleep(0.05)
        order.append("other_task")

    async def pair_call():
        await a20._on_requested({"pair_id": "P-C", "leg_role": "BUY",
                                 "request_id": "rc", "account_id": "A",
                                 "symbol": "Z"})
        order.append("pair_call")

    try:
        await asyncio.gather(other_task(), pair_call())
    finally:
        mod.pair_store.save = real_save2
    assert order == ["other_task", "pair_call"], order
    print("OK — حلقة الحدث بقيت حرّة أثناء كتابة الزوج الدائمة")


if __name__=="__main__":asyncio.run(main())
