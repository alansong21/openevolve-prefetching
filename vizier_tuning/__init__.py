"""Vizier hyperparameter tuning stage for OpenEvolve workflows.

This package adds a second optimization stage that runs *after* OpenEvolve
evolution: it takes an evolved program, identifies the hyperparameters that
live inside it (via a single LLM call), and uses Google Vizier to search for
the hyperparameter values that maximize the score reported by the *same*
ChampSim evaluator the evolution stage used.

See ``docs/vizier_integration_design.md`` for the full design.
"""

from __future__ import annotations

__all__ = [
    "WORKFLOWS",
    "WorkflowSpec",
    "VizierTuningConfig",
    "load_tuning_config",
]

from vizier_tuning.config import (
    WORKFLOWS,
    WorkflowSpec,
    VizierTuningConfig,
    load_tuning_config,
)
