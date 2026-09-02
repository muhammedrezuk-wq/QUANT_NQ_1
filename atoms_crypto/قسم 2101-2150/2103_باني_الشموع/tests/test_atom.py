import asyncio, importlib.util, sys
from pathlib import Path
root = Path(__file__).resolve().parents[4]; folder = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location('_t103', folder / 'atom.py')
m = importlib.util.module_from_spec(spec); sys.modules['_t103'] = m; spec.loader.exec_module(m)


class L:
    def __getattr__(self, n): return lambda *a, **k: None


class B:
    def __init__(self): self.e = []
    def subscribe(self, *a): pass
    async def publish(self, n, p): self.e.append((n, p))


def _tick(ts, bid=100, ask=101):
    return {'account_id': 'A', 'broker': 'BR', 'provider': 'MT5', 'symbol': 'NQ',
            'bid': bid, 'ask': ask, 'timestamp': ts}


async def _new(frames):
    b = B(); a = m.Atom()
    await a.initialize(m.AtomContext(103, {'timeframes': frames}, L(), b.publish, b.subscribe))
    await a.start()
    return a, b


async def test_single_frame_backward_compatible():
    a, b = await _new(['60s'])
    await a._on_tick(_tick(1))
    await a._on_tick(_tick(61, 102, 103))
    out = [p for n, p in b.e if n == m.EVENT_OUT]
    assert len(out) == 1 and out[0]['timeframe'] == '60s'
    assert out[0]['open'] == 100.5 and out[0]['period_start'] == 0.0
    print('OK — فريم واحد افتراضي: نفس سلوك 4.1.0 بالضبط')


async def test_factory_builds_frames_in_parallel_batch():
    a, b = await _new(['5s', '60s'])
    for ts in (1, 3, 6, 9):          # فريم 5ث يغلق عند 6 و(لا) — 60ث ما زال يبني
        await a._on_tick(_tick(ts))
    fives = [p for n, p in b.e if n == m.EVENT_OUT and p['timeframe'] == '5s']
    assert len(fives) == 1 and fives[0]['period_start'] == 0.0, fives
    assert not [p for n, p in b.e if n == m.EVENT_OUT and p['timeframe'] == '60s']
    await a._on_tick(_tick(61))      # هنا الدفعة: 5ث يقفل دوره و60ث يقفل دوره سوا
    outs = [p for n, p in b.e if n == m.EVENT_OUT]
    assert {o['timeframe'] for o in outs[-2:]} == {'5s', '60s'}
    h = await a.health_check()
    assert h.details['batch_releases'] >= 1 and h.details['closed_by_frame']['5s'] >= 2
    print('OK — مصنع الفريمات: 5ث و60ث بالتوازي، ودفعات عند التقاطع')


async def test_bad_frames_ignored_default_kept():
    a, b = await _new(['zz', '60s'])
    assert [name for name, _ in a._frames] == ['60s']
    print('OK — فريم تالف يُتجاهل والافتراضي يبقى')


async def main():
    await test_single_frame_backward_compatible()
    await test_factory_builds_frames_in_parallel_batch()
    await test_bad_frames_ignored_default_kept()
    print('103 frame factory tests passed')


if __name__ == '__main__':
    asyncio.run(main())
