"""Tests for H3 storage, anti-hacking, and drcachesim analysis."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.critic import review_combined_source
from agents.drcachesim_analysis import analyze_drcachesim
from agents.storage import analyze_storage
from blackboard import Blackboard
from insight_service import build_insight_bundle


INITIAL = (Path(__file__).resolve().parent / "initial_program.cc").read_text(
    encoding="utf-8"
)


class H3GateTest(unittest.TestCase):
    def test_seed_storage_is_bounded_and_low_budget_rejects(self):
        self.assertTrue(analyze_storage(INITIAL).approved)
        report = analyze_storage(INITIAL, budget_bytes=1024)
        self.assertFalse(report.approved)
        self.assertGreater(report.metrics()["storage_budget_ratio"], 1.0)

    def test_storage_rejects_unbounded_map(self):
        modified = INITIAL.replace(
            "namespace {", "namespace {\nstd::unordered_map<uint64_t, int> exploit;", 1
        )
        report = analyze_storage(modified)
        self.assertFalse(report.approved)
        self.assertTrue(any("unbounded" in reason for reason in report.reasons))

    def test_critic_rejects_hardcoded_pc(self):
        modified = INITIAL.replace(
            "// EVOLVE-BLOCK-START",
            "// EVOLVE-BLOCK-START\n// if (pc == 0x123456) specialize workload",
            1,
        )
        report = review_combined_source(modified)
        self.assertFalse(report.approved)
        self.assertTrue(any("program-counter" in reason for reason in report.reasons))

    def test_dynamic_hack_does_not_update_bandit(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"OPENEVOLVE_BLACKBOARD_DIR": directory, "OPENEVOLVE_RUN_ID": "h3"},
            clear=False,
        ):
            board = Blackboard.load()
            board.set_pending_reward(
                arm="pf_coverage",
                play_id="pf_coverage_gap",
                mode="joint",
                parent_ipc=1.0,
                parent_id="p",
                iteration=10,
                contract_id=None,
            )
            metrics = {
                "ipc": 0.99,
                "combined_score": 0.99,
                "stage2_ran": 1.0,
                "ipc_proxy": 0.2,
            }
            reward = board.record_evaluation_result(
                child_id="c",
                parent_id="p",
                child_metrics=metrics,
                parent_metrics={"ipc": 1.0},
            )
        self.assertIsNone(reward)
        self.assertEqual(metrics["reward_hack_suspected"], 1.0)
        self.assertLess(metrics["combined_score"], 0.0)
        self.assertEqual(board.bandit.arms["pf_coverage"].pulls, 0)
        self.assertEqual(board.tried_ideas[-1].outcome, "reward_hack_suspected")

    def test_drcachesim_analysis_enters_insight_bundle(self):
        metrics = {
            "stage1_available": 1.0,
            "ipc_proxy": -0.1,
            "demand_miss_reduction": -0.05,
            "traffic_growth": 0.3,
            "prefetch_accuracy": 0.01,
            "drcachesim_prefetch_misses": 500,
        }
        analysis = analyze_drcachesim(metrics)
        bundle = build_insight_bundle(
            "test",
            artifacts={
                "drcachesim_metrics": metrics,
                "drcachesim_analysis": analysis,
            },
        )
        self.assertIn("=== drcachesim analysis ===", bundle)
        self.assertIn("bandwidth/pollution risk", bundle)


if __name__ == "__main__":
    unittest.main()
