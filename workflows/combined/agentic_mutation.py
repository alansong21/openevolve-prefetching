"""Agentic mutation operator for the combined workflow (Phase 2/3)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

_COMBINED_DIR = Path(__file__).resolve().parent
if str(_COMBINED_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBINED_DIR))

from agents.critic import review_combined_source  # noqa: E402
from agents.directive import format_directive, synthesize_directive  # noqa: E402
from agents.engineer import run_engineer  # noqa: E402
from insight_service import build_insight_bundle  # noqa: E402
from merge import extract_layout, merge_sections  # noqa: E402
from orchestrator import orchestrator_enabled, run_orchestrator_async  # noqa: E402

logger = logging.getLogger(__name__)

MAX_CRITIC_RETRIES = int(os.environ.get("OPENEVOLVE_CRITIC_RETRIES", "2"))


def _apply_section_diff(section_source: str, llm_response: str, diff_pattern: str) -> str:
    from openevolve.utils.code_utils import apply_diff, extract_diffs

    diff_blocks = extract_diffs(llm_response, diff_pattern)
    if not diff_blocks:
        raise ValueError("Engineer response contained no valid SEARCH/REPLACE blocks")
    return apply_diff(section_source, llm_response, diff_pattern)


async def _run_phase2_mutation_async(
    *,
    parent_code: str,
    parent_artifacts: dict[str, Any] | None,
    parent_metrics: dict[str, Any] | None,
    llm_ensemble: Any,
    iteration: int,
    diff_pattern: str,
) -> tuple[str, str, str] | None:
    """Phase 2 fallback: heuristic directive without bandit/blackboard."""

    insights = build_insight_bundle(
        "Co-evolve the L2C prefetcher and replacement policy to maximise IPC",
        parent_artifacts,
        token_budget=int(os.environ.get("OPENEVOLVE_INSIGHT_BUDGET", "8000")),
    )
    directive = synthesize_directive(insights, parent_metrics, iteration)
    directive_text = format_directive(directive)

    layout = extract_layout(parent_code)
    pf_section = layout.prefetcher_section
    rp_section = layout.replacement_section

    responses: list[str] = []
    critic_feedback = ""

    for attempt in range(MAX_CRITIC_RETRIES + 1):
        if directive["edit_prefetcher"]:
            pf_response = await run_engineer(
                llm_ensemble,
                role="prefetcher",
                section_source=pf_section,
                directive_text=directive_text,
                insights=insights,
                critic_feedback=critic_feedback,
            )
            responses.append(f"[Prefetcher engineer attempt {attempt + 1}]\n{pf_response}")
            pf_section = _apply_section_diff(pf_section, pf_response, diff_pattern)
        else:
            responses.append(f"[Prefetcher engineer] skipped ({directive['mode']})")

        if directive["edit_replacement"]:
            rp_response = await run_engineer(
                llm_ensemble,
                role="replacement",
                section_source=rp_section,
                directive_text=directive_text,
                insights=insights,
                critic_feedback=critic_feedback,
            )
            responses.append(f"[Replacement engineer attempt {attempt + 1}]\n{rp_response}")
            rp_section = _apply_section_diff(rp_section, rp_response, diff_pattern)
        else:
            responses.append(f"[Replacement engineer] skipped ({directive['mode']})")

        child_code = merge_sections(layout, pf_section, rp_section)
        contract_id = directive.get("metadata_contract_id")
        report = review_combined_source(
            child_code,
            metadata_contract_id=contract_id,
            joint_edit=directive.get("edit_prefetcher", False) and directive.get("edit_replacement", False),
        )
        if report.approved:
            summary = (
                f"Agentic mutation ({directive['mode']}, attempt {attempt + 1}): "
                f"PF={'yes' if directive['edit_prefetcher'] else 'skip'}, "
                f"RP={'yes' if directive['edit_replacement'] else 'skip'}"
            )
            llm_response = "\n\n".join(responses)
            logger.info("Agentic mutation approved on attempt %d", attempt + 1)
            return child_code, llm_response, summary

        critic_feedback = report.text()
        logger.warning("Critic rejected attempt %d: %s", attempt + 1, critic_feedback)

    logger.error("Agentic mutation failed critic after %d attempts", MAX_CRITIC_RETRIES + 1)
    return None


async def run_agentic_mutation_async(
    *,
    parent_code: str,
    parent_artifacts: dict[str, Any] | None,
    parent_metrics: dict[str, Any] | None,
    parent_id: str | None = None,
    llm_ensemble: Any,
    iteration: int,
    diff_pattern: str,
) -> tuple[str, str, str] | None:
    """Run Phase 3 orchestrator or Phase 2 fallback mutation."""

    if orchestrator_enabled():
        result = await run_orchestrator_async(
            parent_code=parent_code,
            parent_artifacts=parent_artifacts,
            parent_metrics=parent_metrics,
            parent_id=parent_id,
            llm_ensemble=llm_ensemble,
            iteration=iteration,
            diff_pattern=diff_pattern,
        )
        if result is not None:
            return result.child_code, result.llm_response, result.summary
        return None

    return await _run_phase2_mutation_async(
        parent_code=parent_code,
        parent_artifacts=parent_artifacts,
        parent_metrics=parent_metrics,
        llm_ensemble=llm_ensemble,
        iteration=iteration,
        diff_pattern=diff_pattern,
    )


def record_evaluation_reward(
    *,
    child_id: str,
    parent_id: str | None,
    child_metrics: dict[str, Any] | None,
    parent_metrics: dict[str, Any] | None,
) -> float | None:
    from orchestrator import record_orchestrator_reward

    return record_orchestrator_reward(
        child_id=child_id,
        parent_id=parent_id,
        child_metrics=child_metrics,
        parent_metrics=parent_metrics,
    )


def run_agentic_mutation_sync(
    *,
    parent_code: str,
    parent_artifacts: dict[str, Any] | None,
    parent_metrics: dict[str, Any] | None,
    parent_id: str | None = None,
    llm_ensemble: Any,
    iteration: int,
    diff_pattern: str,
) -> tuple[str, str, str] | None:
    return asyncio.run(
        run_agentic_mutation_async(
            parent_code=parent_code,
            parent_artifacts=parent_artifacts,
            parent_metrics=parent_metrics,
            parent_id=parent_id,
            llm_ensemble=llm_ensemble,
            iteration=iteration,
            diff_pattern=diff_pattern,
        )
    )
