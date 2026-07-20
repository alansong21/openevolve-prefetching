"""Deterministic analysis of drcachesim stage-1 results."""

from __future__ import annotations

from typing import Any

from drcachesim_stats import parse_drcachesim_stats


def analyze_drcachesim(
    metrics: dict[str, Any] | None = None,
    output: str | None = None,
) -> str:
    metrics = metrics or {}
    if not metrics and not output:
        return "=== drcachesim analysis ===\nNo stage-1 results are available."

    lines = ["=== drcachesim analysis ==="]
    if not bool(metrics.get("stage1_available", 1.0)):
        lines.append("Stage 1 was unavailable; do not infer cache behavior from its proxy.")
        return "\n".join(lines)

    proxy = float(metrics.get("ipc_proxy", 0.0))
    miss_delta = float(metrics.get("demand_miss_reduction", 0.0))
    traffic = float(metrics.get("traffic_growth", 0.0))
    accuracy = float(metrics.get("prefetch_accuracy", 0.0))
    lines.append(
        f"Proxy={proxy:+.4f}; demand-miss reduction={miss_delta:+.2%}; "
        f"traffic growth={traffic:+.2%}; prefetch accuracy={accuracy:.2%}."
    )
    if miss_delta < 0:
        lines.append(
            "Hypothesis: the candidate increases modeled L2 demand misses; reduce "
            "pollution or make insertion more selective."
        )
    elif miss_delta > 0.03 and traffic < 0.10:
        lines.append(
            "Hypothesis: useful coverage improved without disproportionate traffic; "
            "preserve this mechanism while validating IPC."
        )
    if traffic > 0.25:
        lines.append(
            "Hypothesis: bandwidth/pollution risk is high; raise confidence thresholds "
            "or lower prefetch degree."
        )
    if accuracy < 0.05 and float(metrics.get("drcachesim_prefetch_misses", 0.0)) > 100:
        lines.append(
            "Hypothesis: prefetch stream is mostly unused; coordinate throttling with "
            "low-priority replacement insertion."
        )

    if output:
        parsed = parse_drcachesim_stats(output)
        if "L1D" in parsed and "LL" in parsed:
            l1d, ll = parsed["L1D"], parsed["LL"]
            lines.append(
                f"Modeled-L2 miss rate={l1d.miss_rate:.2%}; downstream LL miss "
                f"rate={ll.miss_rate:.2%}."
            )
            if l1d.miss_rate > 0.25 and ll.miss_rate < l1d.miss_rate / 2:
                lines.append(
                    "Hypothesis: misses shift downstream rather than disappearing; "
                    "the proxy may overstate value."
                )
    return "\n".join(lines)
