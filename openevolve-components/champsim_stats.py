"""Parse ChampSim plain-text statistics from simulator stdout."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


ROI_MARKER = "Region of Interest Statistics"

IPC_PATTERN = re.compile(
    r"CPU\s+\d+\s+cumulative IPC:\s+([0-9.]+)\s+instructions:\s+(\d+)",
    re.MULTILINE,
)
CACHE_ACCESS_PATTERN = re.compile(
    r"cpu\d+->(?:cpu\d+_)?(?P<cache>L2C|LLC)\s+(?P<kind>LOAD|RFO|TOTAL|PREFETCH)\s+ACCESS:\s+\d+\s+HIT:\s+\d+\s+MISS:\s+(?P<miss>\d+)",
)
PREFETCH_STATS_PATTERN = re.compile(
    r"cpu\d+->(?:cpu\d+_)?(?P<cache>L2C|LLC)\s+PREFETCH REQUESTED:\s+(?P<requested>\d+)\s+ISSUED:\s+(?P<issued>\d+)\s+USEFUL:\s+(?P<useful>\d+)\s+USELESS:\s+(?P<useless>\d+)",
)
MISS_LATENCY_PATTERN = re.compile(
    r"cpu\d+->(?:cpu\d+_)?(?P<cache>L2C|LLC)\s+AVERAGE MISS LATENCY:\s+(?P<latency>[0-9.]+|-)\s+cycles",
)


@dataclass
class CacheStats:
    load_misses: int = 0
    rfo_misses: int = 0
    total_misses: int = 0
    prefetch_misses: int = 0
    pf_requested: int = 0
    pf_issued: int = 0
    pf_useful: int = 0
    pf_useless: int = 0
    avg_miss_latency_cycles: float | None = None

    @property
    def demand_misses(self) -> int:
        return self.load_misses + self.rfo_misses

    def mpki(self, instructions: int) -> float | None:
        if instructions <= 0:
            return None
        return 1000.0 * self.demand_misses / instructions

    def prefetch_accuracy(self) -> float | None:
        denom = self.pf_useful + self.pf_useless
        if denom <= 0:
            return None
        return self.pf_useful / denom

    def to_dict(self, instructions: int) -> dict[str, Any]:
        return {
            "load_misses": self.load_misses,
            "rfo_misses": self.rfo_misses,
            "demand_misses": self.demand_misses,
            "total_misses": self.total_misses,
            "prefetch_misses": self.prefetch_misses,
            "mpki": self.mpki(instructions),
            "pf_requested": self.pf_requested,
            "pf_issued": self.pf_issued,
            "pf_useful": self.pf_useful,
            "pf_useless": self.pf_useless,
            "prefetch_accuracy": self.prefetch_accuracy(),
            "avg_miss_latency_cycles": self.avg_miss_latency_cycles,
        }


@dataclass
class ChampSimStats:
    ipc: float
    instructions: int
    l2c: CacheStats = field(default_factory=CacheStats)
    llc: CacheStats = field(default_factory=CacheStats)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ipc": self.ipc,
            "instructions": self.instructions,
            "l2c": self.l2c.to_dict(self.instructions),
            "llc": self.llc.to_dict(self.instructions),
        }


def _roi_section(stdout: str) -> str:
    marker_idx = stdout.rfind(ROI_MARKER)
    if marker_idx == -1:
        return stdout
    return stdout[marker_idx:]


def parse_stats(stdout: str) -> ChampSimStats:
    """Extract IPC and L2C/LLC statistics from the ROI section of ChampSim stdout."""

    roi = _roi_section(stdout)
    ipc_matches = IPC_PATTERN.findall(roi)
    if not ipc_matches:
        raise ValueError("Could not find cumulative IPC in ChampSim ROI output")

    ipc_str, instructions_str = ipc_matches[-1]
    instructions = int(instructions_str)
    stats = ChampSimStats(ipc=float(ipc_str), instructions=instructions)

    cache_map = {"L2C": stats.l2c, "LLC": stats.llc}

    for match in CACHE_ACCESS_PATTERN.finditer(roi):
        cache_stats = cache_map[match.group("cache")]
        miss_count = int(match.group("miss"))
        kind = match.group("kind")
        if kind == "LOAD":
            cache_stats.load_misses = miss_count
        elif kind == "RFO":
            cache_stats.rfo_misses = miss_count
        elif kind == "TOTAL":
            cache_stats.total_misses = miss_count
        elif kind == "PREFETCH":
            cache_stats.prefetch_misses = miss_count

    for match in PREFETCH_STATS_PATTERN.finditer(roi):
        cache_stats = cache_map[match.group("cache")]
        cache_stats.pf_requested = int(match.group("requested"))
        cache_stats.pf_issued = int(match.group("issued"))
        cache_stats.pf_useful = int(match.group("useful"))
        cache_stats.pf_useless = int(match.group("useless"))

    for match in MISS_LATENCY_PATTERN.finditer(roi):
        cache_stats = cache_map[match.group("cache")]
        latency = match.group("latency")
        cache_stats.avg_miss_latency_cycles = None if latency == "-" else float(latency)

    return stats


def parse_ipc(stdout: str) -> float:
    """Backward-compatible helper that returns only cumulative IPC."""

    return parse_stats(stdout).ipc
