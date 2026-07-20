#!/usr/bin/env python3
"""Run a combined-workflow ablation with a recorded environment manifest."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path


VARIANTS = {
    "full": {
        "OPENEVOLVE_AGENTIC_MUTATION": "true",
        "OPENEVOLVE_UNIFIED_IMPLEMENTER": "true",
        "OPENEVOLVE_HIERARCHICAL_EVAL": "true",
    },
    "native_mutator": {
        "OPENEVOLVE_AGENTIC_MUTATION": "false",
        "OPENEVOLVE_UNIFIED_IMPLEMENTER": "false",
        "OPENEVOLVE_HIERARCHICAL_EVAL": "true",
    },
    "no_stage1": {
        "OPENEVOLVE_AGENTIC_MUTATION": "true",
        "OPENEVOLVE_UNIFIED_IMPLEMENTER": "true",
        "OPENEVOLVE_HIERARCHICAL_EVAL": "false",
    },
    "native_no_stage1": {
        "OPENEVOLVE_AGENTIC_MUTATION": "false",
        "OPENEVOLVE_UNIFIED_IMPLEMENTER": "false",
        "OPENEVOLVE_HIERARCHICAL_EVAL": "false",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", choices=VARIANTS)
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=Path("workflows/combined/ablations"),
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("provide the OpenEvolve command after --")

    environment = os.environ.copy()
    environment.update(VARIANTS[args.variant])
    run_id = f"{args.variant}-{int(time.time())}"
    environment.setdefault("OPENEVOLVE_RUN_ID", run_id)
    args.manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = args.manifest_dir / f"{run_id}.json"
    payload = {
        "schema_version": 1,
        "variant": args.variant,
        "run_id": environment["OPENEVOLVE_RUN_ID"],
        "environment": VARIANTS[args.variant],
        "command": command,
        "started_at": time.time(),
    }
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    completed = subprocess.run(command, env=environment, check=False)
    payload["finished_at"] = time.time()
    payload["returncode"] = completed.returncode
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
