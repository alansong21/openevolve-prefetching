#!/usr/bin/env python3
"""Generate baseline ChampSim miss logs and stats for the combined workflow.

Runs L2C with no prefetcher and LRU replacement, writes per-trace:
  workflows/combined/baseline/<trace_stem>/misses.txt
  workflows/combined/baseline/<trace_stem>/stats.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHAMPSIM_ROOT = REPO_ROOT / "ChampSim"
CHAMPSIM_BIN = CHAMPSIM_ROOT / "bin" / "champsim"
BASELINE_CONFIG = REPO_ROOT / "workflows" / "combined" / "baseline_champsim_config.json"
BASELINE_ROOT = REPO_ROOT / "workflows" / "combined" / "baseline"
COMP_DIR = REPO_ROOT / "openevolve-components"

sys.path.insert(0, str(COMP_DIR))
from champsim_stats import parse_stats  # noqa: E402


def _discover_traces(trace_dir: Path, token: str) -> list[Path]:
    return sorted(
        path
        for path in trace_dir.rglob("*")
        if path.is_file() and token in path.name.lower()
    )


def _configure_and_build() -> None:
    if not BASELINE_CONFIG.exists():
        raise FileNotFoundError(f"Missing baseline config: {BASELINE_CONFIG}")

    dest_cfg = CHAMPSIM_ROOT / "champsim_config.json"
    shutil.copy(BASELINE_CONFIG, dest_cfg)
    subprocess.run(["./config.sh", dest_cfg.name], cwd=CHAMPSIM_ROOT, check=True)
    subprocess.run(["make", f"-j{max(1, os.cpu_count() or 1)}"], cwd=CHAMPSIM_ROOT, check=True)


def _run_trace(trace: Path, warmup: int, simulation: int, miss_log: Path) -> str:
    miss_log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(CHAMPSIM_BIN),
        "--warmup-instructions",
        str(warmup),
        "--simulation-instructions",
        str(simulation),
        "--miss-log",
        str(miss_log),
        str(trace),
    ]
    result = subprocess.run(
        cmd,
        cwd=CHAMPSIM_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trace-dir",
        type=Path,
        default=Path(os.environ.get("CHAMPSIM_TRACE_DIR", "/nfs/traces/SPEC17")),
        help="Directory containing ChampSim traces",
    )
    parser.add_argument(
        "--trace-token",
        default=os.environ.get("CHAMPSIM_TRACE_NAME_TOKEN", "champsimtrace"),
        help="Substring that trace filenames must contain",
    )
    parser.add_argument(
        "--traces",
        nargs="*",
        type=Path,
        help="Explicit trace paths (overrides discovery)",
    )
    parser.add_argument("--warmup", type=int, default=int(os.environ.get("CHAMPSIM_WARMUP_INSTR", 1_000_000)))
    parser.add_argument("--simulation", type=int, default=int(os.environ.get("CHAMPSIM_SIM_INSTR", 1_000_000)))
    parser.add_argument("--force", action="store_true", help="Regenerate even if outputs exist")
    parser.add_argument("--skip-build", action="store_true", help="Assume ChampSim is already built with baseline config")
    args = parser.parse_args()

    traces = args.traces or _discover_traces(args.trace_dir, args.trace_token)
    if not traces:
        print(f"No traces found under {args.trace_dir}", file=sys.stderr)
        return 1

    if not args.skip_build:
        print("Configuring ChampSim with baseline (no L2C prefetcher, LRU replacement)...")
        _configure_and_build()

    for trace in traces:
        out_dir = BASELINE_ROOT / trace.stem
        miss_log = out_dir / "misses.txt"
        stats_json = out_dir / "stats.json"
        stdout_log = out_dir / "stdout.log"

        if miss_log.exists() and stats_json.exists() and not args.force:
            print(f"Skipping cached baseline for {trace.name}")
            continue

        print(f"Running baseline for {trace.name}...")
        stdout = _run_trace(trace, args.warmup, args.simulation, miss_log)
        stats = parse_stats(stdout)
        out_dir.mkdir(parents=True, exist_ok=True)
        stdout_log.write_text(stdout, encoding="utf-8")
        stats_json.write_text(json.dumps(stats.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            f"  ipc={stats.ipc:.4f} l2c_mpki={stats.l2c.mpki(stats.instructions):.2f} "
            f"miss_log={miss_log}"
        )

    print(f"Baseline artifacts stored under {BASELINE_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
