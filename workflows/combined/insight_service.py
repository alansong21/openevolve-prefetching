"""Insight service for Phase 1 advisor mode (combined workflow)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_COMBINED_DIR = Path(__file__).resolve().parent
if str(_COMBINED_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBINED_DIR))

from agents.miss_log import analyze_miss_logs  # noqa: E402
from agents.drcachesim_analysis import analyze_drcachesim  # noqa: E402
from agents.workload import characterize_workloads  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_DIR = REPO_ROOT / "workflows" / "combined" / "profiles"
DEFAULT_BASELINE_ROOT = REPO_ROOT / "workflows" / "combined" / "baseline"
DEFAULT_TRACE_DIR = Path(os.environ.get("CHAMPSIM_TRACE_DIR", REPO_ROOT / "traces"))
TRACE_TOKEN = os.environ.get("CHAMPSIM_TRACE_NAME_TOKEN", "champsimtrace").strip().lower()


def _discover_trace_names() -> list[str]:
    if not DEFAULT_TRACE_DIR.is_dir():
        return []
    traces = [
        path.name
        for path in DEFAULT_TRACE_DIR.rglob("*")
        if path.is_file() and TRACE_TOKEN in path.name.lower()
    ]
    return sorted(traces)


def _extract_trace_runs(artifacts: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not artifacts:
        return []

    trace_results = artifacts.get("trace_results")
    if not isinstance(trace_results, dict):
        return []

    runs: list[dict[str, Any]] = []
    idx = 1
    while True:
        name_key = f"trace_{idx}_name"
        if name_key not in trace_results:
            break
        name = trace_results[name_key]
        stem = Path(str(name)).stem
        baseline_dir = DEFAULT_BASELINE_ROOT / stem
        runs.append(
            {
                "name": name,
                "miss_log_path": trace_results.get(f"trace_{idx}_miss_log_path"),
                "baseline_miss_log_path": trace_results.get("baseline_miss_log_path")
                or (str(baseline_dir / "misses.txt") if (baseline_dir / "misses.txt").exists() else None),
                "stats": trace_results.get(f"trace_{idx}_stats"),
            }
        )
        idx += 1
    return runs


def build_insight_bundle(
    task_description: str,
    artifacts: dict[str, Any] | None = None,
    token_budget: int = 8000,
    profile_dir: Path | None = None,
) -> str:
    """Merge workload + miss-log advisor outputs for prompt injection."""

    profile_dir = profile_dir or DEFAULT_PROFILE_DIR
    trace_names = _discover_trace_names()
    if not trace_names:
        trace_runs = _extract_trace_runs(artifacts)
        trace_names = [str(run["name"]) for run in trace_runs]

    artifact_data = artifacts or {}
    dr_metrics = artifact_data.get("drcachesim_metrics")
    if not isinstance(dr_metrics, dict):
        dr_metrics = {}
    dr_output = artifact_data.get("drcachesim_output")
    dr_analysis = artifact_data.get("drcachesim_analysis")
    if not isinstance(dr_analysis, str):
        dr_analysis = analyze_drcachesim(
            dr_metrics, dr_output if isinstance(dr_output, str) else None
        )
    storage_analysis = artifact_data.get(
        "storage_report", "=== Storage analysis ===\nNo storage report is available."
    )

    parts = [
        "=== Co-design advisor insights (Phase 1) ===",
        f"Task: {task_description}",
        "",
        characterize_workloads(trace_names, profile_dir),
        "",
        analyze_miss_logs(_extract_trace_runs(artifacts)),
        "",
        dr_analysis,
        "",
        str(storage_analysis),
        "",
        "Use workload biases to steer prefetcher vs replacement edits. "
        "Use miss-log labels (coverage_gap / conflict / capacity / compulsory) "
        "to pick PF coverage vs RP victim/insertion changes. "
        "Cooperate only through metadata — no shared globals across split markers.",
    ]

    bundle = "\n".join(parts).strip()
    if token_budget > 0 and len(bundle) > token_budget:
        bundle = bundle[:token_budget] + "\n... [insight bundle truncated]"
    return bundle
