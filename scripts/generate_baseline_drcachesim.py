#!/usr/bin/env python3
"""Run and cache baseline drcachesim policy combinations on L2C→LLC configs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMBINED = REPO_ROOT / "workflows" / "combined"
sys.path.insert(0, str(COMBINED))

from drcachesim_runner import (  # noqa: E402
    TraceInput,
    load_hierarchy_geometry,
    run_trace,
)
from drcachesim_stats import parse_drcachesim_stats  # noqa: E402


def trace_for_counts(counts_path: Path) -> Path:
    suffix = ".counts.json"
    text = str(counts_path)
    if not text.endswith(suffix):
        raise ValueError(f"not a counts file: {counts_path}")
    return Path(text[: -len(suffix)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trace-root", type=Path, default=REPO_ROOT / "l2_drcachesim_out"
    )
    parser.add_argument(
        "--launcher",
        type=Path,
        default=REPO_ROOT
        / "DynamoRIO"
        / "build"
        / "clients"
        / "bin64"
        / "drmemtrace_launcher",
    )
    parser.add_argument(
        "--champsim-config",
        type=Path,
        default=COMBINED / "champsim_config.json",
        help="ChampSim config whose L2C/LLC geometry sizes the hierarchy",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=COMBINED / "baseline" / "drcachesim_baselines.json",
    )
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not args.launcher.is_file():
        parser.error(f"drcachesim launcher not found: {args.launcher}")

    hierarchy = load_hierarchy_geometry(args.champsim_config)
    entries: dict[str, object] = {}
    count_files = sorted(args.trace_root.rglob("*.counts.json"))
    if args.limit > 0:
        count_files = count_files[: args.limit]
    for counts_path in count_files:
        trace_path = trace_for_counts(counts_path)
        if not trace_path.is_file():
            continue
        counts = json.loads(counts_path.read_text(encoding="utf-8"))
        workload = str(trace_path.parent.relative_to(args.trace_root))
        trace = TraceInput(
            name=workload,
            trace=trace_path,
            warmup_refs=int(counts.get("warmup_refs", 0)),
            sim_refs=int(counts.get("sim_refs", 0)),
        )
        entries[workload] = {}
        for replacement in ("LRU", "LFU", "FIFO"):
            for prefetcher in ("none", "nextline"):
                stats, output = run_trace(
                    trace,
                    launcher=args.launcher,
                    replacement=replacement,
                    prefetcher=prefetcher,
                    timeout=args.timeout,
                    geometry=hierarchy,
                )
                entries[workload][f"{replacement}_{prefetcher}"] = {
                    "replacement": replacement,
                    "prefetcher": prefetcher,
                    "hierarchy": {
                        "l2c": {
                            "sets": hierarchy.l2c.sets,
                            "ways": hierarchy.l2c.ways,
                            "line_size": hierarchy.line_size,
                            "size_bytes": hierarchy.l2c.size_bytes,
                            "latency_cycles": hierarchy.l2c.latency_cycles,
                        },
                        "llc": {
                            "sets": hierarchy.llc.sets,
                            "ways": hierarchy.llc.ways,
                            "size_bytes": hierarchy.llc.size_bytes,
                            "latency_cycles": hierarchy.llc.latency_cycles,
                        },
                        "physical_memory": hierarchy.physical_memory,
                        "memory_latency_modeled": False,
                    },
                    "modeled_cache": stats.to_dict(),
                    "caches": {
                        name: value.to_dict()
                        for name, value in parse_drcachesim_stats(output).items()
                    },
                }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "launcher": str(args.launcher),
                "champsim_config": str(args.champsim_config),
                "hierarchy": {
                    "l2c": {
                        "sets": hierarchy.l2c.sets,
                        "ways": hierarchy.l2c.ways,
                        "line_size": hierarchy.line_size,
                        "size_bytes": hierarchy.l2c.size_bytes,
                        "size_label": hierarchy.l2c.size_label,
                        "latency_cycles": hierarchy.l2c.latency_cycles,
                    },
                    "llc": {
                        "sets": hierarchy.llc.sets,
                        "ways": hierarchy.llc.ways,
                        "size_bytes": hierarchy.llc.size_bytes,
                        "size_label": hierarchy.llc.size_label,
                        "latency_cycles": hierarchy.llc.latency_cycles,
                    },
                    "physical_memory": hierarchy.physical_memory,
                    "memory_latency_modeled": False,
                },
                "workloads": entries,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(entries)} workload baselines to {args.output}")
    print(
        f"Hierarchy: L2C {hierarchy.l2c.sets}x{hierarchy.l2c.ways} "
        f"({hierarchy.l2c.size_label}) → LLC {hierarchy.llc.sets}x{hierarchy.llc.ways} "
        f"({hierarchy.llc.size_label}) from {args.champsim_config}"
    )
    print(
        "Note: ChampSim DRAM timing is not modeled by stock drcachesim; "
        "values are recorded for documentation only."
    )
    if not entries:
        print(
            "No trace files were found next to the counts JSON files.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
