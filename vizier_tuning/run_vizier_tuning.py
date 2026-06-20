"""CLI entry point for the Vizier hyperparameter tuning stage.

Examples
--------
Tune the champsim workflow's best evolved program with 50 trials::

    python -m vizier_tuning.run_vizier_tuning --workflow champsim --trials 50

Tune a specific program file::

    python -m vizier_tuning.run_vizier_tuning \
        --workflow combined \
        --program workflows/combined/openevolve_output/best/best_program.cc

Dry-run Stage A only (identify hyperparameters, no Vizier search)::

    python -m vizier_tuning.run_vizier_tuning --workflow champsim --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from vizier_tuning.config import (
    WORKFLOWS,
    load_tuning_config,
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vizier_tuning",
        description="Tune hyperparameters of an OpenEvolve-evolved program with Vizier.",
    )
    parser.add_argument(
        "--workflow",
        required=True,
        choices=sorted(WORKFLOWS.keys()),
        help="Which OpenEvolve workflow's evaluator/program to tune.",
    )
    parser.add_argument(
        "--program",
        type=Path,
        default=None,
        help="Path to the evolved program. Defaults to the workflow's "
        "openevolve_output/best/best_program.*",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Workflow OpenEvolve config.yaml (for the identifier LLM). Defaults "
        "to the workflow's config.",
    )
    parser.add_argument(
        "--tuning-config",
        type=Path,
        default=None,
        help="vizier_tuning_config.yaml override (defaults to the bundled one).",
    )
    parser.add_argument("--trials", type=int, default=None, help="Override number of trials.")
    parser.add_argument(
        "--max-parameters", type=int, default=None, help="Override max identified params."
    )
    parser.add_argument(
        "--identifier-model", default=None, help="Override the identifier LLM model name."
    )
    parser.add_argument("--output-dir", type=Path, default=None, help="Override output dir.")
    parser.add_argument("--run-id", default=None, help="Explicit run id (default: timestamp).")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only run Stage A (identify hyperparameters) and print the spec.",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level (default INFO).")
    return parser


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    workflow = WORKFLOWS[args.workflow]
    cfg = load_tuning_config(args.tuning_config)
    if args.trials is not None:
        cfg.num_trials = args.trials
    if args.max_parameters is not None:
        cfg.max_parameters = args.max_parameters
    if args.identifier_model is not None:
        cfg.identifier_model = args.identifier_model

    workflow_config_path = args.config or workflow.config_path
    if not Path(workflow_config_path).exists():
        print(f"Workflow config not found: {workflow_config_path}", file=sys.stderr)
        return 2

    program_path = args.program or workflow.default_best_program

    if args.dry_run:
        from vizier_tuning.hyperparam_identifier import identify_hyperparameters

        source = Path(program_path).read_text(encoding="utf-8", errors="replace")
        result = identify_hyperparameters(source, workflow_config_path, cfg)
        print(
            json.dumps(
                {
                    "workflow": args.workflow,
                    "program": str(program_path),
                    "num_parameters": len(result.parameters),
                    "parameters": [p.to_dict() for p in result.parameters],
                },
                indent=2,
            )
        )
        return 0

    from vizier_tuning.tuner import run_tuning

    outcome = run_tuning(
        workflow_name=args.workflow,
        program_path=program_path,
        cfg=cfg,
        workflow_config_path=Path(workflow_config_path),
        output_root=args.output_dir,
        run_id=args.run_id,
    )

    print(
        json.dumps(
            {
                "workflow": outcome.workflow,
                "run_id": outcome.run_id,
                "output_dir": str(outcome.output_dir),
                "baseline_score": outcome.baseline_score,
                "best_score": outcome.best_score,
                "best_program": str(outcome.best_program_path) if outcome.best_program_path else None,
                "num_trials_run": outcome.num_trials_run,
                "best_params": outcome.best_params,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
