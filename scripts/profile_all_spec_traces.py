#!/usr/bin/env python3
"""Profile all SPEC ChampSim traces under /nfs in parallel.

Runs ``profile_trace.py`` logic for every ``*.champsimtrace.xz`` file in the
trace directory and writes JSON profiles to ``workflows/combined/profiles/``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DEFAULT_TRACE_DIR = Path("/nfs/traces/SPEC17")

sys.path.insert(0, str(SCRIPTS_DIR))
from profile_trace import DEFAULT_PROFILE_DIR, profile_trace  # noqa: E402


def _discover_traces(trace_dir: Path) -> list[Path]:
    return sorted(trace_dir.glob("*.champsimtrace.xz"))


def _profile_one(
    trace_path: str,
    max_instructions: int,
    warmup_instructions: int,
    force: bool,
    output_dir: str,
) -> tuple[str, str, str]:
    trace = Path(trace_path)
    out_dir = Path(output_dir)
    output = out_dir / f"{trace.stem}.json"

    if output.exists() and not force:
        return trace.name, "cached", str(output)

    try:
        result = profile_trace(
            trace,
            max_instructions=max_instructions,
            warmup_instructions=warmup_instructions,
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        taxonomy = result.get("access_pattern_taxonomy", "?")
        return trace.name, "ok", f"{taxonomy} -> {output}"
    except Exception as exc:  # pylint: disable=broad-except
        return trace.name, "error", str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trace-dir",
        type=Path,
        default=Path(os.environ.get("CHAMPSIM_TRACE_DIR", DEFAULT_TRACE_DIR)),
        help=f"Directory containing SPEC traces (default: {DEFAULT_TRACE_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
        help=f"Directory for profile JSON files (default: {DEFAULT_PROFILE_DIR})",
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
        "--jobs",
        type=int,
        default=max(1, os.cpu_count() or 1),
        help="Parallel workers (default: CPU count)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing profile JSON files",
    )
    args = parser.parse_args()

    if not args.trace_dir.is_dir():
        print(f"Trace directory not found: {args.trace_dir}", file=sys.stderr)
        return 1

    traces = _discover_traces(args.trace_dir)
    if not traces:
        print(f"No *.champsimtrace.xz files under {args.trace_dir}", file=sys.stderr)
        return 1

    print(f"Profiling {len(traces)} traces from {args.trace_dir} with {args.jobs} workers...")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    ok = cached = failed = 0
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(
                _profile_one,
                str(trace),
                args.max_instructions,
                args.warmup_instructions,
                args.force,
                str(args.output_dir),
            ): trace
            for trace in traces
        }
        for future in as_completed(futures):
            name, status, detail = future.result()
            if status == "ok":
                ok += 1
                print(f"[ok] {name}: {detail}")
            elif status == "cached":
                cached += 1
                print(f"[cached] {name}: {detail}")
            else:
                failed += 1
                print(f"[error] {name}: {detail}", file=sys.stderr)

    print(
        f"Done: {ok} profiled, {cached} cached, {failed} failed "
        f"-> {args.output_dir}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
