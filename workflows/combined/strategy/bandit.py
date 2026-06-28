"""Multi-armed bandit over co-design knob categories."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Literal

KnobArm = Literal[
    "pf_coverage",
    "pf_timeliness",
    "rp_insertion",
    "rp_victim",
    "metadata_contract",
]

ALL_ARMS: tuple[KnobArm, ...] = (
    "pf_coverage",
    "pf_timeliness",
    "rp_insertion",
    "rp_victim",
    "metadata_contract",
)


@dataclass
class ArmStats:
    pulls: int = 0
    total_reward: float = 0.0

    @property
    def mean_reward(self) -> float:
        if self.pulls == 0:
            return 0.0
        return self.total_reward / self.pulls


@dataclass
class StrategyBandit:
    """UCB1 bandit with epsilon exploration."""

    arms: dict[KnobArm, ArmStats] = field(default_factory=lambda: {arm: ArmStats() for arm in ALL_ARMS})
    epsilon: float = 0.15
    ucb_c: float = 1.4
    total_pulls: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> StrategyBandit:
        bandit = cls()
        if not data:
            return bandit
        bandit.epsilon = float(data.get("epsilon", bandit.epsilon))
        bandit.ucb_c = float(data.get("ucb_c", bandit.ucb_c))
        bandit.total_pulls = int(data.get("total_pulls", 0))
        for arm in ALL_ARMS:
            arm_data = data.get("arms", {}).get(arm, {})
            bandit.arms[arm] = ArmStats(
                pulls=int(arm_data.get("pulls", 0)),
                total_reward=float(arm_data.get("total_reward", 0.0)),
            )
        return bandit

    def to_dict(self) -> dict[str, Any]:
        return {
            "epsilon": self.epsilon,
            "ucb_c": self.ucb_c,
            "total_pulls": self.total_pulls,
            "arms": {
                arm: {"pulls": stats.pulls, "total_reward": stats.total_reward}
                for arm, stats in self.arms.items()
            },
        }

    def _ucb_score(self, arm: KnobArm) -> float:
        stats = self.arms[arm]
        if stats.pulls == 0:
            return float("inf")
        exploit = stats.mean_reward
        explore = self.ucb_c * math.sqrt(math.log(max(self.total_pulls, 1)) / stats.pulls)
        return exploit + explore

    def select_arm(self, *, insights: str = "", metrics: dict[str, Any] | None = None) -> KnobArm:
        """Select a knob arm, biasing toward under-explored or high-reward arms."""

        if random.random() < self.epsilon:
            return random.choice(ALL_ARMS)

        # One-shot hints from metrics before UCB takes over.
        if metrics:
            pf_useless = float(metrics.get("l2c_pf_useless", 0) or 0)
            pf_useful = float(metrics.get("l2c_pf_useful", 0) or 0)
            if pf_useless > pf_useful and pf_useless >= 20:
                return "rp_victim"
            if pf_useful > 0 and pf_useless == 0 and self.arms["pf_coverage"].pulls < 2:
                return "pf_coverage"

        lowered = insights.lower()
        if "coverage_gap" in lowered and self.arms["pf_coverage"].pulls < 3:
            return "pf_coverage"
        if "conflict" in lowered and self.arms["rp_victim"].pulls < 3:
            return "rp_victim"

        return max(ALL_ARMS, key=self._ucb_score)

    def update(self, arm: KnobArm, reward: float) -> None:
        stats = self.arms[arm]
        stats.pulls += 1
        stats.total_reward += reward
        self.total_pulls += 1
