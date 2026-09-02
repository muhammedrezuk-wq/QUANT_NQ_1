# اختبارات أمان الترتيب للذرة 103 (الإصدار 4.1.0) — تدقيق 2026-08-22.
# تغطي: out-of-order (تكة متأخرة)، duplicate، late-period (إعادة فتح فترة مغلقة)،
# و fabric_gap awareness.
import asyncio, importlib.util, sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
folder = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location('_t103_ord', folder / 'atom.py')
m = importlib.util.module_from_spec(spec)
sys.modules['_t103_ord'] = m
spec.loader.exec_module(m)


class L:
    def __getattr__(self, n):
        return lambda *a, **k: None


class B:
    def __init__(self):
        self.e = []

    def subscribe(self, *a):
        pass

    async def publish(self, n, p):
        self.e.append((n, p))


async def new_atom(period=60):
    b = B()
    a = m.Atom()
    await a.initialize(m.AtomContext(103, {'period_s': period}, L(), b.publish, b.subscribe))
    await a.start()
    return a, b


async def test_happy_path():
    a, b = await new_atom()
    base = {'account_id': 'A', 'broker': 'BR', 'provider': 'MT5', 'symbol': 'NQ',
            'bid': 100, 'ask': 101, 'timestamp': 1}
    await a._on_tick(base)               # period 0
    await a._on_tick({**base, 'bid': 102, 'ask': 103, 'timestamp': 61})  # period 60
    out = [p for n, p in b.e if n == m.EVENT_OUT][-1]
    assert out['open'] == 100.5 and out['period_start'] == 0, out
    assert out['fabric_gap'] is False
    print('ok happy_path')


async def test_out_of_order_dropped():
    a, b = await new_atom()
    base = {'account_id': 'A', 'broker': 'BR', 'provider': 'MT5', 'symbol': 'NQ',
            'bid': 100, 'ask': 101, 'timestamp': 1, 'sequence': 100}
    await a._on_tick(base)
    # تكة لاحقة بترتيب سليم (فترة 60)
    await a._on_tick({**base, 'bid': 102, 'ask': 103, 'timestamp': 61, 'sequence': 101})
    # تكة متأخرة (sequence أقل) تصل متأخرة → تُسقط قبل لمس الشمعة الحالية
    await a._on_tick({**base, 'bid': 50, 'ask': 51, 'timestamp': 5, 'sequence': 99})
    # يجب ألا تُسقط out-of-order أي شيء: الفترة الحالية (60) لم تُغلق مبكراً
    assert a.out_of_order_dropped == 1, a.out_of_order_dropped
    # الشمعة الحالية لم تُغلق بعد من التكة المتأخرة — لا candle_closed جديد
    closed = [p for n, p in b.e if n == m.EVENT_OUT]
    assert len(closed) == 1, len(closed)
    print('ok out_of_order_dropped')


async def test_duplicate_dropped():
    a, b = await new_atom()
    base = {'account_id': 'A', 'broker': 'BR', 'provider': 'MT5', 'symbol': 'NQ',
            'bid': 100, 'ask': 101, 'timestamp': 1}
    await a._on_tick(base)
    # نسخة مكررة مطابقة تماماً
    await a._on_tick({**base, 'bid': 100, 'ask': 101, 'timestamp': 1})
    assert a.duplicates_dropped == 1, a.duplicates_dropped
    # tick_count لم يتضخم
    cur = a._candles[('A', 'BR', 'NQ')]
    assert cur['tick_count'] == 1, cur['tick_count']
    print('ok duplicate_dropped')


async def test_late_period_dropped():
    a, b = await new_atom()
    base = {'account_id': 'A', 'broker': 'BR', 'provider': 'MT5', 'symbol': 'NQ',
            'bid': 100, 'ask': 101, 'timestamp': 1}
    await a._on_tick(base)               # فترة 0
    await a._on_tick({**base, 'bid': 102, 'ask': 103, 'timestamp': 61})  # فترة 60
    # الآن فترة 0 مغلقة. تكة متأخرة من فترة 0 (بلا sequence) تصل بعدها:
    await a._on_tick({**base, 'bid': 40, 'ask': 41, 'timestamp': 30})
    # يجب ألا تُغلق فترة 60 مبكراً ولا تُعيد فتح فترة 0
    closed = [p for n, p in b.e if n == m.EVENT_OUT]
    assert len(closed) == 1, len(closed)
    assert a.late_dropped == 1, a.late_dropped
    assert ('A', 'BR', 'NQ') in a._candles and a._candles[('A', 'BR', 'NQ')]['period_start'] == 60
    print('ok late_period_dropped')


async def test_fabric_gap_flag():
    a, b = await new_atom()
    base = {'account_id': 'A', 'broker': 'BR', 'provider': 'MT5', 'symbol': 'NQ',
            'bid': 100, 'ask': 101, 'timestamp': 1, 'fabric_gap': True}
    await a._on_tick(base)
    assert a.gap_affected_candles == 1, a.gap_affected_candles
    # إغلاق عند الفترة التالية
    await a._on_tick({**base, 'bid': 102, 'ask': 103, 'timestamp': 61})
    out = [p for n, p in b.e if n == m.EVENT_OUT][-1]
    assert out['fabric_gap'] is True, out
    print('ok fabric_gap_flag')


async def main():
    await test_happy_path()
    await test_out_of_order_dropped()
    await test_duplicate_dropped()
    await test_late_period_dropped()
    await test_fabric_gap_flag()
    print('103 ordering-safety tests passed')


if __name__ == '__main__':
    asyncio.run(main())
