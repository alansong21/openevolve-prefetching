"""Design directive synthesis for agentic mutation."""

from __future__ import annotations

import os
from typing import Any, Literal

from metadata_contract import get_contract_text
from strategy.bandit import KnobArm
from strategy.plays import Play

MutationFocus = Literal["joint", "prefetcher", "replacement"]

PF_REQUIRED = (
    "prefetcher_initialize",
    "prefetcher_cache_operate",
    "prefetcher_cache_fill",
    "prefetcher_cycle_operate",
)
RP_REQUIRED = (
    "find_victim",
    "replacement_cache_fill",
    "update_replacement_state",
)


def choose_mutation_mode(
    metrics: dict[str, Any] | None,
    insights: str,
    iteration: int,
) -> MutationFocus:
    forced = os.environ.get("OPENEVOLVE_MUTATION_MODE", "").lower()
    if forced in {"joint", "prefetcher", "replacement"}:
        return forced  # type: ignore[return-value]
    if forced == "prefetcher_only":
        return "prefetcher"
    if forced == "replacement_only":
        return "replacement"

    if metrics:
        pf_useless = float(metrics.get("l2c_pf_useless", 0))
        pf_useful = float(metrics.get("l2c_pf_useful", 0))
        if pf_useless > pf_useful and pf_useless >= 20:
            return "replacement"
        if pf_useful > 0 and pf_useless == 0 and iteration % 3 == 0:
            return "prefetcher"

    if "coverage_gap" in insights and "conflict" not in insights[:2000]:
        return "prefetcher"
    if "conflict" in insights or "capacity" in insights:
        if iteration % 2 == 1:
            return "replacement"

    return "joint"


def synthesize_orchestrated_directive(
    *,
    insights: str,
    metrics: dict[str, Any] | None,
    iteration: int,
    arm: KnobArm,
    play: Play,
) -> dict[str, Any]:
    """Build a directive from bandit arm + named play (Phase 3)."""

    forced = os.environ.get("OPENEVOLVE_MUTATION_MODE", "").lower()
    focus = {
        "prefetcher_only": "prefetcher",
        "replacement_only": "replacement",
    }.get(play.mode, "joint")
    if forced in {"joint", "prefetcher", "replacement"}:
        focus = forced
    elif forced in {"prefetcher_only", "replacement_only"}:
        focus = forced.removesuffix("_only")

    contract_id = play.contract_id
    metadata_contract = get_contract_text(contract_id) or (
        "No metadata contract this round (single-component edit)."
    )

    return {
        "mode": "joint",
        "focus_component": focus,
        "summary": f"Play: {play.name} — {play.description}",
        "edit_prefetcher": True,
        "edit_replacement": True,
        "prefetcher_focus": play.pf_guidance,
        "replacement_focus": play.rp_guidance,
        "metadata_contract": metadata_contract,
        "metadata_contract_id": contract_id,
        "play_id": play.id,
        "play_name": play.name,
        "knob_arm": arm,
        "parent_ipc": metrics.get("ipc") if metrics else None,
        "parent_l2c_mpki": metrics.get("l2c_mpki") if metrics else None,
        "insights_excerpt": insights[:4000],
        "iteration": iteration,
    }


def synthesize_directive(
    insights: str,
    metrics: dict[str, Any] | None,
    iteration: int,
) -> dict[str, Any]:
    """Legacy Phase 2 heuristic directive (used when orchestrator is disabled)."""

    focus = choose_mutation_mode(metrics, insights, iteration)
    l2c_mpki = metrics.get("l2c_mpki") if metrics else None
    ipc = metrics.get("ipc") if metrics else None

    pf_focus = (
        "Improve prefetch coverage and timeliness for hot PCs flagged in miss-log "
        "hypotheses. Encode prefetch confidence in returned metadata."
    )
    rp_focus = (
        "Improve victim selection and prefetch-aware insertion using metadata from "
        "fills. Demote dead or low-confidence prefetches."
    )

    if focus == "prefetcher":
        summary = f"Unified round with prefetcher emphasis: {pf_focus}"
    elif focus == "replacement":
        summary = f"Unified round with replacement emphasis: {rp_focus}"
    else:
        summary = (
            "Joint round: coordinate metadata contract — PF encodes confidence in "
            "returned metadata; RP reads it in replacement_cache_fill for insertion RRPV."
        )

    metadata_contract = (
        "Metadata contract: low 8 bits = prefetch type enum; bits 8-15 = confidence "
        "(0=low/near-evict, 255=high/protect). RP must decode the same layout."
    )

    return {
        "mode": "joint",
        "focus_component": focus,
        "summary": summary,
        "edit_prefetcher": True,
        "edit_replacement": True,
        "prefetcher_focus": pf_focus,
        "replacement_focus": rp_focus,
        "metadata_contract": metadata_contract,
        "metadata_contract_id": "confidence_rrpv",
        "parent_ipc": ipc,
        "parent_l2c_mpki": l2c_mpki,
        "insights_excerpt": insights[:4000],
    }


def format_directive(directive: dict[str, Any]) -> str:
    lines = [
        "=== Design directive ===",
        f"Mode: {directive['mode']}",
        f"Focus component: {directive.get('focus_component', 'joint')}",
        directive["summary"],
        f"Prefetcher focus: {directive['prefetcher_focus']}",
        f"Replacement focus: {directive['replacement_focus']}",
        directive["metadata_contract"],
    ]
    if directive.get("play_id"):
        lines.append(f"Play: {directive['play_id']} (arm={directive.get('knob_arm')})")
    if directive.get("parent_ipc") is not None:
        lines.append(f"Parent IPC: {directive['parent_ipc']}")
    if directive.get("parent_l2c_mpki") is not None:
        lines.append(f"Parent L2C MPKI: {directive['parent_l2c_mpki']}")
    return "\n".join(lines)
