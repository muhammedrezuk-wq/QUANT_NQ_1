# اختبار 451 (تجميع القرار) على التكة — Owner 2026-08-22.
import asyncio, importlib.util, sys
from pathlib import Path
root = Path(__file__).resolve().parents[3]
folder = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("_m451", folder / "atom.py")
m = importlib.util.module_from_spec(spec); sys.modules["_m451"] = m
spec.loader.exec_module(m)
Atom = m.Atom; EVENT_OUT = m.EVENT_OUT
ACCOUNT="A"; BROKER="BR"; SYMBOL="BTCUSD"
EXPECTED=["150","400"]

class L:
    def __getattr__(self,n): return lambda *a,**k: None
class B:
    def __init__(self): self.e=[]
    def subscribe(self,*a): pass
    async def publish(self,n,p): self.e.append((n,p))

def _tick(seq):
    return {"symbol":SYMBOL,"account_id":ACCOUNT,"broker":BROKER,"sequence":seq}

async def _new():
    b=B(); a=Atom()
    await a.initialize(m.AtomContext(451, {"expected_families":EXPECTED}, L(), b.publish, b.subscribe))
    await a.start(); return a,b

def _by_source(payload,src):
    for row in payload["evidence"]:
        if row["source"]==src: return row
    return None

async def test_tick_opens_cycle_from_sequence():
    print("\n--- test_tick_opens_cycle_from_sequence ---")
    a,b=await _new()
    await a._on_tick(_tick(1))
    cid=a._identity[(ACCOUNT, BROKER, SYMBOL)]["cycle_id"]
    assert cid.endswith("|tick|1"), cid
    print("OK — دورة تكة من sequence:", cid)

async def test_emits_when_all_families_complete():
    print("\n--- test_emits_when_all_families_complete ---")
    a,b=await _new()
    # البناء ٤ (أمر المالك ٢٠٢٦-٠٨-٢٣): التحليل يدخل بطاقة قسم (150) لا
    # analysis.raw.completed — طريق واحد.
    await a._on_section_live({"symbol":SYMBOL,"account_id":ACCOUNT,"broker":BROKER,
        "section_id":"150","signal":"up","score":60,"confidence":80,"quality":"good",
        "status":"ok","analysis_mode":"live_tick","ready":True,"analysis_state":"DECISION_READY",
        "direction":60,"active_weight":50,"current_depth":90,"required_depth":60,
        "weight":16.66,"state":"READY","unified":{"state":"READY","direction":60,
        "strength":60,"confidence":80,"weight":16.66,"weight_effect":16.66}})
    # أضف عائلة 400 (strategies section.live) — تُخزن في _latest_section_live
    await a._on_section_live({"symbol":SYMBOL,"account_id":ACCOUNT,"broker":BROKER,
        "section_id":"400","signal":"positive_strategic_lean","direction":60,
        "strength":80,"confidence":80,"current_depth":90,"required_depth":60,
        "weight":16.66,"ready":True,"state":"READY","unified":{"state":"READY",
        "direction":60,"strength":80,"confidence":80,"current_depth":90,"required_depth":60,
        "weight":16.66,"weight_effect":16.66}})
    # الآن افتح دورة التكة — يقرأ live + section_live ويجمع الأدلة
    await a._on_tick(_tick(1))
    out=[p for n,p in b.e if n==EVENT_OUT]
    assert out, "لم ينشر"
    last=out[-1]
    assert last["complete"] is True, last["complete"]
    assert last["cycle_id"].endswith("|tick|1"), last["cycle_id"]
    assert last["weighted_direction"] is not None, "بلا اتجاه موزون"
    print("OK — الرقم النهائي يتحرك على التكة, direction=%s" % last["weighted_direction"])

async def test_room_streams_gradual_whatever_state():
    # إقفال 150 مرحلة ٢: الروم يستقبل كل بطاقة هوية كاملة مهما كانت حالتها
    a, b = await _new()
    # بطاقة تحليل NOT_READY — كانت تُرمى، الآن تدخل الروم بما قالته
    await a._on_section_live({"symbol":SYMBOL,"account_id":ACCOUNT,"broker":BROKER,
        "section_id":"150","state":"NOT_READY","unified":{"state":"NOT_READY",
        "direction":40.0,"direction_sign":1.0,"strength":30.0,"confidence":None,
        "current_depth":33.0,"required_depth":60.0,"weight":45.0,"ratio":100.0,
        "unknown_fields":["confidence"]},"readiness_pct":55.0})
    rooms=[p for n,p in b.e if n==m.EVENT_ROOM]
    assert rooms, "الروم لم يُنشر لبطاقة NOT_READY"
    r1=rooms[-1]
    assert "150" in r1["sections_present"] and r1["sections_missing"], r1
    assert r1["direction"]==40.0 and r1["confidence"] is None and r1["confidence_defined"] is False, r1
    assert r1["readiness_pct"]==55.0, r1
    # قسم ثانٍ بوزن مختلف — الرقم يتحرك بتدرج بلا قفزة ولا انتظار
    await a._on_section_live({"symbol":SYMBOL,"account_id":ACCOUNT,"broker":BROKER,
        "section_id":"400","state":"READY","unified":{"state":"READY",
        "direction":-20.0,"direction_sign":-1.0,"strength":70.0,"confidence":80.0,
        "current_depth":90.0,"required_depth":60.0,"weight":55.0,"ratio":50.0,
        "unknown_fields":[]},"readiness_pct":100.0})
    r2=[p for n,p in b.e if n==m.EVENT_ROOM][-1]
    # (40*45 + -20*55)/100 = 7 — حركة تدريجية من 40 إلى 7
    assert r2["direction"]==7.0, r2["direction"]
    assert r2["confidence"]==80.0 and r2["confidence_defined"] is True, r2
    assert r2["ratio"]==72.5 and r2["ratio_defined"] is True, r2
    assert len(r2["sections_present"])==2, r2
    required={"direction","strength","confidence","current_depth","required_depth","weight","ratio","state"}
    assert all(required <= set(row) for row in r2["sections"]), r2["sections"]
    # مجهول الاتجاه يبقى None في صف القسم، ولا يدخل المتوسط كحياد 0.
    await a._on_section_live({"symbol":SYMBOL,"account_id":ACCOUNT,"broker":BROKER,
        "section_id":"150","state":"STALE","unified":{"state":"STALE",
        "direction":None,"direction_sign":None,"strength":0.0,"confidence":0.0,
        "current_depth":100.0,"required_depth":60.0,"weight":45.0,"ratio":100.0,
        "unknown_fields":["direction"]},"readiness_pct":100.0})
    r3=[p for n,p in b.e if n==m.EVENT_ROOM][-1]
    row150=next(row for row in r3["sections"] if row["section_id"]=="150")
    assert row150["direction"] is None and "direction" in row150["unknown_fields"], row150
    assert r3["direction"]==-20.0, r3["direction"]
    # وبطاقة بلا هوية ما تدخل الروم
    before=len(b.e)
    await a._on_section_live({"section_id":"150","state":"READY"})
    assert len([p for n,p in b.e if n==m.EVENT_ROOM])==3, "بطاقة بلا هوية دخلت الروم"
    h = await a.health_check()
    assert h.details["room_emitted"]==3 and h.details["room_sections"]==2, h.details
    print("OK — الروم: NOT_READY يدخل، الرقم يتحرك 40→7 بتدرج، وبلا هوية يُرفض")


async def main():
    await test_tick_opens_cycle_from_sequence()
    await test_emits_when_all_families_complete()
    await test_room_streams_gradual_whatever_state()
    print("451 tick tests passed")

asyncio.run(main())
