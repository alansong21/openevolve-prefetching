"""Blackboard: shared orchestrator memory mirrored to JSON on disk."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from strategy.bandit import KnobArm, StrategyBandit
from agents.reward_hacking import assess_reward_hacking
from calibration import ProxyCalibration

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_DIR = REPO_ROOT / "workflows" / "combined" / "state"


def _state_path() -> Path:
    state_dir = Path(os.environ.get("OPENEVOLVE_BLACKBOARD_DIR", DEFAULT_STATE_DIR))
    state_dir.mkdir(parents=True, exist_ok=True)
    run_id = os.environ.get("OPENEVOLVE_RUN_ID", "default")
    return state_dir / f"blackboard_{run_id}.json"


@dataclass
class TriedIdea:
    key: str
    play_id: str
    arm: KnobArm
    iteration: int
    outcome: str
    reward: float | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "play_id": self.play_id,
            "arm": self.arm,
            "iteration": self.iteration,
            "outcome": self.outcome,
            "reward": self.reward,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TriedIdea:
        return cls(
            key=str(data["key"]),
            play_id=str(data["play_id"]),
            arm=data["arm"],  # type: ignore[arg-type]
            iteration=int(data["iteration"]),
            outcome=str(data["outcome"]),
            reward=data.get("reward"),
            timestamp=float(data.get("timestamp", time.time())),
        )


@dataclass
class Blackboard:
    """Static + dynamic orchestrator memory."""

    bandit: StrategyBandit = field(default_factory=StrategyBandit)
    tried_ideas: list[TriedIdea] = field(default_factory=list)
    workload_profile_cache: dict[str, str] = field(default_factory=dict)
    recent_results: list[dict[str, Any]] = field(default_factory=list)
    pending_reward: dict[str, Any] | None = None
    last_directive: dict[str, Any] | None = None

    @classmethod
    def load(cls) -> Blackboard:
        path = _state_path()
        if not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        board = cls(
            bandit=StrategyBandit.from_dict(data.get("bandit")),
            workload_profile_cache=dict(data.get("workload_profile_cache", {})),
            recent_results=list(data.get("recent_results", [])),
            pending_reward=data.get("pending_reward"),
            last_directive=data.get("last_directive"),
        )
        board.tried_ideas = [TriedIdea.from_dict(item) for item in data.get("tried_ideas", [])]
        return board

    def save(self) -> None:
        path = _state_path()
        payload = {
            "bandit": self.bandit.to_dict(),
            "tried_ideas": [idea.to_dict() for idea in self.tried_ideas[-200:]],
            "workload_profile_cache": self.workload_profile_cache,
            "recent_results": self.recent_results[-50:],
            "pending_reward": self.pending_reward,
            "last_directive": self.last_directive,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def idea_key(play_id: str, arm: KnobArm, mode: str) -> str:
        raw = f"{play_id}|{arm}|{mode}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def is_tried_recently(self, play_id: str, arm: KnobArm, mode: str, *, max_age: int = 3) -> bool:
        key = self.idea_key(play_id, arm, mode)
        recent = [idea for idea in self.tried_ideas if idea.key == key]
        if not recent:
            return False
        last = recent[-1]
        return last.outcome in {"failed_critic", "rejected"} and (
            len(self.tried_ideas) - self.tried_ideas.index(last) <= max_age
        )

    def record_tried_idea(
        self,
        *,
        play_id: str,
        arm: KnobArm,
        mode: str,
        iteration: int,
        outcome: str,
        reward: float | None = None,
    ) -> None:
        self.tried_ideas.append(
            TriedIdea(
                key=self.idea_key(play_id, arm, mode),
                play_id=play_id,
                arm=arm,
                iteration=iteration,
                outcome=outcome,
                reward=reward,
            )
        )

    def set_pending_reward(
        self,
        *,
        arm: KnobArm,
        play_id: str,
        mode: str,
        parent_ipc: float | None,
        parent_id: str | None,
        iteration: int,
        contract_id: str | None,
    ) -> None:
        self.pending_reward = {
            "arm": arm,
            "play_id": play_id,
            "mode": mode,
            "parent_ipc": parent_ipc,
            "parent_id": parent_id,
            "iteration": iteration,
            "contract_id": contract_id,
        }

    def pop_pending_reward(self) -> dict[str, Any] | None:
        pending = self.pending_reward
        self.pending_reward = None
        return pending

    def record_evaluation_result(
        self,
        *,
        child_id: str,
        parent_id: str | None,
        child_metrics: dict[str, Any],
        parent_metrics: dict[str, Any] | None,
    ) -> float | None:
        """Apply bandit reward from evaluation and return IPC delta if computable."""

        pending = self.pop_pending_reward()
        hack_report = assess_reward_hacking(child_metrics, parent_metrics)
        if hack_report.suspicious:
            child_metrics["reward_hack_suspected"] = 1.0
            child_metrics["promotion_eligible"] = 0.0
            if "combined_score" in child_metrics:
                child_metrics["combined_score"] = min(
                    float(child_metrics["combined_score"]), -0.01
                )
        else:
            child_metrics["reward_hack_suspected"] = 0.0
        parent_ipc = None
        child_ipc = child_metrics.get("ipc")
        if parent_metrics and parent_metrics.get("ipc") is not None:
            parent_ipc = float(parent_metrics["ipc"])
        elif pending and pending.get("parent_ipc") is not None:
            parent_ipc = float(pending["parent_ipc"])

        reward = None
        if parent_ipc is not None and child_ipc is not None:
            reward = float(child_ipc) - parent_ipc
            # Small MPKI improvement bonus.
            parent_mpki = parent_metrics.get("l2c_mpki") if parent_metrics else None
            child_mpki = child_metrics.get("l2c_mpki")
            if parent_mpki is not None and child_mpki is not None:
                reward += 0.001 * (float(parent_mpki) - float(child_mpki))

        if (
            child_metrics.get("stage2_ran")
            and parent_ipc is not None
            and child_ipc is not None
            and child_metrics.get("stage1_available")
        ):
            calibration = ProxyCalibration.load()
            actual_delta = float(child_ipc) - parent_ipc
            prior_prediction = calibration.predict(
                child_metrics, require_trusted=False
            )
            if prior_prediction is not None:
                child_metrics["calibration_prediction_error"] = (
                    prior_prediction - actual_delta
                )
            calibration.add_observation(child_metrics, actual_delta)
            calibration.save()
            child_metrics["calibration_spearman"] = calibration.spearman
            child_metrics["calibration_mae"] = (
                calibration.mean_absolute_error or 0.0
            )
            child_metrics["calibration_trusted"] = (
                1.0 if calibration.trusted else 0.0
            )

        if pending and reward is not None and not hack_report.suspicious:
            self.bandit.update(pending["arm"], reward)
            self.record_tried_idea(
                play_id=pending["play_id"],
                arm=pending["arm"],
                mode=pending["mode"],
                iteration=int(pending.get("iteration", 0)),
                outcome="evaluated",
                reward=reward,
            )
        elif pending and hack_report.suspicious:
            self.record_tried_idea(
                play_id=pending["play_id"],
                arm=pending["arm"],
                mode=pending["mode"],
                iteration=int(pending.get("iteration", 0)),
                outcome="reward_hack_suspected",
                reward=None,
            )

        self.recent_results.append(
            {
                "child_id": child_id,
                "parent_id": parent_id,
                "child_ipc": child_ipc,
                "parent_ipc": parent_ipc,
                "reward": reward,
                "arm": pending.get("arm") if pending else None,
                "play_id": pending.get("play_id") if pending else None,
                "reward_hack_suspected": hack_report.suspicious,
                "reward_hack_reasons": hack_report.reasons,
                "timestamp": time.time(),
            }
        )
        self.save()
        return None if hack_report.suspicious else reward

    def orchestrator_context(self, *, limit: int = 5) -> str:
        """Short summary of tried ideas for engineer prompts."""

        if not self.tried_ideas:
            return "Blackboard: no prior tried ideas recorded."
        lines = ["Blackboard — recent tried ideas (avoid repeating dead ends):"]
        for idea in self.tried_ideas[-limit:]:
            reward_txt = f", reward={idea.reward:+.4f}" if idea.reward is not None else ""
            lines.append(
                f"- iter {idea.iteration}: play={idea.play_id}, arm={idea.arm}, "
                f"outcome={idea.outcome}{reward_txt}"
            )
        return "\n".join(lines)
