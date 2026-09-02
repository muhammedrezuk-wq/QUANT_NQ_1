"""اختبار العقود المفتوحة v2.0 — التزامن.

يثبت أنّ:
  ١ — السعر المتزامن (من حمولة OI) يُنتج رباعيّةً صحيحة
  ٢ — إغلاق الشمعة المنفصل (بلا تزامن) يُنتج flat كاذب
  ٣ — السقوط للشمعة يعمل إن غاب السعر المرافق
  ٤ — الحقل price_source يُعلن المصدر فعلاً
  ٥ — الرباعيّات الأربع تُصنَّف بشكل صحيح مع السعر المتزامن

المشكلة الأصلية (v1.0):
  الـOI يصل كل ~١٦٫٨ ثانية، وإغلاق الشمعة كل ٥ دقائق.
  بينهما يبقى price نفسه ⇒ d_price_pct == 0 ⇒ 79.5% flat كاذب.
  الحل (v2.0): تضمين fair_price في market.oi من المصدر ٢٦٢١ (نفس الاستجابة).
"""
from __future__ import annotations
import asyncio, importlib.util, os
from core.contracts.atom import AtomContext

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("oi", os.path.join(HERE, "..", "atom.py"))
oi = importlib.util.module_from_spec(spec); spec.loader.exec_module(oi)


class Log:
    def __getattr__(self, _): return lambda *a, **k: None


class Bus:
    def __init__(self):
        self.out = []
    async def publish(self, event, payload):
        self.out.append((event, payload))
    def ctx(self, cfg):
        return AtomContext(atom_id=2170, config=cfg, logger=Log(),
                           publish=self.publish, subscribe=lambda *a, **k: None)
    def states(self):
        return [p for e, p in self.out if e == "sense.oi.state"]
    def last(self):
        s = self.states()
        return s[-1] if s else None


# ════════════════════════════════════════════════════════════════════════════
# الاختبار الجوهري: التزامن يُنتج رباعيّة، وانعدامه يُنتج flat كاذب
# ════════════════════════════════════════════════════════════════════════════

async def test_sync_vs_stale():
    """يحاكي الواقع: OI يتحرّك كل ١٦ ثانية، الشمعة تُغلَق كل ٥ دقائق.

    مع السعر المتزامن (v2.0): كل قراءة OI تحمل سعرها ⇒ رباعيّة صحيحة.
    مع إغلاق الشمعة (v1.0): السعر ثابت بين إغلاقين ⇒ flat كاذب.
    """
    # ── الحالة A: السعر المتزامن (من حمولة OI) ──
    bus_a = Bus(); atom_a = oi.Atom()
    await atom_a.initialize(bus_a.ctx({"noise_pct": 0.0, "max_age_s": 60}))
    await atom_a.start()

    # ١٠ قراءات OI متباعدة ~١٦ ثانية، كل قراءة تحمل سعرها المتزامن
    base_oi = 50000.0
    base_price = 42000.0
    for i in range(11):
        oi_val = base_oi + i * 100.0          # OI يصعد steadily
        price_val = base_price + i * 5.0      # السعر يصعد steadily
        await atom_a._on_oi({
            "symbol": "BTC_USDT", "provider": "MEXC",
            "oi": oi_val, "price": price_val, "timestamp": i * 16.8,
        })

    states_a = bus_a.states()
    assert len(states_a) == 10, f"يجب ١٠ حالات (أوّلها مرجع)، وجدنا {len(states_a)}"

    # مع السعر المتزامن: السعر يصعد + OI يصعد ⇒ new_longs
    quadrants_a = [s["quadrant"] for s in states_a]
    new_longs_a = quadrants_a.count("new_longs")
    flat_a = quadrants_a.count("flat")
    sync_pct_a = sum(1 for s in states_a if s["price_source"] == "oi_sync")

    # ── الحالة B: إغلاق الشمعة فقط (محاكاة v1.0) ──
    bus_b = Bus(); atom_b = oi.Atom()
    await atom_b.initialize(bus_b.ctx({"noise_pct": 0.0, "max_age_s": 60}))
    await atom_b.start()

    # شمعة واحدة تُغلَق — السعر يتثبّت عندها
    await atom_b._on_candle({"symbol": "BTC_USDT", "close": 42000.0})

    # ١٠ قراءات OI بلا سعر مرافق — الذرّة تسقط لإغلاق الشمعة (الثابت)
    for i in range(11):
        oi_val = base_oi + i * 100.0          # OI يصعد steadily (نفس الحالة A)
        await atom_b._on_oi({
            "symbol": "BTC_USDT", "provider": "MEXC",
            "oi": oi_val,                      # ← بلا price!
        })

    states_b = bus_b.states()
    assert len(states_b) == 10, f"يجب ١٠ حالات، وجدنا {len(states_b)}"

    quadrants_b = [s["quadrant"] for s in states_b]
    new_longs_b = quadrants_b.count("new_longs")
    flat_b = quadrants_b.count("flat")
    fallback_pct_b = sum(1 for s in states_b if s["price_source"] == "candle_fallback")

    # ── الإثبات ──
    # الحالة A (متزامن): يجب new_longs (OI↑ سعر↑)
    assert new_longs_a == 10, f"متزامن: يجب ١٠ new_longs، وجدنا {new_longs_a}"
    assert flat_a == 0, f"متزامن: يجب ٠ flat، وجدنا {flat_a}"
    assert sync_pct_a == 10, f"متزامن: يجب ١٠ oi_sync، وجدنا {sync_pct_a}"

    # الحالة B (شمعة ثابتة): يجب flat كاذب (السعر لا يتحرّك!)
    assert flat_b == 10, f"شمعة ثابتة: يجب ١٠ flat كاذب، وجدنا {flat_b}"
    assert new_longs_b == 0, f"شمعة ثابتة: يجب ٠ new_longs، وجدنا {new_longs_b}"
    assert fallback_pct_b == 10, f"شمعة ثابتة: يجب ١٠ candle_fallback، وجدنا {fallback_pct_b}"

    # ── نسبة flat: من ١٠٠% (v1.0) إلى ٠% (v2.0) مع السعر المتزامن ──
    print(f"  v1.0 (شمعة منفصلة): flat = {flat_b}/10 = {flat_b*10:.0f}%")
    print(f"  v2.0 (سعر متزامن):   flat = {flat_a}/10 = {flat_a*10:.0f}%")
    print(f"  ✅ التزامن أنقذ {new_longs_a} رباعيّات من flat الكاذب")


# ════════════════════════════════════════════════════════════════════════════
# الرباعيّات الأربع مع السعر المتزامن
# ════════════════════════════════════════════════════════════════════════════

async def test_quadrants_with_sync_price():
    """الرباعيّات الأربع مع السعر المتزامن — نفس المنطق، مصدر أفضل."""
    bus = Bus(); atom = oi.Atom()
    await atom.initialize(bus.ctx({"noise_pct": 0.0, "max_age_s": 60}))
    await atom.start()

    # مرجع
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC",
                        "oi": 50000.0, "price": 42000.0})
    assert bus.last() is None, "أوّل قراءة = مرجع فقط"

    # سعر↑ OI↑ = new_longs (صادقة)
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC",
                        "oi": 51000.0, "price": 42100.0})
    s = bus.last()
    assert s["quadrant"] == "new_longs" and s["honesty"] == "honest", s
    assert s["price_source"] == "oi_sync"

    # سعر↓ OI↓ = long_liquidation (شلّال)
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC",
                        "oi": 50500.0, "price": 41900.0})
    s = bus.last()
    assert s["quadrant"] == "long_liquidation" and s["honesty"] == "cascade", s

    # سعر↑ OI↓ = short_covering (هشّ)
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC",
                        "oi": 50000.0, "price": 42000.0})
    s = bus.last()
    assert s["quadrant"] == "short_covering" and s["honesty"] == "fragile", s

    # سعر↓ OI↑ = new_shorts (صادقة)
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC",
                        "oi": 51000.0, "price": 41800.0})
    s = bus.last()
    assert s["quadrant"] == "new_shorts" and s["honesty"] == "honest", s

    print("  ✅ الرباعيّات الأربع تُصنَّف بشكل صحيح مع السعر المتزامن")


# ════════════════════════════════════════════════════════════════════════════
# السقوط لإغلاق الشمعة
# ════════════════════════════════════════════════════════════════════════════

async def test_fallback_to_candle():
    """إن غاب السعر المرافق، الذرّة تسقط لإغلاق الشمعة — وتُعلن ذلك."""
    bus = Bus(); atom = oi.Atom()
    await atom.initialize(bus.ctx({"noise_pct": 0.0, "max_age_s": 60}))
    await atom.start()

    # شمعة تُغلَق أولاً
    await atom._on_candle({"symbol": "BTC_USDT", "close": 42000.0})

    # أوّل OI بلا سعر → مرجع
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC", "oi": 50000.0})
    assert bus.last() is None

    # ثاني OI بلا سعر → يستخدم الشمعة، ويُعلن candle_fallback
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC", "oi": 51000.0})
    s = bus.last()
    assert s is not None, "يجب أن يُنتج حالة"
    assert s["price_source"] == "candle_fallback", f"يجب candle_fallback، وجدنا {s['price_source']}"
    assert s["price"] == 42000.0, "يجب أن يستخدم سعر الشمعة"

    # الآن شمعة جديدة + OI مع سعر → يُفضّل المتزامن
    await atom._on_candle({"symbol": "BTC_USDT", "close": 43000.0})
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC",
                        "oi": 52000.0, "price": 42500.0})  # سعر مختلف عن الشمعة!
    s = bus.last()
    assert s["price_source"] == "oi_sync", "يجب أن يُفضّل السعر المتزامن"
    assert s["price"] == 42500.0, f"يجب 42500 (المتزامن)، لا 43000 (الشمعة)"

    # فحص العدّادين
    assert atom._sync_used >= 1
    assert atom._fallback_used >= 1

    print(f"  ✅ السقوط يعمل: sync={atom._sync_used}, fallback={atom._fallback_used}")


# ════════════════════════════════════════════════════════════════════════════
# بلا سعر من أيّ مصدر ⇒ لا رباعيّة
# ════════════════════════════════════════════════════════════════════════════

async def test_no_price_no_quadrant():
    """إن غاب السعر من أيّ مصدر، الذرّة لا تُنتج رباعيّة."""
    bus = Bus(); atom = oi.Atom()
    await atom.initialize(bus.ctx({"noise_pct": 0.0, "max_age_s": 60}))
    await atom.start()

    # OI بلا سعر وبلا شمعة سابقة ⇒ لا شيء
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC", "oi": 50000.0})
    assert bus.last() is None, "بلا سعر ⇒ لا رباعيّة"

    # مرجع بأول سعر
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC",
                        "oi": 50000.0, "price": 42000.0})
    assert bus.last() is None  # مرجع فقط

    # ثاني OI بلا سعر ⇒ يسقط للشمعة ⇒ لكن لا شمعة ⇒ لا شيء
    # (السعر السابق من OI الأول لا يُخزَّن كـ candle)
    # في الواقع: _price فارغ ⇒ يسقط ⇒ price = None ⇒ return
    # لكن _prev يحتوي على (ts, 42000.0, 50000.0) من القراءة السابقة
    # هل سيُنتج رباعيّة؟ لا: لأن price=None ⇒ return قبل الوصول للرباعيّة
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC", "oi": 51000.0})
    assert bus.last() is None, "بلا سعر من أيّ مصدر ⇒ لا رباعيّة"

    print("  ✅ بلا سعر من أيّ مصدر ⇒ لا رباعيّة (حماية صحيحة)")


# ════════════════════════════════════════════════════════════════════════════
# نسبة flat — الدليل الكميّ
# ════════════════════════════════════════════════════════════════════════════

async def test_flat_reduction():
    """يحاكي ١٠٠ قراءة OI — يثبت أنّ flat% انخفضت مع التزامن.

    الإنتاج (قبل الإصلاح): 79.5% flat — لأن السعر ثابت بين إغلاقَي شمعة.
    d_price_pct == 0 بالضبط (السعر لم يُعاد قراءته).
    
    بعد الإصلاح: السعر المتزامن يتغيّر مع كل قراءة ⇒ d_price_pct != 0.
    flat يحدث فقط حين يكون BOTH d_oi و d_price أقلّ من noise_pct.
    
    مع بيانات واقعية + noise_pct معقول: flat% يجب أن تكون < 40%.
    """
    import random
    random.seed(42)  # حتمية

    N = 100
    base_oi = 50000.0
    base_price = 42000.0

    # ── مع التزامن ──
    bus = Bus(); atom = oi.Atom()
    # noise_pct = 0.005% — فقط التغيّرات الصغيرة جدًا تُصنَّف flat
    await atom.initialize(bus.ctx({"noise_pct": 0.005, "max_age_s": 60}))
    await atom.start()

    oi_val = base_oi
    price_val = base_price
    # مرجع
    await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC",
                        "oi": oi_val, "price": price_val})

    for i in range(N):
        # حركة واقعية — OI والسعر يتحرّكان مع كل قراءة
        # OI: ~100-500 عقد (0.2-1.0% من 50000) — حركة واضحة
        oi_val += random.gauss(0, 200.0)
        # السعر: ~10-50 دولار (0.02-0.12% من 42000) — حركة واضحة
        price_val += random.gauss(0, 15.0)
        await atom._on_oi({"symbol": "BTC_USDT", "provider": "MEXC",
                            "oi": oi_val, "price": price_val})

    states = bus.states()
    flat_count = sum(1 for s in states if s["quadrant"] == "flat")
    flat_pct = flat_count / max(1, len(states)) * 100.0

    # ── الدليل ──
    # مع بيانات واقعية + noise_pct=0.005%:
    # السعر يتحرّك ~0.02-0.12% مع كل قراءة ⇒ d_price_pct > 0.005% غالبًا
    # OI يتحرّك ~0.2-1.0% ⇒ d_oi_pct > 0.005% غالبًا
    # ⇒ flat نادر (فقط حين يكون كلاهما < 0.005%)
    print(f"  {len(states)} قراءة، flat = {flat_count} ({flat_pct:.1f}%)")
    print(f"  sync = {atom._sync_used}/{atom._sync_used + atom._fallback_used}")

    # يجب أن تكون flat% أقلّ من 40% (هدف المهمة)
    assert flat_pct < 40.0, f"flat% = {flat_pct:.1f}% — يجب أقلّ من 40% مع السعر المتزامن"

    # وكل القراءات (بما فيها المرجع) استخدمت السعر المتزامن
    assert atom._sync_used == N + 1, f"يجب {N+1} sync (بما فيها المرجع)، وجدنا {atom._sync_used}"

    print(f"  ✅ flat% = {flat_pct:.1f}% < 40% — هدف المهمة محقّق")


# ════════════════════════════════════════════════════════════════════════════
# التشغيل
# ════════════════════════════════════════════════════════════════════════════

async def main():
    print("=== اختبار الذرّة ٢١٧٠ v2.0 — التزامن ===\n")

    print("[١] التزامن مقابل القدم:")
    await test_sync_vs_stale()

    print("\n[٢] الرباعيّات الأربع:")
    await test_quadrants_with_sync_price()

    print("\n[٣] السقوط لإغلاق الشمعة:")
    await test_fallback_to_candle()

    print("\n[٤] بلا سعر ⇒ لا رباعيّة:")
    await test_no_price_no_quadrant()

    print("\n[٥] الدليل الكميّ — نسبة flat:")
    await test_flat_reduction()

    print("\n" + "=" * 60)
    print("✅ OK 2170 — التزامن يُنتج رباعيّات صحيحة")
    print("   السعر المتزامن (oi_sync) يُفضَّل، السقوط (candle_fallback) يُعلن")
    print("   flat% انخفضت من 79.5% إلى <40%")


if __name__ == "__main__":
    asyncio.run(main())
