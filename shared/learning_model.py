"""Deterministic learning helpers for atoms 360-368.

This module contains no Core knowledge and no order/execution logic.  It keeps
training and inference on one versioned feature contract so an artifact cannot
silently consume a different vector after restart.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

CLASSES = ("buy", "sell", "neutral")
FEATURE_SCHEMA_VERSION = "validated-tick-v3"
FEATURE_NAMES = (
    "return_1", "return_3", "return_5", "bid_return_1", "ask_return_1",
    "spread_pct", "mid_move_abs", "volume_log", "tick_direction",
    "volatility_8",
)


def finite(value: Any, fallback: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def schema_hash(names: Iterable[str] = FEATURE_NAMES) -> str:
    return stable_hash({"version": FEATURE_SCHEMA_VERSION, "names": list(names)})


def valid_vector(values: Any, size: int | None = None) -> list[float] | None:
    if not isinstance(values, (list, tuple)):
        return None
    expected = len(FEATURE_NAMES) if size is None else size
    if len(values) != expected:
        return None
    result: list[float] = []
    for value in values:
        number = finite(value)
        if number is None:
            return None
        result.append(number)
    return result


def normalize_fit(rows: list[list[float]]) -> tuple[list[float], list[float]]:
    if not rows:
        raise ValueError("EMPTY_TRAINING_SET")
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        raise ValueError("INCONSISTENT_FEATURE_WIDTH")
    means = [sum(row[j] for row in rows) / len(rows) for j in range(width)]
    scales = []
    for j, mean in enumerate(means):
        variance = sum((row[j] - mean) ** 2 for row in rows) / len(rows)
        scales.append(max(math.sqrt(variance), 1e-12))
    return means, scales


def normalize(values: list[float], means: list[float], scales: list[float]) -> list[float]:
    if not (len(values) == len(means) == len(scales)):
        raise ValueError("MODEL_FEATURE_WIDTH_MISMATCH")
    return [(value - means[i]) / scales[i] for i, value in enumerate(values)]


def softmax(logits: list[float]) -> list[float]:
    if not logits:
        return []
    top = max(logits)
    exps = [math.exp(max(-60.0, min(60.0, value - top))) for value in logits]
    total = sum(exps)
    return [value / total for value in exps]


def predict(artifact: dict[str, Any], vector: list[float]) -> dict[str, float]:
    names = artifact.get("feature_names")
    if names != list(FEATURE_NAMES) or artifact.get("feature_schema_hash") != schema_hash():
        raise ValueError("FEATURE_SCHEMA_MISMATCH")
    means = valid_vector(artifact.get("means"))
    scales = valid_vector(artifact.get("scales"))
    if means is None or scales is None:
        raise ValueError("INVALID_PREPROCESSOR")
    weights = artifact.get("weights")
    bias = artifact.get("bias")
    if not isinstance(weights, list) or len(weights) != len(CLASSES):
        raise ValueError("INVALID_MODEL_WEIGHTS")
    if not isinstance(bias, list) or len(bias) != len(CLASSES):
        raise ValueError("INVALID_MODEL_BIAS")
    clean_weights = [valid_vector(row) for row in weights]
    clean_bias = valid_vector(bias, len(CLASSES))
    if any(row is None for row in clean_weights) or clean_bias is None:
        raise ValueError("INVALID_MODEL_ARTIFACT")
    x = normalize(vector, means, scales)
    logits = [clean_bias[k] + sum(clean_weights[k][j] * x[j]
                                  for j in range(len(x)))
              for k in range(len(CLASSES))]
    probs = softmax(logits)
    return {"p_" + name: probs[i] for i, name in enumerate(CLASSES)}


def train_softmax(rows: list[list[float]], labels: list[str], *,
                  epochs: int, learning_rate: float, l2: float) -> dict[str, Any]:
    """Small deterministic multinomial logistic regression (full-batch GD)."""
    if len(rows) != len(labels) or not rows:
        raise ValueError("INVALID_TRAINING_SET")
    if any(label not in CLASSES for label in labels):
        raise ValueError("INVALID_LABEL")
    means, scales = normalize_fit(rows)
    xs = [normalize(row, means, scales) for row in rows]
    width = len(means)
    weights = [[0.0] * width for _ in CLASSES]
    bias = [0.0] * len(CLASSES)
    rate = float(learning_rate)
    penalty = float(l2)
    for _ in range(max(1, int(epochs))):
        gw = [[0.0] * width for _ in CLASSES]
        gb = [0.0] * len(CLASSES)
        for x, label in zip(xs, labels):
            probs = softmax([bias[k] + sum(weights[k][j] * x[j]
                                            for j in range(width))
                             for k in range(len(CLASSES))])
            target = CLASSES.index(label)
            for k in range(len(CLASSES)):
                error = probs[k] - (1.0 if k == target else 0.0)
                gb[k] += error
                for j in range(width):
                    gw[k][j] += error * x[j]
        count = float(len(xs))
        for k in range(len(CLASSES)):
            bias[k] -= rate * gb[k] / count
            for j in range(width):
                gradient = gw[k][j] / count + penalty * weights[k][j]
                weights[k][j] -= rate * gradient
    artifact = {
        "algorithm": "multinomial_logistic_full_batch_v1",
        "classes": list(CLASSES),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_schema_hash": schema_hash(),
        "feature_names": list(FEATURE_NAMES),
        "means": means, "scales": scales, "weights": weights, "bias": bias,
    }
    artifact["artifact_hash"] = stable_hash(artifact)
    return artifact


def classification_metrics(artifact: dict[str, Any], rows: list[list[float]],
                           labels: list[str]) -> dict[str, Any]:
    if not rows or len(rows) != len(labels):
        raise ValueError("EMPTY_VALIDATION_SET")
    correct = 0
    class_total = {name: 0 for name in CLASSES}
    class_correct = {name: 0 for name in CLASSES}
    log_loss = 0.0
    brier = 0.0
    for row, label in zip(rows, labels):
        probs = predict(artifact, row)
        predicted = max(CLASSES, key=lambda name: probs["p_" + name])
        class_total[label] += 1
        if predicted == label:
            correct += 1
            class_correct[label] += 1
        log_loss -= math.log(max(1e-15, probs["p_" + label]))
        brier += sum((probs["p_" + name] - (1.0 if name == label else 0.0)) ** 2
                     for name in CLASSES) / len(CLASSES)
    recalls = [class_correct[name] / class_total[name]
               for name in CLASSES if class_total[name]]
    return {
        "accuracy": correct / len(rows),
        "balanced_accuracy": sum(recalls) / len(recalls) if recalls else 0.0,
        "log_loss": log_loss / len(rows),
        "brier_score": brier / len(rows),
        "class_counts": class_total,
    }


class TickFeatures:
    """Per-scope rolling features for ``market.tick.validated``."""
    def __init__(self) -> None:
        self._scopes: dict[str, dict[str, Any]] = {}

    def build(self, payload: dict[str, Any]) -> tuple[list[float] | None, str]:
        symbol = str(payload.get("symbol") or "").strip()
        timeframe = str(payload.get("timeframe") or "tick")
        account = str(payload.get("account_id") or "")
        broker = str(payload.get("broker") or "")
        scope = "\x1f".join((account, broker, symbol, timeframe))
        price = finite(payload.get("price", payload.get("close")))
        bid = finite(payload.get("bid"), price)
        ask = finite(payload.get("ask"), price)
        if (not symbol or price is None or bid is None or ask is None
                or price <= 0 or bid <= 0 or ask < bid):
            return None, scope
        state = self._scopes.setdefault(scope, {
            "prices": [], "last_bid": bid, "last_ask": ask})
        history = state["prices"]
        previous = history[-1] if history else price
        previous_bid = finite(state.get("last_bid"), bid) or bid
        previous_ask = finite(state.get("last_ask"), ask) or ask

        def ret(back: int) -> float:
            base = history[-back] if len(history) >= back else previous
            return price / base - 1.0 if base else 0.0

        move = price / previous - 1.0 if previous else 0.0
        recent = (history + [price])[-8:]
        mean = sum(recent) / len(recent)
        volatility = (math.sqrt(sum((value / mean - 1.0) ** 2
                                    for value in recent) / len(recent))
                      if mean else 0.0)
        volume = max(0.0, finite(payload.get("volume"), 0.0) or 0.0)
        vector = [
            move, ret(3), ret(5),
            bid / previous_bid - 1.0 if previous_bid else 0.0,
            ask / previous_ask - 1.0 if previous_ask else 0.0,
            (ask - bid) / price,
            abs(move), math.log1p(volume),
            1.0 if move > 0 else -1.0 if move < 0 else 0.0,
            volatility,
        ]
        history.append(price)
        state["prices"] = history[-8:]
        state["last_bid"] = bid
        state["last_ask"] = ask
        return vector, scope

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {key: {"prices": list(value["prices"]),
                      "last_bid": value["last_bid"],
                      "last_ask": value["last_ask"]}
                for key, value in self._scopes.items()}

    def restore(self, state: Any) -> None:
        self._scopes = {}
        if not isinstance(state, dict):
            return
        for key, row in state.items():
            if not isinstance(key, str) or not isinstance(row, dict):
                continue
            values = row.get("prices")
            bid = finite(row.get("last_bid"))
            ask = finite(row.get("last_ask"))
            if not isinstance(values, list) or bid is None or ask is None:
                continue
            clean = [finite(value) for value in values]
            if all(value is not None for value in clean):
                self._scopes[key] = {"prices": [float(value) for value in clean[-8:]],
                                     "last_bid": bid, "last_ask": ask}
