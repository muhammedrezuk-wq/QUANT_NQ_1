# The risk scope is an explicit dollar budget per account x asset; no percent conversion is used.
#
# v4.2.0 (item 18/27 of the 27-atom review): this file used to be exactly
# the comment above and nothing else -- a fake test, no actual assertion
# that the claim holds. Real coverage below: a budget of 100 means
# EXACTLY $100, and u (utilization) is a plain exposure/budget ratio --
# never scaled as if budget were a percentage of anything.
import importlib.util
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
folder = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(folder))
spec = importlib.util.spec_from_file_location("_atom518_risk_scope", folder / "atom.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["_atom518_risk_scope"] = mod
spec.loader.exec_module(mod)


class _Log:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def critical(self, *a, **k): pass


class _Bus:
    def __init__(self): self.events = []
    def subscribe(self, *a, **k): pass
    async def publish(self, name, payload): self.events.append((name, payload))
    def ctx(self, db, budget):
        return mod.AtomContext(518, {"default_risk_budget": 0, "consumer_db_path": db,
                                     "count_realized": True, "max_seen_trades": 100},
                               _Log(), self.publish, self.subscribe)


async def _ledger_row_for_budget(tmp_dir, budget):
    bus = _Bus()
    atom = mod.Atom()
    await atom.initialize(bus.ctx(str(Path(tmp_dir) / ("j-%s.db" % budget)), budget))
    await atom.start()
    await atom._on_account({"account_id": "A", "broker": "BR"})
    await atom._on_specs({"symbols": [{"account_id": "A", "symbol": "NQ",
                                       "tick_size": 1.0, "tick_value": 1.0}]})
    await atom._on_budget({"account_id": "A", "broker": "BR", "symbol": "NQ",
                           "risk_budget": budget})
    # BUY 1 lot at 100, marked at bid=90 -- a clean $10 floating loss with
    # tick_value/tick_size == 1, so the dollar arithmetic is unambiguous.
    await atom._on_positions({"account_id": "A", "broker": "BR", "source": "609",
                              "timestamp": 1, "complete": True,
                              "positions": [{"account_id": "A", "broker": "BR", "symbol": "NQ",
                                            "ticket": 1, "side": "BUY", "volume": 1,
                                            "entry_price": 100, "bid": 90, "ask": 90}]})
    rows = [p for n, p in bus.events if n == mod.EVENT_OUT]
    return rows[-1]["ledgers"][0]


async def test_budget_is_used_as_a_raw_dollar_figure_not_converted():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        row = await _ledger_row_for_budget(tmp, 100)
    # budget=100 means $100 exactly -- not "100%" of anything, not divided
    # or multiplied by any implicit scale factor.
    assert row["risk_budget"] == 100 and row["budget"] == 100, row
    # $10 floating loss / $100 budget == a plain 0.1 ratio. If budget were
    # ever treated as a percentage (e.g. silently divided by 100 first),
    # u would come out as 10 instead.
    assert row["loss_exposure"] == 10, row
    assert row["u"] == 0.1, ("u ليس نسبة دولار/دولار بسيطة -- شبهة تحويل نسبة مئوية: %r" % row["u"])


async def test_doubling_budget_halves_utilization_no_percent_step():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        row_100 = await _ledger_row_for_budget(tmp, 100)
        row_200 = await _ledger_row_for_budget(tmp, 200)
    # Same $10 exposure, double the dollar budget -> exactly half the
    # utilization ratio. A percent-conversion bug (e.g. budget/100 or
    # budget*100 somewhere in the pipeline) would break this exact 2x
    # relationship -- a factor-of-100 bug does not survive as a near-miss.
    assert row_100["loss_exposure"] == row_200["loss_exposure"] == 10, (row_100, row_200)
    assert abs(row_100["u"] - 2 * row_200["u"]) < 1e-9, (row_100["u"], row_200["u"])
