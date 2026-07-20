"""Parse drcachesim cache-simulator output and compute stage-1 proxy scores."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Mapping


_CACHE_HEADER = re.compile(r"^\s*(?P<name>(?:unified\s+)?[\w.-]+)\s*(?:\([^)]*\))?\s*stats:\s*$")
_COUNT = re.compile(
    r"^\s*(?P<label>Warmup hits|Warmup misses|Hits|Misses|Compulsory misses|"
    r"Invalidations|Prefetch hits|Prefetch misses|Child hits):\s*"
    r"(?P<value>[\d,]+)\s*$"
)
_RATE = re.compile(
    r"^\s*(?P<label>Miss rate|Local miss rate|Total miss rate):\s*"
    r"(?P<value>[\d.]+)%\s*$"
)


@dataclass(frozen=True)
class CacheStats:
    name: str
    hits: int = 0
    misses: int = 0
    compulsory_misses: int = 0
    prefetch_hits: int = 0
    prefetch_misses: int = 0
    child_hits: int = 0
    miss_rate: float = 0.0
    total_miss_rate: float = 0.0

    @property
    def accesses(self) -> int:
        return self.hits + self.misses

    @property
    def prefetch_accuracy(self) -> float:
        total = self.prefetch_hits + self.prefetch_misses
        return self.prefetch_hits / total if total else 0.0

    def to_dict(self) -> dict[str, int | float | str]:
        return asdict(self)


def _normalized_label(label: str) -> str:
    return label.lower().replace(" ", "_")


def parse_drcachesim_stats(output: str) -> dict[str, CacheStats]:
    """Return cache-name keyed stats from drcachesim stderr/stdout."""
    raw: dict[str, dict[str, int | float | str]] = {}
    current: dict[str, int | float | str] | None = None

    for line in output.splitlines():
        header = _CACHE_HEADER.match(line)
        if header:
            name = header.group("name").removeprefix("unified ")
            current = {"name": name}
            raw[name] = current
            continue
        if current is None:
            continue
        count = _COUNT.match(line)
        if count:
            label = _normalized_label(count.group("label"))
            if label not in {"warmup_hits", "warmup_misses", "invalidations"}:
                current[label] = int(count.group("value").replace(",", ""))
            continue
        rate = _RATE.match(line)
        if rate:
            label = _normalized_label(rate.group("label"))
            if label in {"miss_rate", "local_miss_rate"}:
                current["miss_rate"] = float(rate.group("value")) / 100.0
            elif label == "total_miss_rate":
                current["total_miss_rate"] = float(rate.group("value")) / 100.0

    return {name: CacheStats(**values) for name, values in raw.items()}


def select_data_cache(
    stats: Mapping[str, CacheStats],
    preferred: tuple[str, ...] = ("L2C", "L2", "LL", "L1D"),
) -> CacheStats:
    for name in preferred:
        if name in stats:
            return stats[name]
    if not stats:
        raise ValueError("drcachesim output contained no cache-stat blocks")
    return list(stats.values())[-1]


def compute_stage1_proxy(
    candidate: CacheStats,
    baseline: CacheStats,
    *,
    traffic_penalty: float = 0.10,
    useless_prefetch_penalty: float = 0.05,
) -> dict[str, float]:
    """Compute a baseline-relative cache proxy; larger is better."""
    base_misses = max(baseline.misses, 1)
    miss_reduction = (baseline.misses - candidate.misses) / base_misses

    base_traffic = max(
        baseline.hits
        + baseline.misses
        + baseline.prefetch_hits
        + baseline.prefetch_misses,
        1,
    )
    candidate_traffic = (
        candidate.hits
        + candidate.misses
        + candidate.prefetch_hits
        + candidate.prefetch_misses
    )
    traffic_growth = max(0.0, (candidate_traffic - base_traffic) / base_traffic)

    candidate_prefetches = candidate.prefetch_hits + candidate.prefetch_misses
    useless_ratio = (
        candidate.prefetch_misses / candidate_prefetches if candidate_prefetches else 0.0
    )
    score = (
        miss_reduction
        - traffic_penalty * traffic_growth
        - useless_prefetch_penalty * useless_ratio
    )
    return {
        "ipc_proxy": score,
        "demand_miss_reduction": miss_reduction,
        "traffic_growth": traffic_growth,
        "useless_prefetch_ratio": useless_ratio,
        "prefetch_accuracy": candidate.prefetch_accuracy,
    }
