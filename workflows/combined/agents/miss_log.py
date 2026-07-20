"""Miss-log analysis agent (deterministic, reads misses.txt + baseline)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BLOCK_SIZE = 64
L2_SETS = 2048  # matches workflows/combined/champsim_config.json (DPC4 1C.limitBW)


@dataclass(frozen=True)
class MissEntry:
    cache: str
    cpu: int
    pc: int
    address: int
    count: int

    @property
    def block(self) -> int:
        return self.address // BLOCK_SIZE

    @property
    def set_index(self) -> int:
        return self.block % L2_SETS


def _parse_pc(value: str) -> int:
    return int(value, 16)


def _parse_address(value: str) -> int:
    return int(value, 16)


def load_miss_log(path: Path, cache_filter: str = "L2C") -> list[MissEntry]:
    if not path.is_file():
        return []

    entries: list[MissEntry] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        header = handle.readline()
        if not header:
            return []
        for line in handle:
            parts = line.split()
            if len(parts) < 5:
                continue
            cache, cpu_s, pc_s, addr_s, count_s = parts[0], parts[1], parts[2], parts[3], parts[4]
            if cache_filter not in cache:
                continue
            pc = _parse_pc(pc_s)
            if pc == 0:
                continue
            entries.append(
                MissEntry(
                    cache=cache,
                    cpu=int(cpu_s),
                    pc=pc,
                    address=_parse_address(addr_s),
                    count=int(count_s),
                )
            )
    return entries


def _dominant_delta(entries: list[MissEntry]) -> tuple[int | None, float]:
    by_pc: dict[int, list[int]] = defaultdict(list)
    for entry in entries:
        by_pc[entry.pc].append(entry.address)

    deltas: Counter[int] = Counter()
    for addrs in by_pc.values():
        if len(addrs) < 2:
            continue
        sorted_addrs = sorted(addrs)
        for prev, curr in zip(sorted_addrs, sorted_addrs[1:]):
            deltas[curr - prev] += 1

    if not deltas:
        return None, 0.0
    delta, count = deltas.most_common(1)[0]
    return delta, count / sum(deltas.values())


def _label_miss_pattern(
    pc_misses: int,
    unique_blocks: int,
    set_spread: int,
    baseline_misses: int,
) -> str:
    if baseline_misses == 0 and pc_misses >= 10:
        return "coverage_gap"
    if unique_blocks >= 8 and set_spread >= 4:
        return "conflict"
    if unique_blocks >= 16:
        return "capacity"
    if pc_misses >= 5 and unique_blocks <= 3:
        return "compulsory"
    return "mixed"


def _rank_pc_hypotheses(
    entries: list[MissEntry],
    baseline_entries: list[MissEntry],
) -> list[str]:
    baseline_by_pc: Counter[int] = Counter()
    for entry in baseline_entries:
        baseline_by_pc[entry.pc] += entry.count

    by_pc: dict[int, list[MissEntry]] = defaultdict(list)
    for entry in entries:
        by_pc[entry.pc].append(entry)

    ranked: list[tuple[int, int, int, int]] = []
    for pc, pc_entries in by_pc.items():
        total = sum(e.count for e in pc_entries)
        unique_blocks = len({e.block for e in pc_entries})
        set_spread = len({e.set_index for e in pc_entries})
        ranked.append((total, pc, unique_blocks, set_spread))

    ranked.sort(reverse=True)
    lines: list[str] = []
    for total, pc, unique_blocks, set_spread in ranked[:12]:
        pc_entries = by_pc[pc]
        addrs = sorted({hex(e.address) for e in pc_entries})
        label = _label_miss_pattern(total, unique_blocks, set_spread, baseline_by_pc.get(pc, 0))
        delta, delta_frac = _dominant_delta(pc_entries)
        delta_text = f"dominant_delta={hex(delta & ((1 << 64) - 1))} ({delta_frac:.0%})" if delta is not None else "no_stride"
        sample_addrs = ", ".join(addrs[:3])
        if len(addrs) > 3:
            sample_addrs += f", ... (+{len(addrs) - 3} more)"
        lines.append(
            f"  [{label}] PC {hex(pc)}: {total} L2C demand misses, "
            f"{unique_blocks} unique lines, {set_spread} sets — {delta_text}; "
            f"sample addresses: {sample_addrs}"
        )
    return lines


def _pf_pollution_note(stats: dict | None) -> str | None:
    if not stats:
        return None
    l2c = stats.get("l2c") if isinstance(stats.get("l2c"), dict) else {}
    useful = int(l2c.get("pf_useful", 0))
    useless = int(l2c.get("pf_useless", 0))
    issued = int(l2c.get("pf_issued", 0))
    if issued == 0:
        return None
    accuracy = useful / max(useful + useless, 1)
    if useless > useful and useless >= 10:
        return (
            f"Prefetch pollution signal: pf_useful={useful}, pf_useless={useless}, "
            f"accuracy={accuracy:.1%} — demote dead prefetches in replacement."
        )
    if useful > 0 and accuracy >= 0.5:
        return (
            f"Prefetch useful: pf_useful={useful}, pf_useless={useless}, "
            f"accuracy={accuracy:.1%} — protect useful prefetched lines on fill."
        )
    return None


def analyze_miss_logs(
    trace_runs: Iterable[dict[str, str | dict | None]],
) -> str:
    """Analyze candidate miss logs against baselines for each trace."""

    sections: list[str] = ["=== Miss-log analysis ==="]
    any_data = False

    for run in trace_runs:
        name = str(run.get("name", "unknown"))
        miss_path = run.get("miss_log_path")
        baseline_path = run.get("baseline_miss_log_path")
        stats = run.get("stats")

        sections.append(f"\nTrace: {name}")
        if not miss_path:
            sections.append("  (no candidate miss log — enable CHAMPSIM_MISS_LOG=true)")
            continue

        path = Path(str(miss_path))
        entries = load_miss_log(path)
        if not entries:
            sections.append(f"  (empty or missing L2C misses in {path})")
            continue

        any_data = True
        baseline_entries: list[MissEntry] = []
        if baseline_path:
            baseline_entries = load_miss_log(Path(str(baseline_path)))

        total_misses = sum(e.count for e in entries)
        sections.append(f"  Candidate L2C demand misses: {total_misses} ({len(entries)} pc,addr pairs)")
        if baseline_entries:
            baseline_total = sum(e.count for e in baseline_entries)
            delta = total_misses - baseline_total
            sections.append(
                f"  Baseline L2C demand misses: {baseline_total} (delta {delta:+d})"
            )

        hypotheses = _rank_pc_hypotheses(entries, baseline_entries)
        if hypotheses:
            sections.append("  Ranked hypotheses:")
            sections.extend(hypotheses)
        else:
            sections.append("  No ranked PC hypotheses.")

        pollution = _pf_pollution_note(stats if isinstance(stats, dict) else None)
        if pollution:
            sections.append(f"  {pollution}")

    if not any_data:
        sections.append(
            "\nNo candidate miss logs available yet. After the first evaluation, "
            "re-run evolution to inject miss-log insights."
        )

    return "\n".join(sections).strip()
