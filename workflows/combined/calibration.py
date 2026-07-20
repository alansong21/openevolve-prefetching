"""Persistent ridge calibration from drcachesim features to ChampSim IPC delta."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_PATH = Path(__file__).resolve().parent / "state" / "proxy_calibration.json"
FEATURES = (
    "demand_miss_reduction",
    "traffic_growth",
    "useless_prefetch_ratio",
    "prefetch_accuracy",
)


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    n = len(vector)
    augmented = [matrix[row][:] + [vector[row]] for row in range(n)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(n):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                augmented[row][idx] - factor * augmented[column][idx]
                for idx in range(n + 1)
            ]
    return [augmented[row][-1] for row in range(n)]


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index + 1
        while end < len(order) and values[order[end]] == values[order[index]]:
            end += 1
        rank = (index + end - 1) / 2.0
        for position in order[index:end]:
            ranks[position] = rank
        index = end
    return ranks


def _correlation(left: list[float], right: list[float]) -> float:
    if len(left) < 2:
        return 0.0
    left_ranks, right_ranks = _ranks(left), _ranks(right)
    left_mean = sum(left_ranks) / len(left_ranks)
    right_mean = sum(right_ranks) / len(right_ranks)
    numerator = sum(
        (x - left_mean) * (y - right_mean)
        for x, y in zip(left_ranks, right_ranks)
    )
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left_ranks)
        * sum((y - right_mean) ** 2 for y in right_ranks)
    )
    return numerator / denominator if denominator else 0.0


@dataclass
class ProxyCalibration:
    observations: list[dict[str, Any]] = field(default_factory=list)
    coefficients: list[float] | None = None
    spearman: float = 0.0
    mean_absolute_error: float | None = None
    minimum_pairs: int = 5

    @classmethod
    def load(cls, path: Path | None = None) -> "ProxyCalibration":
        target = path or Path(os.environ.get("HIER_CALIBRATION_PATH", DEFAULT_PATH))
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return cls()
        return cls(
            observations=list(data.get("observations", [])),
            coefficients=data.get("coefficients"),
            spearman=float(data.get("spearman", 0.0)),
            mean_absolute_error=data.get("mean_absolute_error"),
            minimum_pairs=int(data.get("minimum_pairs", 5)),
        )

    def save(self, path: Path | None = None) -> None:
        target = path or Path(os.environ.get("HIER_CALIBRATION_PATH", DEFAULT_PATH))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "features": FEATURES,
                    "observations": self.observations[-200:],
                    "coefficients": self.coefficients,
                    "spearman": self.spearman,
                    "mean_absolute_error": self.mean_absolute_error,
                    "minimum_pairs": self.minimum_pairs,
                    "trusted": self.trusted,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    @property
    def trusted(self) -> bool:
        return (
            self.coefficients is not None
            and len(self.observations) >= self.minimum_pairs
            and self.spearman >= 0.2
        )

    @staticmethod
    def feature_vector(metrics: dict[str, Any]) -> list[float]:
        return [1.0] + [float(metrics.get(name, 0.0)) for name in FEATURES]

    def predict(self, metrics: dict[str, Any], *, require_trusted: bool = True) -> float | None:
        if self.coefficients is None or (require_trusted and not self.trusted):
            return None
        return sum(
            coefficient * value
            for coefficient, value in zip(
                self.coefficients, self.feature_vector(metrics)
            )
        )

    def add_observation(self, metrics: dict[str, Any], ipc_delta: float) -> None:
        self.observations.append(
            {
                "features": {
                    name: float(metrics.get(name, 0.0)) for name in FEATURES
                },
                "raw_proxy": float(metrics.get("ipc_proxy", 0.0)),
                "ipc_delta": float(ipc_delta),
            }
        )
        self.fit()

    def fit(self, ridge: float = 1e-3) -> None:
        if len(self.observations) < self.minimum_pairs:
            self.coefficients = None
            return
        rows = [
            [1.0] + [float(item["features"].get(name, 0.0)) for name in FEATURES]
            for item in self.observations
        ]
        targets = [float(item["ipc_delta"]) for item in self.observations]
        width = len(rows[0])
        gram = [
            [
                sum(row[left] * row[right] for row in rows)
                + (ridge if left == right and left != 0 else 0.0)
                for right in range(width)
            ]
            for left in range(width)
        ]
        rhs = [
            sum(row[column] * target for row, target in zip(rows, targets))
            for column in range(width)
        ]
        self.coefficients = _solve(gram, rhs)
        if self.coefficients is None:
            return
        predictions = [
            sum(coefficient * value for coefficient, value in zip(self.coefficients, row))
            for row in rows
        ]
        self.spearman = _correlation(predictions, targets)
        self.mean_absolute_error = sum(
            abs(predicted - actual)
            for predicted, actual in zip(predictions, targets)
        ) / len(targets)
