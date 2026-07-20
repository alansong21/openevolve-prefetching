"""Evaluator for joint prefetcher + replacement evolution.

The combined initial program packs two independent C++ translation units in
one file, separated by ``// === OPENEVOLVE_PREFETCHER_BEGIN === / END`` and
``// === OPENEVOLVE_REPLACEMENT_BEGIN === / END`` markers. This evaluator:

  1. Splits the LLM-produced candidate file on those markers.
  2. Writes the prefetcher half to ``openevolve-components/initial_program.cc``
     and the replacement half to ``openevolve-components/initial_replacement.cc``.
  3. Reuses the solo-prefetcher evaluator's build / run / parsing pipeline
     under a different ChampSim config (``workflows/combined/champsim_config.json``)
     and arranges for the replacement-policy build object to be invalidated on
     every iteration so the new source actually takes effect.

The base evaluator is monkey-patched (not modified) so the standalone
prefetcher workflow keeps behaving exactly as before.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Tuple

from drcachesim_runner import build_candidate_plugin, evaluate_stage1_policy
from hierarchical_state import next_evaluation, stage1_gate_metrics
from agents.storage import analyze_storage
from agents.drcachesim_analysis import analyze_drcachesim
from calibration import ProxyCalibration

REPO_ROOT = Path(__file__).resolve().parents[2]
COMP_DIR = REPO_ROOT / "openevolve-components"
WORKFLOW_DIR = Path(__file__).resolve().parent

from split_source import (
    PREFETCHER_BEGIN,
    PREFETCHER_END,
    REPLACEMENT_BEGIN,
    REPLACEMENT_END,
    split_combined_source,
)


def _import_base_evaluator():
    """Import ``openevolve-components/evaluator.py`` without mutating sys.modules state."""

    base_path = COMP_DIR / "evaluator.py"
    if not base_path.exists():
        raise FileNotFoundError(f"Base evaluator not found: {base_path}")

    module_name = "openevolve_components_evaluator"
    if module_name in sys.modules:
        return sys.modules[module_name]

    if str(COMP_DIR) not in sys.path:
        sys.path.insert(0, str(COMP_DIR))

    spec = importlib.util.spec_from_file_location(module_name, base_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not build module spec for {base_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_base = _import_base_evaluator()
EvaluationResult = _base.EvaluationResult  # type: ignore[attr-defined]

REPLACEMENT_CC = COMP_DIR / "initial_replacement.cc"
REPLACEMENT_OBJ_DIR = (
    _base.CHAMPSIM_ROOT / ".csconfig" / "modules" / "replacement" / "openevolve_replacement"
)
COMBINED_CONFIG_PATH = (WORKFLOW_DIR / "champsim_config.json").resolve()


# --- Patch base evaluator hooks --------------------------------------------------
# We swap the module-level ``_copy_candidate`` / ``_invalidate_prefetcher_object``
# functions so the base ``evaluate()`` flow performs the combined-mode steps
# (split + dual write, dual invalidation) without otherwise diverging from the
# solo prefetcher logic. Python resolves these names through the base module's
# globals each call, so swapping them at import time is safe.

_base.CONFIG_PATH = COMBINED_CONFIG_PATH

_orig_invalidate = _base._invalidate_prefetcher_object  # type: ignore[attr-defined]


def _patched_copy_candidate(program_path: Path) -> None:
    src_text = Path(program_path).read_text(encoding="utf-8", errors="replace")
    try:
        pf_source, rp_source = split_combined_source(src_text)
    except ValueError as exc:
        raise ValueError(
            f"Combined candidate at {program_path} could not be split: {exc}"
        ) from exc

    _base.PREFETCHER_CC.write_text(pf_source, encoding="utf-8")
    REPLACEMENT_CC.write_text(rp_source, encoding="utf-8")


def _patched_invalidate() -> None:
    _orig_invalidate()
    try:
        shutil.rmtree(REPLACEMENT_OBJ_DIR)
    except FileNotFoundError:
        pass
    except OSError:
        for artifact in ("openevolve_replacement.o", "openevolve_replacement.d"):
            artifact_path = REPLACEMENT_OBJ_DIR / artifact
            try:
                artifact_path.unlink()
            except FileNotFoundError:
                continue


_base._copy_candidate = _patched_copy_candidate  # type: ignore[attr-defined]
_base._invalidate_prefetcher_object = _patched_invalidate  # type: ignore[attr-defined]


def evaluate(program_path: str):
    """Entry point used by OpenEvolve for the combined workflow."""

    return _base.evaluate(program_path)


def evaluate_stage1(program_path: str):
    """Run the cheap cache proxy and decide whether periodic ChampSim is due."""
    source = Path(program_path).read_text(encoding="utf-8", errors="replace")
    storage = analyze_storage(source)
    if not storage.approved:
        return EvaluationResult(
            metrics={
                **storage.metrics(),
                "ipc_proxy": -1.0,
                "stage1_available": 0.0,
                "stage1_passed": 0.0,
                "stage2_due": 0.0,
                "combined_score": -1.0,
                "promotion_eligible": 0.0,
            },
            artifacts={"storage_report": storage.text()},
        )

    hierarchical_enabled = os.environ.get(
        "OPENEVOLVE_HIERARCHICAL_EVAL", "true"
    ).lower() in ("1", "true", "yes", "on")
    if not hierarchical_enabled:
        return EvaluationResult(
            metrics={
                **storage.metrics(),
                "ipc_proxy": 0.0,
                "stage1_available": 0.0,
                "stage1_passed": 1.0,
                "stage2_due": 1.0,
                "combined_score": 1.0,
                "promotion_eligible": 0.0,
                "hierarchical_eval_enabled": 0.0,
            },
            artifacts={"storage_report": storage.text()},
        )

    every_n = int(os.environ.get("HIER_STAGE2_EVERY_N", "10"))
    cadence = next_evaluation(every_n=every_n)
    artifacts: dict[str, object] = {}
    try:
        plugin = build_candidate_plugin(Path(program_path))
        metrics, run_artifacts = evaluate_stage1_policy(
            replacement="CUSTOM",
            prefetcher="custom",
            prefetcher_plugin=plugin,
            replacement_plugin=plugin,
        )
        run_artifacts["drcachesim_plugin"] = str(plugin)
        artifacts.update(run_artifacts)
    except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired) as exc:
        # Missing generated traces must not disable authoritative periodic IPC
        # evaluation. Non-ChampSim iterations remain explicitly unmeasured.
        metrics = {
            "ipc_proxy": -1.0,
            "stage1_available": 0.0,
        }
        artifacts["drcachesim_error"] = str(exc)

    ipc_proxy = float(metrics.get("ipc_proxy", -1.0))
    calibration = ProxyCalibration.load()
    predicted_delta = calibration.predict(metrics)
    ranking_score = predicted_delta if predicted_delta is not None else ipc_proxy
    metrics.update(
        {
            "stage1_rank_score": ranking_score,
            "predicted_ipc_delta": predicted_delta or 0.0,
            "calibration_trusted": 1.0 if calibration.trusted else 0.0,
            "calibration_spearman": calibration.spearman,
            "calibration_mae": calibration.mean_absolute_error or 0.0,
        }
    )
    threshold = float(os.environ.get("HIER_STAGE1_THRESHOLD", "-1.0"))
    available = bool(metrics.get("stage1_available", 0.0))
    metrics.update(
        stage1_gate_metrics(
            cadence,
            ipc_proxy=ranking_score,
            available=available,
            threshold=threshold,
        )
    )
    metrics.update(storage.metrics())
    artifacts["storage_report"] = storage.text()
    artifacts["drcachesim_metrics"] = dict(metrics)
    artifacts["drcachesim_analysis"] = analyze_drcachesim(
        metrics, str(artifacts.get("drcachesim_output", ""))
    )
    return EvaluationResult(metrics=metrics, artifacts=artifacts)


def evaluate_stage2(program_path: str):
    """Run authoritative ChampSim IPC evaluation."""
    result = _base.evaluate(program_path)
    processed = (
        result
        if isinstance(result, EvaluationResult)
        else EvaluationResult(metrics=dict(result))
    )
    processed.metrics["stage2_ran"] = 1.0
    processed.metrics["promotion_eligible"] = 1.0
    if "combined_score" in processed.metrics:
        processed.metrics["measured_ipc"] = float(
            processed.metrics["combined_score"]
        )
    return processed


__all__ = [
    "evaluate",
    "evaluate_stage1",
    "evaluate_stage2",
    "split_combined_source",
]
