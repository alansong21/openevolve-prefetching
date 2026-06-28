#!/usr/bin/env python3
"""Offline workload profiler for ChampSim traces.

Reads a raw ``*.champsimtrace.xz`` once and emits a compact JSON profile with
stride/reuse-distance/page-crossing statistics used by the multi-agent workflow.
"""

from __future__ import annotations

import argparse
import json
import lzma
import os
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE_DIR = REPO_ROOT / "workflows" / "combined" / "profiles"

INPUT_INSTR_FMT = "<QBB2s4s2Q4Q"
INPUT_INSTR_SIZE = struct.calcsize(INPUT_INSTR_FMT)
BLOCK_SIZE = 64
PAGE_SIZE = 4096


def _open_trace(path: Path):
    if path.suffix == ".xz":
        return lzma.open(path, "rb")
    return path.open("rb")


def _iter_instructions(trace_path: Path, max_instructions: int, warmup_instructions: int = 0):
    with _open_trace(trace_path) as handle:
        for _ in range(warmup_instructions):
            if handle.read(INPUT_INSTR_SIZE) == b"":
                return
        for _ in range(max_instructions):
            chunk = handle.read(INPUT_INSTR_SIZE)
            if len(chunk) < INPUT_INSTR_SIZE:
                break
            ip, _, _, _, _, _, _, smem0, smem1, smem2, smem3 = struct.unpack(INPUT_INSTR_FMT, chunk)
            addrs = [addr for addr in (smem0, smem1, smem2, smem3) if addr]
            yield ip, addrs


def _bucket_reuse_distance(distance: int) -> str:
    if distance <= 1:
        return "0-1"
    if distance <= 4:
        return "2-4"
    if distance <= 16:
        return "5-16"
    if distance <= 64:
        return "17-64"
    if distance <= 256:
        return "65-256"
    if distance <= 1024:
        return "257-1024"
    return "1025+"


def _classify_stride(delta: int) -> str:
    if delta == 0:
        return "repeat"
    if abs(delta) == BLOCK_SIZE:
        return "cache_line"
    if abs(delta) == PAGE_SIZE:
        return "page"
    if abs(delta) <= 128:
        return "small_stride"
    return "large_stride"


def profile_trace(
    trace_path: Path,
    max_instructions: int = 2_000_000,
    warmup_instructions: int = 0,
) -> dict:
    load_count = 0
    instructions_scanned = 0
    unique_blocks = set()
    reuse_hist: Counter[str] = Counter()
    stride_hist: Counter[str] = Counter()
    page_crossings = 0
    consecutive_pairs = 0
    pc_delta_hist: dict[int, Counter[int]] = defaultdict(Counter)

    last_block_access: dict[int, int] = {}
    last_addr_by_pc: dict[int, int] = {}
    prev_addr: int | None = None

    for instr_idx, (pc, addrs) in enumerate(
        _iter_instructions(trace_path, max_instructions, warmup_instructions)
    ):
        instructions_scanned += 1
        for addr in addrs:
            load_count += 1
            block = addr // BLOCK_SIZE
            unique_blocks.add(block)

            if pc in last_addr_by_pc:
                delta = addr - last_addr_by_pc[pc]
                pc_delta_hist[pc][delta] += 1
            last_addr_by_pc[pc] = addr

            if prev_addr is not None:
                consecutive_pairs += 1
                if (prev_addr // PAGE_SIZE) != (addr // PAGE_SIZE):
                    page_crossings += 1
                stride_hist[_classify_stride(addr - prev_addr)] += 1
            prev_addr = addr

            if block in last_block_access:
                reuse_hist[_bucket_reuse_distance(instr_idx - last_block_access[block])] += 1
            last_block_access[block] = instr_idx

    instructions_scanned = max(1, instructions_scanned)
    page_cross_rate = page_crossings / consecutive_pairs if consecutive_pairs else 0.0
    memory_intensity = load_count / instructions_scanned

    top_pcs = []
    for pc, deltas in sorted(pc_delta_hist.items(), key=lambda item: sum(item[1].values()), reverse=True)[:16]:
        dominant_delta, dominant_count = deltas.most_common(1)[0]
        total = sum(deltas.values())
        top_pcs.append(
            {
                "pc": hex(pc),
                "load_count": total,
                "dominant_delta": dominant_delta,
                "dominant_delta_hex": hex(dominant_delta & ((1 << 64) - 1)),
                "dominant_delta_fraction": dominant_count / total,
            }
        )

    stride_total = sum(stride_hist.values()) or 1
    reuse_total = sum(reuse_hist.values()) or 1

    streaming_score = (
        stride_hist["cache_line"] + stride_hist["page"] + stride_hist["small_stride"]
    ) / stride_total
    irregular_score = stride_hist["large_stride"] / stride_total
    pointer_chasing_score = reuse_hist["1025+"] / reuse_total

    if streaming_score >= 0.55:
        taxonomy = "streaming"
    elif pointer_chasing_score >= 0.25:
        taxonomy = "pointer_chasing"
    elif irregular_score >= 0.35:
        taxonomy = "irregular"
    elif stride_hist["small_stride"] / stride_total >= 0.35:
        taxonomy = "complex_stride"
    elif stride_hist["cache_line"] / stride_total >= 0.35:
        taxonomy = "constant_stride"
    else:
        taxonomy = "mixed"

    return {
        "trace": str(trace_path),
        "trace_name": trace_path.name,
        "warmup_instructions": warmup_instructions,
        "max_instructions": max_instructions,
        "instructions_scanned": instructions_scanned,
        "load_accesses": load_count,
        "memory_intensity": memory_intensity,
        "unique_cache_lines": len(unique_blocks),
        "access_pattern_taxonomy": taxonomy,
        "scores": {
            "streaming": streaming_score,
            "pointer_chasing": pointer_chasing_score,
            "irregular": irregular_score,
        },
        "reuse_distance_histogram": dict(reuse_hist),
        "stride_histogram": dict(stride_hist),
        "page_crossing_rate": page_cross_rate,
        "memory_boundedness": {
            "loads_per_instruction": memory_intensity,
            "unique_lines_per_1k_loads": 1000.0 * len(unique_blocks) / max(load_count, 1),
        },
        "top_pc_delta_summary": top_pcs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path, help="Path to a ChampSim trace (.xz or raw)")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSON path (default: workflows/combined/profiles/<trace_stem>.json)",
    )
    parser.add_argument(
        "--warmup-instructions",
        "-w",
        type=int,
        default=int(os.environ.get("CHAMPSIM_WARMUP_INSTR", 0)),
        help="Skip this many trace instructions before profiling (default: 0, or CHAMPSIM_WARMUP_INSTR)",
    )
    parser.add_argument(
        "--max-instructions",
        "-i",
        type=int,
        default=int(os.environ.get("CHAMPSIM_SIM_INSTR", 2_000_000)),
        help="Instructions to profile after warmup (default: 2M, or CHAMPSIM_SIM_INSTR)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing cached profile",
    )
    args = parser.parse_args()

    if not args.trace.exists():
        print(f"Trace not found: {args.trace}", file=sys.stderr)
        return 1

    output = args.output or (DEFAULT_PROFILE_DIR / f"{args.trace.stem}.json")
    if output.exists() and not args.force:
        print(f"Using cached profile: {output}")
        return 0

    profile = profile_trace(
        args.trace,
        max_instructions=args.max_instructions,
        warmup_instructions=args.warmup_instructions,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote workload profile: {output}")
    print(f"taxonomy={profile['access_pattern_taxonomy']} loads={profile['load_accesses']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
