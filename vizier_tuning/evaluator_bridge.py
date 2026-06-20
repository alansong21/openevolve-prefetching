"""Import a workflow's OpenEvolve evaluator and expose its scalar objective.

This guarantees the tuning stage scores candidates with the *exact* evaluator
the evolution stage used for the chosen workflow.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

from vizier_tuning.config import VizierTuningConfig, WorkflowSpec, ensure_openevolve_importable

logger = logging.getLogger(__name__)


def load_evaluator(workflow: WorkflowSpec):
    """Load and return the evaluator module for the given workflow."""

    ensure_openevolve_importable()

    evaluator_path = workflow.evaluator_path
    if not evaluator_path.exists():
        raise FileNotFoundError(f"Evaluator not found: {evaluator_path}")

    module_name = f"vizier_workflow_evaluator_{workflow.name.replace('-', '_')}"
    if module_name in sys.modules:
        return sys.modules[module_name]

    # Evaluators resolve sibling modules relative to their own directory.
    eval_dir = str(evaluator_path.parent)
    if eval_dir not in sys.path:
        sys.path.insert(0, eval_dir)

    spec = importlib.util.spec_from_file_location(module_name, evaluator_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not build module spec for {evaluator_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    if not hasattr(module, "evaluate"):
        raise AttributeError(f"Evaluator {evaluator_path} has no evaluate() function.")
    logger.info("Loaded evaluator for workflow '%s' from %s", workflow.name, evaluator_path)
    return module


def _metrics_from_result(result: Any) -> Dict[str, Any]:
    if result is None:
        return {}
    metrics = getattr(result, "metrics", None)
    if isinstance(metrics, dict):
        return metrics
    if isinstance(result, dict):
        return result
    return {}


def score_and_metrics(
    evaluator_module, program_path: Path, cfg: VizierTuningConfig
) -> Tuple[float, Dict[str, Any]]:
    """Evaluate a candidate program and return (objective_score, metrics).

    Build/run failures are caught and reported as ``cfg.failure_score`` so the
    optimizer learns to avoid that region instead of crashing the run.
    """

    try:
        result = evaluator_module.evaluate(str(program_path))
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Evaluator raised for %s: %s", program_path.name, exc)
        return cfg.failure_score, {"error": str(exc)}

    metrics = _metrics_from_result(result)
    score = metrics.get(cfg.metric_name)
    if score is None:
        score = metrics.get(cfg.metric_fallback)
    if score is None:
        logger.warning(
            "No '%s'/'%s' metric in evaluator result for %s; scoring as failure.",
            cfg.metric_name,
            cfg.metric_fallback,
            program_path.name,
        )
        return cfg.failure_score, metrics

    try:
        return float(score), metrics
    except (TypeError, ValueError):
        return cfg.failure_score, metrics
