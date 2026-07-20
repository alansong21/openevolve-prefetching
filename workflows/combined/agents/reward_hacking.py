"""Rule-based post-evaluation reward-hacking signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RewardHackReport:
    suspicious: bool
    reasons: list[str] = field(default_factory=list)

    def text(self) -> str:
        if not self.suspicious:
            return "Reward-hack checks: no rule fired"
        return "Reward-hack suspected:\n- " + "\n- ".join(self.reasons)


def assess_reward_hacking(
    child_metrics: dict[str, Any],
    parent_metrics: dict[str, Any] | None = None,
) -> RewardHackReport:
    reasons: list[str] = []
    useful = float(
        child_metrics.get(
            "l2c_pf_useful", child_metrics.get("drcachesim_prefetch_hits", 0.0)
        )
    )
    useless = float(
        child_metrics.get(
            "l2c_pf_useless", child_metrics.get("drcachesim_prefetch_misses", 0.0)
        )
    )
    issued = useful + useless
    if issued >= 1000 and useful / max(issued, 1.0) < 0.01:
        reasons.append("prefetch issue volume is high with under 1% useful fraction")

    parent_ipc = (
        float(parent_metrics["ipc"])
        if parent_metrics and parent_metrics.get("ipc") is not None
        else None
    )
    child_ipc = (
        float(child_metrics["ipc"]) if child_metrics.get("ipc") is not None else None
    )
    if (
        child_metrics.get("stage2_ran")
        and float(child_metrics.get("ipc_proxy", 0.0)) >= 0.05
        and parent_ipc is not None
        and child_ipc is not None
        and child_ipc <= parent_ipc
    ):
        reasons.append(
            "drcachesim proxy improved materially but authoritative ChampSim IPC did not"
        )

    if (
        float(child_metrics.get("storage_budget_ratio", 0.0)) >= 0.95
        and parent_ipc is not None
        and child_ipc is not None
        and child_ipc - parent_ipc < 0.001
    ):
        reasons.append("near-budget storage growth produced negligible IPC improvement")

    train_delta = child_metrics.get("train_ipc_delta")
    heldout_delta = child_metrics.get("heldout_ipc_delta")
    if parent_metrics:
        if (
            train_delta is None
            and child_metrics.get("train_ipc") is not None
            and parent_metrics.get("train_ipc") is not None
        ):
            train_delta = float(child_metrics["train_ipc"]) - float(
                parent_metrics["train_ipc"]
            )
        if (
            heldout_delta is None
            and child_metrics.get("heldout_ipc") is not None
            and parent_metrics.get("heldout_ipc") is not None
        ):
            heldout_delta = float(child_metrics["heldout_ipc"]) - float(
                parent_metrics["heldout_ipc"]
            )
    if (
        train_delta is not None
        and heldout_delta is not None
        and float(train_delta) > 0
        and float(heldout_delta) < 0
    ):
        reasons.append("training IPC improved while held-out IPC regressed")

    return RewardHackReport(suspicious=bool(reasons), reasons=reasons)
