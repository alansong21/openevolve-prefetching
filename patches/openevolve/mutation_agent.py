"""
Bridge from OpenEvolve to combined-workflow agentic mutation (Phase 2).
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def agentic_mutation_enabled() -> bool:
    if os.environ.get("OPENEVOLVE_WORKFLOW", "").lower() != "combined":
        return False
    return os.environ.get("OPENEVOLVE_AGENTIC_MUTATION", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _load_agentic_mutation():
    path = _REPO_ROOT / "workflows" / "combined" / "agentic_mutation.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("combined_agentic_mutation", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def try_agentic_mutation_async(
    *,
    parent_code: str,
    parent_artifacts: dict[str, Any] | None,
    parent_metrics: dict[str, Any] | None,
    parent_id: str | None = None,
    llm_ensemble: Any,
    iteration: int,
    diff_pattern: str,
) -> tuple[str, str, str] | None:
    if not agentic_mutation_enabled():
        return None
    module = _load_agentic_mutation()
    if module is None:
        logger.debug("Agentic mutation module not found")
        return None
    try:
        return await module.run_agentic_mutation_async(
            parent_code=parent_code,
            parent_artifacts=parent_artifacts,
            parent_metrics=parent_metrics,
            parent_id=parent_id,
            llm_ensemble=llm_ensemble,
            iteration=iteration,
            diff_pattern=diff_pattern,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agentic mutation failed, falling back to single LLM: %s", exc)
        return None


def try_agentic_mutation_sync(
    *,
    parent_code: str,
    parent_artifacts: dict[str, Any] | None,
    parent_metrics: dict[str, Any] | None,
    parent_id: str | None = None,
    llm_ensemble: Any,
    iteration: int,
    diff_pattern: str,
) -> tuple[str, str, str] | None:
    if not agentic_mutation_enabled():
        return None
    module = _load_agentic_mutation()
    if module is None:
        return None
    try:
        return module.run_agentic_mutation_sync(
            parent_code=parent_code,
            parent_artifacts=parent_artifacts,
            parent_metrics=parent_metrics,
            parent_id=parent_id,
            llm_ensemble=llm_ensemble,
            iteration=iteration,
            diff_pattern=diff_pattern,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agentic mutation failed, falling back to single LLM: %s", exc)
        return None


def record_orchestrator_evaluation_reward(
    *,
    child_id: str,
    parent_id: str | None,
    child_metrics: dict[str, Any] | None,
    parent_metrics: dict[str, Any] | None,
) -> float | None:
    """Update bandit reward on the blackboard after ChampSim evaluation."""

    if not agentic_mutation_enabled():
        return None
    module = _load_agentic_mutation()
    if module is None:
        return None
    try:
        if hasattr(module, "record_evaluation_reward"):
            return module.record_evaluation_reward(
                child_id=child_id,
                parent_id=parent_id,
                child_metrics=child_metrics,
                parent_metrics=parent_metrics,
            )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("Orchestrator reward recording skipped: %s", exc)
        return None
