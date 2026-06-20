#!/usr/bin/env python3
"""Run a standalone ChampSim evaluation for the OpenEvolve prefetcher."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _configure_environment(args: argparse.Namespace) -> None:
    if args.trace_dir is not None:
        os.environ["CHAMPSIM_TRACE_DIR"] = str(args.trace_dir)
    if args.trace_token is not None:
        os.environ["CHAMPSIM_TRACE_NAME_TOKEN"] = args.trace_token
    if args.sim_instr is not None:
        os.environ["CHAMPSIM_SIM_INSTR"] = str(args.sim_instr)
    if args.warmup_instr is not None:
        os.environ["CHAMPSIM_WARMUP_INSTR"] = str(args.warmup_instr)
    if args.parallel is not None:
        os.environ["CHAMPSIM_PARALLEL_TRACES"] = str(args.parallel)
    if args.mem_per_trace_gb is not None:
        os.environ["CHAMPSIM_MEM_PER_TRACE_GB"] = str(args.mem_per_trace_gb)
    if args.reserved_cores is not None:
        os.environ["CHAMPSIM_RESERVED_CORES"] = str(args.reserved_cores)
    if args.reserved_mem_gb is not None:
        os.environ["CHAMPSIM_RESERVED_MEM_GB"] = str(args.reserved_mem_gb)
    if args.jobs is not None:
        os.environ["CHAMPSIM_JOBS"] = str(args.jobs)
    if args.quiet:
        os.environ["CHAMPSIM_STREAM_LOGS"] = "false"


def _parse_args() -> argparse.Namespace:
    repo_root = _repo_root()
    components = repo_root / "openevolve-components"
    default_program = components / "initial_program.cc"
    default_trace_dir = os.environ.get("CHAMPSIM_TRACE_DIR", str(repo_root / "traces"))

    parser = argparse.ArgumentParser(
        description=(
            "Build ChampSim with the OpenEvolve prefetcher and evaluate it on "
            "discovered traces. Parallelism is auto-tuned from CPU and memory "
            "unless --parallel is set."
        )
    )
    parser.add_argument(
        "--program",
        type=Path,
        default=default_program,
        help=f"Prefetcher source to evaluate (default: {default_program})",
    )
    parser.add_argument(
        "--trace-dir",
        type=Path,
        default=None,
        help=f"Directory to search for traces (default: {default_trace_dir})",
    )
    parser.add_argument(
        "--trace-token",
        default=None,
        help="Filename substring filter for traces (default: champsimtrace)",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=None,
        help="Override parallel trace workers (default: auto from CPU + memory)",
    )
    parser.add_argument(
        "--mem-per-trace-gb",
        type=float,
        default=None,
        help="Estimated RAM per parallel trace run in GB (default: 2)",
    )
    parser.add_argument(
        "--reserved-cores",
        type=int,
        default=None,
        help="CPU cores to leave free when auto-selecting parallelism (default: 1)",
    )
    parser.add_argument(
        "--reserved-mem-gb",
        type=float,
        default=None,
        help="System RAM to leave free when auto-selecting parallelism (default: 4)",
    )
    parser.add_argument(
        "--sim-instr",
        type=int,
        default=None,
        help="Simulation instruction count (default: 200000000)",
    )
    parser.add_argument(
        "--warmup-instr",
        type=int,
        default=None,
        help="Warmup instruction count (default: 50000000)",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=None,
        help="make -j jobs for the ChampSim rebuild (default: CPU count)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full metrics and artifacts as JSON",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress live ChampSim stdout streaming",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = _repo_root()
    components = repo_root / "openevolve-components"

    if not args.program.is_file():
        print(f"Error: program not found: {args.program}", file=sys.stderr)
        return 1

    sys.path.insert(0, str(components))
    _configure_environment(args)

    from evaluator import (  # pylint: disable=import-outside-toplevel
        TRACE_DIR,
        TRACE_NAME_TOKEN,
        _compute_max_trace_workers,
        _discover_traces,
        evaluate,
    )

    traces = _discover_traces()
    if not traces:
        print(
            f"Error: no traces found under {TRACE_DIR} matching token '{TRACE_NAME_TOKEN}'",
            file=sys.stderr,
        )
        return 1

    parallel = _compute_max_trace_workers(traces)
    os.environ["CHAMPSIM_PARALLEL_TRACES"] = str(parallel)

    print(f"Program: {args.program.resolve()}")
    print(f"Trace dir: {TRACE_DIR}")
    print(f"Trace token: {TRACE_NAME_TOKEN}")
    print(f"Traces: {len(traces)}")
    for trace in traces:
        print(f"  - {trace.name}")
    print(f"Parallel workers: {parallel}")

    result = evaluate(str(args.program.resolve()))

    if args.json:
        payload = {
            "metrics": result.metrics,
            "artifacts": result.artifacts,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"\nAverage IPC: {result.metrics.get('ipc')}")
        print(f"Traces evaluated: {result.metrics.get('traces_evaluated')}")
        print(f"Successful traces: {result.metrics.get('successful_traces')}")
        for key, value in sorted(result.metrics.items()):
            if key.startswith("trace_") and key.endswith("_ipc"):
                print(f"  {key}: {value}")

        error = (result.artifacts or {}).get("error")
        if error:
            print(f"\nError: {error}", file=sys.stderr)

    ipc = float(result.metrics.get("ipc", 0.0) or 0.0)
    if ipc <= 0.0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
