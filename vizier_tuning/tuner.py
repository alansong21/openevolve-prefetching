"""Stage B: drive a Vizier study that tunes an evolved program's hyperparameters.

Pipeline:
  1. Identify hyperparameters (Stage A) -> templated source + param spec.
  2. Baseline sanity build: render the template with each param's *current*
     value and evaluate it. If that fails to build/score, abort (we refuse to
     search over a broken template).
  3. Build a Vizier StudyConfig and run the ask/tell loop, scoring each
     suggestion with the SAME workflow evaluator.
  4. Write the best tuned program + params + a full trial log.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from vizier_tuning.config import (
    WORKFLOWS,
    VizierTuningConfig,
    WorkflowSpec,
)
from vizier_tuning.evaluator_bridge import load_evaluator, score_and_metrics
from vizier_tuning.hyperparam_identifier import identify_hyperparameters
from vizier_tuning.search_space import build_study_config
from vizier_tuning.template import HyperParam, clamp_current, render_template

logger = logging.getLogger(__name__)


@dataclass
class TuningOutcome:
    workflow: str
    run_id: str
    output_dir: Path
    baseline_score: Optional[float]
    best_score: Optional[float]
    best_params: Dict[str, Any]
    best_program_path: Optional[Path]
    num_trials_run: int
    parameters: List[Dict[str, Any]] = field(default_factory=list)


def _param_value(raw: Any) -> Any:
    """Normalize a Vizier parameter value to a plain Python scalar."""

    value = getattr(raw, "value", raw)
    return value


def _values_from_suggestion(suggestion, params: List[HyperParam]) -> Dict[str, Any]:
    return {p.name: _param_value(suggestion.parameters[p.name]) for p in params}


def _write_candidate(source: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def run_tuning(
    workflow_name: str,
    program_path: Optional[Path],
    cfg: VizierTuningConfig,
    workflow_config_path: Path,
    output_root: Optional[Path] = None,
    run_id: Optional[str] = None,
) -> TuningOutcome:
    if workflow_name not in WORKFLOWS:
        raise ValueError(
            f"Unknown workflow '{workflow_name}'. Expected one of {sorted(WORKFLOWS)}."
        )
    workflow: WorkflowSpec = WORKFLOWS[workflow_name]

    program_path = Path(program_path) if program_path else workflow.default_best_program
    if not program_path.exists():
        raise FileNotFoundError(
            f"Evolved program not found: {program_path}. Run evolution first or pass "
            "--program explicitly."
        )

    run_id = run_id or (time.strftime("%Y%m%d_%H%M%S"))
    output_dir = Path(output_root) if output_root else (workflow.output_dir / "vizier" / run_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    trials_dir = output_dir / "trials"
    trials_dir.mkdir(parents=True, exist_ok=True)
    trials_log = output_dir / "trials.jsonl"

    # Ensure evaluator logs land under a dedicated run id (read at import time).
    os.environ.setdefault("OPENEVOLVE_RUN_ID", f"vizier_{run_id}")

    source = program_path.read_text(encoding="utf-8", errors="replace")
    logger.info("Tuning workflow '%s' from %s", workflow_name, program_path)

    # --- Stage A: identify hyperparameters --------------------------------
    identification = identify_hyperparameters(source, workflow_config_path, cfg)
    params = identification.parameters
    templated_source = identification.templated_source

    (output_dir / "templated_source.txt").write_text(templated_source, encoding="utf-8")
    (output_dir / "param_spec.json").write_text(
        json.dumps([p.to_dict() for p in params], indent=2), encoding="utf-8"
    )
    (output_dir / "identifier_response.txt").write_text(
        identification.raw_response, encoding="utf-8"
    )

    evaluator_module = load_evaluator(workflow)
    candidate_path = trials_dir / f"candidate{workflow.suffix}"

    def evaluate_values(values: Dict[str, Any]) -> tuple[float, Dict[str, Any]]:
        rendered = render_template(templated_source, params, values)
        _write_candidate(rendered, candidate_path)
        return score_and_metrics(evaluator_module, candidate_path, cfg)

    def log_trial(kind: str, index: int, values: Dict[str, Any], score: float, metrics: Dict[str, Any]) -> None:
        with trials_log.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "kind": kind,
                        "index": index,
                        "params": values,
                        "score": score,
                        "metrics": {
                            k: v for k, v in metrics.items() if not isinstance(v, (dict, list))
                        },
                        "timestamp": time.time(),
                    }
                )
                + "\n"
            )

    # --- Baseline sanity build (current values) ---------------------------
    baseline_values = {p.name: clamp_current(p) for p in params}
    baseline_score: Optional[float] = None
    if cfg.baseline_sanity_build:
        logger.info("Baseline sanity build with current hyperparameter values...")
        baseline_score, baseline_metrics = evaluate_values(baseline_values)
        log_trial("baseline", 0, baseline_values, baseline_score, baseline_metrics)
        if baseline_score <= cfg.failure_score:
            raise RuntimeError(
                "Baseline sanity build failed (score "
                f"{baseline_score} <= failure_score {cfg.failure_score}). "
                "The identified template does not build/run with its own current "
                f"values; aborting. See {baseline_metrics.get('error', 'logs')}."
            )
        logger.info("Baseline score (%s) = %s", cfg.metric_name, baseline_score)

    # --- Stage B: Vizier study --------------------------------------------
    try:
        from vizier.service import clients
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Google Vizier is not installed in this Python environment. "
            "From the repo root run: pip install -r requirements.txt "
            "(needs google-vizier and the pinned JAX stack). "
            f"Python: {sys.executable}"
        ) from exc

    study_config = build_study_config(params, cfg)
    study = clients.Study.from_study_config(
        study_config, owner=cfg.owner, study_id=f"{workflow_name}_{run_id}"
    )

    best_score = baseline_score if baseline_score is not None else None
    best_values: Dict[str, Any] = dict(baseline_values) if baseline_score is not None else {}

    trials_run = 0
    batch = max(1, cfg.batch_size)
    while trials_run < cfg.num_trials:
        count = min(batch, cfg.num_trials - trials_run)
        suggestions = study.suggest(count=count)
        for suggestion in suggestions:
            trials_run += 1
            values = _values_from_suggestion(suggestion, params)
            score, metrics = evaluate_values(values)
            log_trial("vizier", trials_run, values, score, metrics)

            from vizier.service import pyvizier as vz

            suggestion.complete(vz.Measurement({cfg.metric_name: score}))

            if best_score is None or score > best_score:
                best_score = score
                best_values = dict(values)
            logger.info(
                "Trial %d/%d: score=%.6f (best=%.6f)",
                trials_run,
                cfg.num_trials,
                score,
                best_score if best_score is not None else float("nan"),
            )

    # --- Persist the winner ----------------------------------------------
    best_program_path: Optional[Path] = None
    if best_values:
        best_source = render_template(templated_source, params, best_values)
        best_program_path = output_dir / f"best_tuned_program{workflow.suffix}"
        best_program_path.write_text(best_source, encoding="utf-8")

    delta = None
    if best_score is not None and baseline_score is not None:
        delta = best_score - baseline_score

    (output_dir / "best_params.json").write_text(
        json.dumps(
            {
                "workflow": workflow_name,
                "run_id": run_id,
                "source_program": str(program_path),
                "metric": cfg.metric_name,
                "baseline_score": baseline_score,
                "best_score": best_score,
                "improvement": delta,
                "best_params": best_values,
                "num_trials": trials_run,
                "best_program": str(best_program_path) if best_program_path else None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info(
        "Tuning complete: baseline=%s best=%s (improvement=%s). Winner: %s",
        baseline_score,
        best_score,
        delta,
        best_program_path,
    )

    return TuningOutcome(
        workflow=workflow_name,
        run_id=run_id,
        output_dir=output_dir,
        baseline_score=baseline_score,
        best_score=best_score,
        best_params=best_values,
        best_program_path=best_program_path,
        num_trials_run=trials_run,
        parameters=[p.to_dict() for p in params],
    )
