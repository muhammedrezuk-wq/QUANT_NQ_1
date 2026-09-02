# -*- coding: utf-8 -*-
"""v4.1.0: ميزانية المالك تنجو من الإقلاع — الجذر المقاس 2026-08-25:
budgets كانت بذاكرة طيّارة فقط، فإعادة تشغيل تبخّر قرار المالك المكتوب
عبر 901 وتُنيم سلسلة الحماية كلها على NO_BUDGET."""
import asyncio
import importlib.util
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[3]
folder = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(folder))

spec = importlib.util.spec_from_file_location("_atom518_budget", folder / "atom.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["_atom518_budget"] = mod
spec.loader.exec_module(mod)


class _Log:
    def debug(self, *a, **k): pass
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def critical(self, *a, **k): pass


class _Bus:
    def __init__(self):
        self.events = []

    def subscribe(self, *a, **k): pass

    async def publish(self, name, payload):
        self.events.append((name, payload))

    def ctx(self, db):
        return mod.AtomContext(518, {"consumer_db_path": db}, _Log(),
                               self.publish, self.subscribe)


async def _boot(db):
    bus = _Bus()
    atom = mod.Atom()
    await atom.initialize(bus.ctx(db))
    await atom.start()
    return atom, bus


def test_budget_survives_restart():
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "journal.db")
            # الحياة الأولى: تفعيل مالك بميزانية 100
            atom1, _ = await _boot(db)
            await atom1._on_account({"account_id": "A1", "broker": "BR"})
            await atom1._on_activate({"account_id": "A1", "broker": "BR",
                                      "symbol": "BTCUSD", "budget": 100.0,
                                      "event_id": "evt-activate-1"})
            key = mod.scope("A1", "BTCUSD", "BR")
            assert atom1._budgets.get(key) == 100.0
            # الحياة الثانية: إقلاع جديد بلا أي حدث — الميزانية تعود من القرص
            atom2, _ = await _boot(db)
            assert atom2._budgets.get(key) == 100.0, atom2._budgets
            assert key in atom2._known
            # تعديل مالك (SET_BUDGET) يبدّل القيمة الدائمة أيضًا
            await atom2._on_budget({"account_id": "A1", "broker": "BR",
                                    "symbol": "BTCUSD", "risk_budget": 75.0,
                                    "event_id": "evt-budget-2"})
            atom3, _ = await _boot(db)
            assert atom3._budgets.get(key) == 75.0, atom3._budgets

    asyncio.run(scenario())


def test_restore_raises_on_corrupt_non_dict_state():
    """Item 18/27 of the 27-atom review ("silent restore on data
    corruption"): ledger_persistence.restore() used to silently `return`
    on a non-dict state -- the atom quietly kept its empty __init__
    defaults for _realized/_extracted/_budgets, no error, no log.
    self._extracted is what stops the SAME profit from being extracted
    twice (524 reads this ledger's published state for its milestone
    ladder); losing it silently on a corrupt snapshot risks re-extracting
    real money already paid out in the prior run. Must now raise."""
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "journal.db")
            atom, _ = await _boot(db)
            await atom._on_trade({"event_id": "t1", "account_id": "A1", "broker": "BR",
                                  "symbol": "BTCUSD", "pnl": 40.0, "gross_pnl": 40.0,
                                  "completeness": "COMPLETE"})
            key = mod.scope("A1", "BTCUSD", "BR")
            before_realized = dict(atom._realized)
            threw = False
            try:
                await atom.restore("not-a-dict-state")
            except ValueError:
                threw = True
            assert threw, "restore() لم ينهر رغم حالة ليست قاموساً -- استعادة صامتة لا تزال قائمة"
            assert atom._realized == before_realized, ("الحالة تغيّرت رغم انهيار restore() "
                                                        "-- ليست صامتة فقط بل ممزَّقة أيضاً")
            assert key in before_realized

    asyncio.run(scenario())


def test_restore_ignores_corrupt_single_field_without_crashing():
    """Item 18/27: a single corrupt sub-field inside an otherwise-valid
    state dict (e.g. "positions" saved as a non-list) used to raise
    TypeError mid-restore -- after whichever earlier books/... in the
    original field order had already been written directly onto self,
    tearing it. Every list-shaped sub-field is now defensively coerced to
    empty when malformed, so restore() can no longer be crashed by one
    bad field; the rest of a valid snapshot still restores correctly."""
    async def scenario():
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "journal.db")
            atom1, _ = await _boot(db)
            await atom1._on_account({"account_id": "A1", "broker": "BR"})
            await atom1._on_activate({"account_id": "A1", "broker": "BR", "symbol": "BTCUSD",
                                      "budget": 100.0, "event_id": "evt-activate-1"})
            snap = await atom1.snapshot()
            snap["positions"] = 42  # corrupt: should be a list
            atom2, _ = await _boot(str(Path(tmp) / "journal2.db"))
            await atom2.restore(snap)  # must not raise
            key = mod.scope("A1", "BTCUSD", "BR")
            assert atom2._budgets.get(key) == 100.0, ("حقل سليم (budgets) لم يُستعَد رغم أنّ "
                                                       "الحقل الفاسد (positions) وحده يجب أن "
                                                       "يُتجاهَل لا أن يُسقط البقية: %r" % atom2._budgets)
            assert not atom2._positions

    asyncio.run(scenario())
