#!/usr/bin/env python3
"""Utility to dump evolved ChampSim prefetcher programs as .cc files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil


def dump_programs(
    checkpoints_root: Path,
    program_text_root: Path,
    run_id: str,
) -> Path:
    checkpoint_dirs = sorted(
        (d for d in checkpoints_root.iterdir() if d.is_dir()),
        key=lambda p: p.name,
    )

    if not checkpoint_dirs:
        raise SystemExit(f"No checkpoints found under {checkpoints_root}")

    run_dir = program_text_root / f"run_{run_id}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    for checkpoint_dir in checkpoint_dirs:
        programs_dir = checkpoint_dir / "programs"
        if not programs_dir.is_dir():
            continue

        checkpoint_output = run_dir / checkpoint_dir.name
        checkpoint_output.mkdir(parents=True, exist_ok=True)

        for program_file in sorted(programs_dir.glob("*.json")):
            with open(program_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)

            code = data.get("code")
            if not code:
                continue

            iteration = data.get("iteration_found", "unknown")
            program_id = data.get("id", program_file.stem)
            filename = f"iter{iteration}_{program_id}.cc"
            destination = checkpoint_output / filename

            with open(destination, "w", encoding="utf-8") as out_fh:
                out_fh.write(code)

    return run_dir


def infer_latest_run_id(log_dir: Path) -> str:
    log_files = sorted(log_dir.glob("openevolve_*.log"))
    if not log_files:
        raise SystemExit(f"No log files found under {log_dir}")
    # Extract timestamp portion
    stem = log_files[-1].stem  # openevolve_YYYYMMDD_HHMMSS
    return stem.split("openevolve_")[-1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract evolved ChampSim prefetcher programs")
    parser.add_argument(
        "--run-id",
        help="Run identifier (default: latest log timestamp)",
        default=None,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    output_root = repo_root / "openevolve-components" / "openevolve_output" / "program_text"
    checkpoints_root = repo_root / "openevolve-components" / "openevolve_output" / "checkpoints"
    logs_root = repo_root / "openevolve-components" / "openevolve_output" / "logs"

    if not checkpoints_root.exists():
        raise SystemExit(f"No checkpoints found at {checkpoints_root}")

    run_id = args.run_id or infer_latest_run_id(logs_root)
    run_dir = dump_programs(checkpoints_root, output_root, run_id)
    print(f"Wrote program snapshots to {run_dir}")


if __name__ == "__main__":
    main()
