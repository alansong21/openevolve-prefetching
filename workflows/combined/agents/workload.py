"""Workload characterization agent (deterministic, reads cached profile JSON)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PF_BIAS: dict[str, str] = {
    "streaming": (
        "Raise prefetch degree and distance; use stride/stream detectors and "
        "next-line fallbacks. Prefer timeliness over accuracy."
    ),
    "constant_stride": (
        "Per-PC stride tables with moderate degree; cross-page stride when "
        "page_crossing_rate is high."
    ),
    "complex_stride": (
        "Multi-delta / GHB-style correlation; throttle degree when MPKI stays high."
    ),
    "pointer_chasing": (
        "Delta/temporal prefetch (GHB, IP-indexed delta tables); avoid polluting "
        "L2 with low-confidence wide prefetches."
    ),
    "irregular": (
        "Conservative prefetch with confidence gating; focus on high-confidence "
        "PC-local patterns only."
    ),
    "mixed": (
        "Hybrid: stride for hot PCs, conservative default elsewhere; MPKC-based throttle."
    ),
}

RP_BIAS: dict[str, str] = {
    "streaming": (
        "Streaming-friendly insertion (BRRIP/DRRIP bias); avoid over-protecting "
        "one-shot lines; consider bypass for low-reuse prefetches."
    ),
    "constant_stride": (
        "Protect demand lines on recurring strides; demote untouched prefetches quickly."
    ),
    "complex_stride": (
        "RRIP with set-dueling; protect reused lines, near-evict low-confidence prefetches."
    ),
    "pointer_chasing": (
        "Strong protection for revisited nodes; dead-prefetch demotion; SRRIP for irregular reuse."
    ),
    "irregular": (
        "Conflict-aware victim selection; prefetch-aware insertion using metadata; "
        "evict useless prefetches first."
    ),
    "mixed": (
        "DRRIP baseline with prefetch flag handling; metadata-driven insertion RRPV."
    ),
}


def _load_profile(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _summarize_profile(name: str, profile: dict[str, Any]) -> list[str]:
    taxonomy = profile.get("access_pattern_taxonomy", "unknown")
    scores = profile.get("scores", {})
    lines = [
        f"Trace: {name}",
        f"  Taxonomy: {taxonomy}",
        f"  Memory intensity: {profile.get('memory_intensity', 0):.3f} loads/instr",
        f"  Page-crossing rate: {profile.get('page_crossing_rate', 0):.2%}",
        (
            f"  Scores: streaming={scores.get('streaming', 0):.2f}, "
            f"pointer_chasing={scores.get('pointer_chasing', 0):.2f}, "
            f"irregular={scores.get('irregular', 0):.2f}"
        ),
    ]

    top_pcs = profile.get("top_pc_delta_summary") or []
    if top_pcs:
        hot = top_pcs[0]
        lines.append(
            f"  Hottest PC: {hot.get('pc')} "
            f"(dominant delta {hot.get('dominant_delta_hex', hot.get('dominant_delta'))}, "
            f"{hot.get('load_count', 0)} loads in profile window)"
        )

    lines.append(f"  Prefetcher bias: {PF_BIAS.get(taxonomy, PF_BIAS['mixed'])}")
    lines.append(f"  Replacement bias: {RP_BIAS.get(taxonomy, RP_BIAS['mixed'])}")
    return lines


def characterize_workloads(
    trace_names: list[str],
    profile_dir: Path,
) -> str:
    """Return prose workload characterization for the configured traces."""

    if not trace_names:
        return "No traces configured; workload profiles unavailable."

    sections: list[str] = ["=== Workload characterization ==="]
    found = 0
    for name in trace_names:
        stem = Path(name).stem
        profile = _load_profile(profile_dir / f"{stem}.json")
        if profile is None:
            sections.append(f"Trace: {name}\n  (profile missing — run scripts/profile_trace.py)")
            continue
        found += 1
        sections.extend(_summarize_profile(name, profile))
        sections.append("")

    if found == 0:
        sections.append(
            f"No cached profiles under {profile_dir}. "
            "Run: python scripts/profile_all_spec_traces.py"
        )
    return "\n".join(sections).strip()
