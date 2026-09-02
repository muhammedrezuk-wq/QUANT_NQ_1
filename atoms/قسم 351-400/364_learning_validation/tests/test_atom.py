import asyncio

import pytest
from core.contracts.atom import AtomContext
from shared.learning_model import predict
from tests.learning_test_support import Bus, Logger, artifact, feature, load_atom, make_atom


@pytest.mark.asyncio
async def test_validator_uses_ordered_holdout_and_multiple_metrics():
    module, atom, bus = await make_atom(364, {"validation_size": 1,
                                               "min_accuracy": 0.0})
    candidate = artifact()
    row = feature("hold", "buy", 0.15)
    probabilities = predict(candidate, row["feature_vector"])
    row["label"] = max(("buy", "sell", "neutral"),
                       key=lambda name: probabilities["p_" + name])
    await atom._on_feature(row)
    candidate["train_ids"] = []
    await atom._on_candidate(candidate)
    report = bus.payloads(module.EVENT_OUT)[-1]
    assert report["passed"] is True
    assert "balanced_accuracy" in report
    assert "log_loss" in report
    assert "brier_score" in report


class GatedBus(Bus):
    """v1.2.0 proof: gate publish() for a specific model_version so a
    concurrent resubmission gets a real window to run while the retry
    loop is suspended mid-validation -- reproduces the exact interleaving
    the _on_feature comment describes, not a timing-dependent guess."""
    def __init__(self):
        super().__init__()
        self.gate = asyncio.Event()
        self.reached_gate = asyncio.Event()
        self.pause_on_version = None

    async def publish(self, name, payload):
        if (self.pause_on_version is not None
                and payload.get("model_version") == self.pause_on_version):
            self.reached_gate.set()
            await self.gate.wait()
        await super().publish(name, payload)


async def _make_gated(atom_id, config):
    module = load_atom(atom_id)
    bus = GatedBus()
    atom = module.Atom()
    await atom.initialize(AtomContext(atom_id, config or {}, Logger(), bus.publish, bus.subscribe))
    await atom.start()
    return module, atom, bus


@pytest.mark.asyncio
async def test_concurrent_resubmission_does_not_crash_pending_removal():
    module, atom, bus = await _make_gated(364, {"validation_size": 2, "min_accuracy": 0.0})

    candidate = artifact()
    candidate["train_ids"] = []
    await atom._on_candidate(candidate)  # لا حيازة كافية بعد -- يُؤجَّل
    assert len(atom._pending) == 1

    f1 = feature("s1", "buy", 0.1)
    await atom._on_feature(f1)  # لا يزال ناقصاً (1 < 2) -- يبقى مؤجَّلاً، بلا تعليق
    assert len(atom._pending) == 1

    # f2 يكمل الحيازة المطلوبة لـ candidate -- يصادق وينشر، فيعلَّق داخل
    # publish (الحارس مضبوط على نسخة المرشّح الأصلي بالضبط).
    bus.pause_on_version = candidate["model_version"]
    f2 = feature("s2", "buy", 0.2)
    feature_task = asyncio.create_task(atom._on_feature(f2))
    await bus.reached_gate.wait()

    # تزامن حقيقي: إعادة تقديم لنفس model_version (سيناريو مشروع بحسب
    # تعليق الكود نفسه) تصل بينما إعادة المحاولة أعلاه معلَّقة.
    resubmitted = dict(candidate)
    resubmitted["train_ids"] = ["s2"]  # يستبعد الميزة التي وصلت للتوّ -- يُؤجَّل مجدداً
    candidate_task = asyncio.create_task(atom._on_candidate(resubmitted))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    bus.gate.set()
    await asyncio.wait_for(asyncio.gather(feature_task, candidate_task), timeout=5.0)

    reports = bus.payloads(module.EVENT_OUT)
    assert len(reports) == 1 and reports[0]["model_version"] == candidate["model_version"], reports
    assert atom._pending == [resubmitted], atom._pending
