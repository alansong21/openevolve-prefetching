"""Design directive synthesis for agentic mutation."""

from __future__ import annotations

import os
from typing import Any, Literal

from metadata_contract import get_contract_text
from strategy.bandit import KnobArm
from strategy.plays import Play

MutationMode = Literal["joint", "prefetcher_only", "replacement_only"]

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
) -> MutationMode:
    forced = os.environ.get("OPENEVOLVE_MUTATION_MODE", "").lower()
    if forced in {"joint", "prefetcher_only", "replacement_only"}:
        return forced  # type: ignore[return-value]

    if metrics:
        pf_useless = float(metrics.get("l2c_pf_useless", 0))
        pf_useful = float(metrics.get("l2c_pf_useful", 0))
        if pf_useless > pf_useful and pf_useless >= 20:
            return "replacement_only"
        if pf_useful > 0 and pf_useless == 0 and iteration % 3 == 0:
            return "prefetcher_only"

    if "coverage_gap" in insights and "conflict" not in insights[:2000]:
        return "prefetcher_only"
    if "conflict" in insights or "capacity" in insights:
        if iteration % 2 == 1:
            return "replacement_only"

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
    mode: MutationMode = play.mode
    if forced in {"joint", "prefetcher_only", "replacement_only"}:
        mode = forced  # type: ignore[assignment]

    edit_pf = mode in {"joint", "prefetcher_only"}
    edit_rp = mode in {"joint", "replacement_only"}

    contract_id = play.contract_id if mode == "joint" else None
    metadata_contract = get_contract_text(contract_id) or (
        "No metadata contract this round (single-component edit)."
    )

    return {
        "mode": mode,
        "summary": f"Play: {play.name} — {play.description}",
        "edit_prefetcher": edit_pf,
        "edit_replacement": edit_rp,
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

    mode = choose_mutation_mode(metrics, insights, iteration)
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

    if mode == "prefetcher_only":
        summary = f"PF-only round: {pf_focus}"
        edit_pf, edit_rp = True, False
    elif mode == "replacement_only":
        summary = f"RP-only round: {rp_focus}"
        edit_pf, edit_rp = False, True
    else:
        summary = (
            "Joint round: coordinate metadata contract — PF encodes confidence in "
            "returned metadata; RP reads it in replacement_cache_fill for insertion RRPV."
        )
        edit_pf, edit_rp = True, True

    metadata_contract = (
        "Metadata contract: low 8 bits = prefetch type enum; bits 8-15 = confidence "
        "(0=low/near-evict, 255=high/protect). RP must decode the same layout."
    )

    return {
        "mode": mode,
        "summary": summary,
        "edit_prefetcher": edit_pf,
        "edit_replacement": edit_rp,
        "prefetcher_focus": pf_focus,
        "replacement_focus": rp_focus,
        "metadata_contract": metadata_contract,
        "metadata_contract_id": "confidence_rrpv" if mode == "joint" else None,
        "parent_ipc": ipc,
        "parent_l2c_mpki": l2c_mpki,
        "insights_excerpt": insights[:4000],
    }


def format_directive(directive: dict[str, Any]) -> str:
    lines = [
        "=== Design directive ===",
        f"Mode: {directive['mode']}",
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
