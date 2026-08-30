#!/usr/bin/env python3
"""Compile every prefetcher seed with ChampSim.

Copies each openevolve-components/seeds/*.cc into initial_program.cc, rebuilds
ChampSim, and reports which seeds compile. Restores the original
initial_program.cc afterward.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SEEDS_DIR = REPO_ROOT / "openevolve-components" / "seeds"
INITIAL_PROGRAM = REPO_ROOT / "openevolve-components" / "initial_program.cc"
CHAMPSIM_ROOT = REPO_ROOT / "ChampSim"
PREFETCHER_OBJ_DIR = CHAMPSIM_ROOT / ".csconfig" / "modules" / "prefetcher" / "openevolve_prefetcher"
PREFETCHER_OBJ = PREFETCHER_OBJ_DIR / "openevolve_prefetcher.o"
CHAMPSIM_BIN = CHAMPSIM_ROOT / "bin" / "champsim"


def default_jobs() -> int:
    env = os.environ.get("CHAMPSIM_JOBS")
    if env:
        return max(1, int(env))
    return max(1, os.cpu_count() or 1)


def discover_seeds() -> list[Path]:
    return sorted(path for path in SEEDS_DIR.glob("*.cc") if path.is_file())


def invalidate_prefetcher() -> None:
    shutil.rmtree(PREFETCHER_OBJ_DIR, ignore_errors=True)
    try:
        CHAMPSIM_BIN.unlink()
    except FileNotFoundError:
        pass


def build_champsim(jobs: int, timeout: int, object_only: bool) -> subprocess.CompletedProcess[str]:
    if object_only:
        target = str(PREFETCHER_OBJ.relative_to(CHAMPSIM_ROOT))
        cmd = ["make", f"-j{jobs}", target]
    else:
        cmd = ["make", f"-j{jobs}"]
    return subprocess.run(
        cmd,
        cwd=CHAMPSIM_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def compile_seed(seed: Path, jobs: int, timeout: int, object_only: bool) -> tuple[bool, str]:
    shutil.copy(seed, INITIAL_PROGRAM)
    invalidate_prefetcher()
    try:
        result = build_champsim(jobs, timeout, object_only)
    except subprocess.TimeoutExpired as exc:
        return False, f"timed out after {timeout}s\n{exc.stderr or exc.stdout or ''}"

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return False, output

    artifact = PREFETCHER_OBJ if object_only else CHAMPSIM_BIN
    if not artifact.exists():
        return False, f"build succeeded but missing {artifact}\n{output}"
    return True, output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=Path,
        help="Specific seed .cc files (default: all openevolve-components/seeds/*.cc)",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=default_jobs(),
        help="make -jN (default: CHAMPSIM_JOBS or CPU count)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("CHAMPSIM_BUILD_TIMEOUT", 600)),
        help="Per-seed build timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--object-only",
        action="store_true",
        help="Compile only openevolve_prefetcher.o instead of linking bin/champsim",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue after a seed fails",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        help="Write per-seed make logs here (default: a temp directory)",
    )
    args = parser.parse_args()

    if not CHAMPSIM_ROOT.exists():
        print(f"ChampSim root not found: {CHAMPSIM_ROOT}", file=sys.stderr)
        return 2
    if not INITIAL_PROGRAM.exists():
        print(f"Missing {INITIAL_PROGRAM}", file=sys.stderr)
        return 2

    seeds = [path.resolve() for path in args.seeds] if args.seeds else discover_seeds()
    if not seeds:
        print(f"No seed .cc files found in {SEEDS_DIR}", file=sys.stderr)
        return 2
    missing = [path for path in seeds if not path.exists()]
    if missing:
        print("Missing seed files:", file=sys.stderr)
        for path in missing:
            print(f"  {path}", file=sys.stderr)
        return 2

    log_dir = args.log_dir or Path(tempfile.mkdtemp(prefix="seed_compile_"))
    log_dir.mkdir(parents=True, exist_ok=True)

    fd, backup_name = tempfile.mkstemp(prefix="initial_program_", suffix=".cc")
    os.close(fd)
    backup = Path(backup_name)
    shutil.copy(INITIAL_PROGRAM, backup)

    passed: list[str] = []
    failed: list[str] = []
    attempted = 0

    print(f"Compiling {len(seeds)} seed(s) with ChampSim (-j{args.jobs})")
    print(f"Logs: {log_dir}")
    print()

    try:
        for index, seed in enumerate(seeds, start=1):
            name = seed.name
            attempted = index
            print(f"[{index}/{len(seeds)}] {name} ...", flush=True)
            ok, output = compile_seed(seed, args.jobs, args.timeout, args.object_only)
            log_path = log_dir / f"{seed.stem}.log"
            log_path.write_text(output)

            if ok:
                print(f"  PASS  ({log_path})")
                passed.append(name)
                continue

            print(f"  FAIL  ({log_path})")
            tail = "\n".join(output.strip().splitlines()[-20:])
            if tail:
                print(tail)
            failed.append(name)
            if not args.keep_going:
                break
    finally:
        shutil.copy(backup, INITIAL_PROGRAM)
        backup.unlink(missing_ok=True)
        print(f"\nRestored {INITIAL_PROGRAM}")

    print()
    print(f"Passed: {len(passed)}/{attempted}")
    for name in passed:
        print(f"  PASS  {name}")
    for name in failed:
        print(f"  FAIL  {name}")

    if failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
