"""Named co-design strategies (§5 plays) selectable by the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from strategy.bandit import KnobArm

MutationMode = Literal["joint", "prefetcher_only", "replacement_only"]


@dataclass(frozen=True)
class Play:
    id: str
    name: str
    description: str
    primary_arm: KnobArm
    mode: MutationMode
    pf_guidance: str
    rp_guidance: str
    contract_id: str | None = None


PLAY_LIBRARY: tuple[Play, ...] = (
    Play(
        id="prefetch_aware_rrip",
        name="Prefetch-aware RRIP insertion",
        description=(
            "PF tags each prefetch with confidence in metadata; RP inserts "
            "high-confidence prefetches with protected RRPV and low-confidence "
            "ones near eviction."
        ),
        primary_arm="metadata_contract",
        mode="joint",
        contract_id="confidence_rrpv",
        pf_guidance=(
            "Encode prefetch type in metadata low byte and confidence (0-255) "
            "in bits 8-15 for every prefetch_line() call."
        ),
        rp_guidance=(
            "In replacement_cache_fill, decode metadata confidence and map to "
            "insertion RRPV: high confidence -> 0, low confidence -> maxRRPV."
        ),
    ),
    Play(
        id="dead_prefetch_demotion",
        name="Dead-prefetch demotion",
        description=(
            "When pf_useless is high, RP evicts untouched prefetched lines first "
            "and demotes low-confidence prefetches aggressively."
        ),
        primary_arm="rp_victim",
        mode="replacement_only",
        pf_guidance="Keep current prefetch issuance; focus RP on pollution cleanup.",
        rp_guidance=(
            "Track prefetch fills; in find_victim prefer ways with untouched "
            "prefetches or low metadata confidence."
        ),
    ),
    Play(
        id="coverage_accuracy_handshake",
        name="Coverage/accuracy throttle handshake",
        description=(
            "Streaming workloads: raise PF degree and use BRRIP-friendly insertion. "
            "Pointer-chasing: delta/temporal PF with strong demand-line protection."
        ),
        primary_arm="pf_coverage",
        mode="joint",
        contract_id="confidence_rrpv",
        pf_guidance=(
            "Tune prefetch degree/distance from workload taxonomy; throttle when "
            "useless prefetches dominate."
        ),
        rp_guidance=(
            "Match insertion policy to workload: streaming -> BRRIP bias; "
            "pointer-chasing -> protect reused demand lines."
        ),
    ),
    Play(
        id="conflict_capacity_routing",
        name="Conflict vs capacity routing",
        description=(
            "Conflict-dominated misses -> RP edits (set-dueling, victim). "
            "Capacity/compulsory -> PF edits (coverage, timeliness)."
        ),
        primary_arm="rp_victim",
        mode="replacement_only",
        pf_guidance="Defer PF changes when conflict labels dominate miss log.",
        rp_guidance=(
            "Improve victim selection for hot conflicting sets; use "
            "get_set_sample_category for set-dueling."
        ),
    ),
    Play(
        id="bypass_low_reuse",
        name="Bypass on low reuse",
        description=(
            "RP bypasses or inserts at eviction for low-reuse lines flagged by "
            "reuse-distance profile."
        ),
        primary_arm="rp_insertion",
        mode="replacement_only",
        pf_guidance="Optional: reduce degree for low-reuse regions.",
        rp_guidance=(
            "Insert low-reuse prefetches at maxRRPV or bypass when reuse-distance "
            "histogram indicates one-shot access."
        ),
    ),
    Play(
        id="pf_timeliness_push",
        name="PF timeliness push",
        description="Increase prefetch distance and earlier issuance for hot PCs.",
        primary_arm="pf_timeliness",
        mode="prefetcher_only",
        pf_guidance=(
            "Issue prefetches earlier for hot PCs from miss-log hypotheses; "
            "increase lookahead without raising degree blindly."
        ),
        rp_guidance="No RP edits this round.",
    ),
    Play(
        id="pf_coverage_gap",
        name="PF coverage gap fill",
        description="Target coverage gaps: misses the PF should have caught.",
        primary_arm="pf_coverage",
        mode="prefetcher_only",
        pf_guidance=(
            "Add coverage for PCs/regions flagged as coverage_gap in miss-log "
            "hypotheses (stride, cross-page, delta tables)."
        ),
        rp_guidance="No RP edits this round.",
    ),
)

PLAY_BY_ID = {play.id: play for play in PLAY_LIBRARY}
PLAYS_BY_ARM: dict[KnobArm, list[Play]] = {}
for _play in PLAY_LIBRARY:
    PLAYS_BY_ARM.setdefault(_play.primary_arm, []).append(_play)


def select_play_for_arm(arm: KnobArm, insights: str, iteration: int) -> Play:
    """Pick a concrete play for the bandit arm using insight keywords."""

    candidates = PLAYS_BY_ARM.get(arm, list(PLAY_LIBRARY))
    lowered = insights.lower()

    if arm == "metadata_contract":
        return PLAY_BY_ID["prefetch_aware_rrip"]
    if arm == "rp_victim":
        if "conflict" in lowered:
            return PLAY_BY_ID["conflict_capacity_routing"]
        if "pf_useless" in lowered or "pollution" in lowered or "useless" in lowered:
            return PLAY_BY_ID["dead_prefetch_demotion"]
        return PLAY_BY_ID["conflict_capacity_routing"]
    if arm == "rp_insertion":
        if "reuse" in lowered or "streaming" in lowered:
            return PLAY_BY_ID["bypass_low_reuse"]
        return PLAY_BY_ID["bypass_low_reuse"]
    if arm == "pf_timeliness":
        return PLAY_BY_ID["pf_timeliness_push"]
    if arm == "pf_coverage":
        if "coverage_gap" in lowered or "compulsory" in lowered:
            return PLAY_BY_ID["pf_coverage_gap"]
        if "streaming" in lowered or "stride" in lowered:
            return PLAY_BY_ID["coverage_accuracy_handshake"]
        return PLAY_BY_ID["pf_coverage_gap"]

    return candidates[iteration % len(candidates)]
