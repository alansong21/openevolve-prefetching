"""Phase 3 orchestrator: bandit + plays + blackboard + engineer/critic dispatch."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

_COMBINED_DIR = Path(__file__).resolve().parent
if str(_COMBINED_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBINED_DIR))

from agents.critic import review_combined_source  # noqa: E402
from agents.directive import format_directive, synthesize_orchestrated_directive  # noqa: E402
from agents.engineer import run_engineer  # noqa: E402
from blackboard import Blackboard  # noqa: E402
from insight_service import build_insight_bundle  # noqa: E402
from merge import extract_layout, merge_sections  # noqa: E402
from strategy.bandit import KnobArm  # noqa: E402
from strategy.plays import Play, select_play_for_arm  # noqa: E402

logger = logging.getLogger(__name__)

MAX_CRITIC_RETRIES = int(os.environ.get("OPENEVOLVE_CRITIC_RETRIES", "2"))


class OrchestratorState(TypedDict, total=False):
    parent_code: str
    parent_artifacts: dict[str, Any] | None
    parent_metrics: dict[str, Any] | None
    parent_id: str | None
    iteration: int
    diff_pattern: str
    llm_ensemble: Any
    blackboard: Blackboard
    insights: str
    arm: KnobArm
    play: Play
    directive: dict[str, Any]
    directive_text: str
    layout: Any
    pf_section: str
    rp_section: str
    child_code: str
    llm_responses: list[str]
    summary: str
    error: str


@dataclass
class OrchestratorResult:
    child_code: str
    llm_response: str
    summary: str
    directive: dict[str, Any]


def orchestrator_enabled() -> bool:
    if os.environ.get("OPENEVOLVE_WORKFLOW", "").lower() != "combined":
        return False
    return os.environ.get("OPENEVOLVE_ORCHESTRATOR", "true").lower() in ("1", "true", "yes", "on")


def _apply_section_diff(section_source: str, llm_response: str, diff_pattern: str) -> str:
    from openevolve.utils.code_utils import apply_diff, extract_diffs

    diff_blocks = extract_diffs(llm_response, diff_pattern)
    if not diff_blocks:
        raise ValueError("Engineer response contained no valid SEARCH/REPLACE blocks")
    return apply_diff(section_source, llm_response, diff_pattern)


# --- Graph nodes (LangGraph-compatible) ----------------------------------------


def node_load_blackboard(state: OrchestratorState) -> OrchestratorState:
    state["blackboard"] = Blackboard.load()
    return state


def node_run_analysts(state: OrchestratorState) -> OrchestratorState:
    board: Blackboard = state["blackboard"]
    insights = build_insight_bundle(
        "Co-evolve the L2C prefetcher and replacement policy to maximise IPC",
        state.get("parent_artifacts"),
        token_budget=int(os.environ.get("OPENEVOLVE_INSIGHT_BUDGET", "8000")),
    )
    board_context = board.orchestrator_context()
    state["insights"] = f"{insights}\n\n{board_context}"
    return state


def node_select_strategy(state: OrchestratorState) -> OrchestratorState:
    board: Blackboard = state["blackboard"]
    insights = state.get("insights", "")
    metrics = state.get("parent_metrics")
    iteration = int(state.get("iteration", 0))

    arm = board.bandit.select_arm(insights=insights, metrics=metrics)
    play = select_play_for_arm(arm, insights, iteration)

    # Skip recently failed plays unless forced.
    attempts = 0
    while board.is_tried_recently(play.id, arm, play.mode) and attempts < len(arm):
        arm = board.bandit.select_arm(insights=insights, metrics=metrics)
        play = select_play_for_arm(arm, insights, iteration + attempts)
        attempts += 1

    state["arm"] = arm
    state["play"] = play
    return state


def node_synthesize_directive(state: OrchestratorState) -> OrchestratorState:
    directive = synthesize_orchestrated_directive(
        insights=state.get("insights", ""),
        metrics=state.get("parent_metrics"),
        iteration=int(state.get("iteration", 0)),
        arm=state["arm"],
        play=state["play"],
    )
    state["directive"] = directive
    state["directive_text"] = format_directive(directive)
    board: Blackboard = state["blackboard"]
    board.last_directive = directive
    board.save()
    return state


def node_prepare_sections(state: OrchestratorState) -> OrchestratorState:
    layout = extract_layout(state["parent_code"])
    state["layout"] = layout
    state["pf_section"] = layout.prefetcher_section
    state["rp_section"] = layout.replacement_section
    state["llm_responses"] = []
    return state


async def node_engineer_and_critic(state: OrchestratorState) -> OrchestratorState:
    directive = state["directive"]
    directive_text = state["directive_text"]
    insights = state.get("insights", "")
    diff_pattern = state["diff_pattern"]
    llm_ensemble = state["llm_ensemble"]
    board: Blackboard = state["blackboard"]
    play: Play = state["play"]
    arm: KnobArm = state["arm"]
    iteration = int(state.get("iteration", 0))

    pf_section = state["pf_section"]
    rp_section = state["rp_section"]
    layout = state["layout"]
    responses: list[str] = state.get("llm_responses", [])
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
            state["child_code"] = child_code
            state["pf_section"] = pf_section
            state["rp_section"] = rp_section
            state["llm_responses"] = responses
            state["summary"] = (
                f"Orchestrator ({directive['mode']}, play={play.id}, arm={arm}, "
                f"attempt {attempt + 1})"
            )
            board.set_pending_reward(
                arm=arm,
                play_id=play.id,
                mode=directive["mode"],
                parent_ipc=(state.get("parent_metrics") or {}).get("ipc"),
                parent_id=state.get("parent_id"),
                iteration=iteration,
                contract_id=contract_id,
            )
            board.save()
            return state

        critic_feedback = report.text()
        board.record_tried_idea(
            play_id=play.id,
            arm=arm,
            mode=directive["mode"],
            iteration=iteration,
            outcome="failed_critic",
        )
        board.save()
        logger.warning("Critic rejected orchestrator attempt %d: %s", attempt + 1, critic_feedback)

    state["error"] = "critic_rejected"
    state["llm_responses"] = responses
    return state


# --- LangGraph optional wrapper ------------------------------------------------


def _build_langgraph():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:
        return None

    graph = StateGraph(OrchestratorState)
    graph.add_node("load_blackboard", node_load_blackboard)
    graph.add_node("run_analysts", node_run_analysts)
    graph.add_node("select_strategy", node_select_strategy)
    graph.add_node("synthesize_directive", node_synthesize_directive)
    graph.add_node("prepare_sections", node_prepare_sections)

    async def _engineer_node(state: OrchestratorState) -> OrchestratorState:
        return await node_engineer_and_critic(state)

    graph.add_node("engineer_and_critic", _engineer_node)
    graph.set_entry_point("load_blackboard")
    graph.add_edge("load_blackboard", "run_analysts")
    graph.add_edge("run_analysts", "select_strategy")
    graph.add_edge("select_strategy", "synthesize_directive")
    graph.add_edge("synthesize_directive", "prepare_sections")
    graph.add_edge("prepare_sections", "engineer_and_critic")
    graph.add_edge("engineer_and_critic", END)
    return graph.compile()


async def run_orchestrator_async(
    *,
    parent_code: str,
    parent_artifacts: dict[str, Any] | None,
    parent_metrics: dict[str, Any] | None,
    parent_id: str | None,
    llm_ensemble: Any,
    iteration: int,
    diff_pattern: str,
) -> OrchestratorResult | None:
    """Run the Phase 3 orchestrator graph."""

    state: OrchestratorState = {
        "parent_code": parent_code,
        "parent_artifacts": parent_artifacts,
        "parent_metrics": parent_metrics,
        "parent_id": parent_id,
        "iteration": iteration,
        "diff_pattern": diff_pattern,
        "llm_ensemble": llm_ensemble,
    }

    compiled = _build_langgraph()
    if compiled is not None:
        logger.info("Running orchestrator via LangGraph")
        final_state = await compiled.ainvoke(state)
    else:
        logger.info("Running orchestrator via sequential async nodes (LangGraph not installed)")
        state = node_load_blackboard(state)
        state = node_run_analysts(state)
        state = node_select_strategy(state)
        state = node_synthesize_directive(state)
        state = node_prepare_sections(state)
        final_state = await node_engineer_and_critic(state)

    if final_state.get("error") or "child_code" not in final_state:
        logger.error("Orchestrator failed: %s", final_state.get("error", "no child_code"))
        return None

    return OrchestratorResult(
        child_code=final_state["child_code"],
        llm_response="\n\n".join(final_state.get("llm_responses", [])),
        summary=final_state.get("summary", "Orchestrator mutation"),
        directive=final_state["directive"],
    )


def record_orchestrator_reward(
    *,
    child_id: str,
    parent_id: str | None,
    child_metrics: dict[str, Any] | None,
    parent_metrics: dict[str, Any] | None,
) -> float | None:
    """Record bandit reward after ChampSim evaluation (called from OpenEvolve)."""

    if not orchestrator_enabled():
        return None
    if not child_metrics:
        return None
    board = Blackboard.load()
    return board.record_evaluation_result(
        child_id=child_id,
        parent_id=parent_id,
        child_metrics=child_metrics,
        parent_metrics=parent_metrics,
    )
