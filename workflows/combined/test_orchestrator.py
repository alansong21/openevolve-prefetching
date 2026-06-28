"""Tests for Phase 3 orchestrator, bandit, blackboard, and plays."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMBINED_DIR = REPO_ROOT / "workflows" / "combined"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if str(COMBINED_DIR) not in sys.path:
        sys.path.insert(0, str(COMBINED_DIR))
    spec.loader.exec_module(module)
    return module


class Phase3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bandit = _load("combined_bandit_test", COMBINED_DIR / "strategy" / "bandit.py")
        cls.plays = _load("combined_plays_test", COMBINED_DIR / "strategy" / "plays.py")
        cls.blackboard = _load("combined_blackboard_test", COMBINED_DIR / "blackboard.py")
        cls.metadata = _load("combined_metadata_test", COMBINED_DIR / "metadata_contract.py")
        cls.directive = _load("combined_directive_p3_test", COMBINED_DIR / "agents" / "directive.py")
        cls.orchestrator = _load("combined_orchestrator_test", COMBINED_DIR / "orchestrator.py")
        cls.initial = (COMBINED_DIR / "initial_program.cc").read_text(encoding="utf-8")

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["OPENEVOLVE_BLACKBOARD_DIR"] = self._tmpdir.name
        os.environ["OPENEVOLVE_RUN_ID"] = "test"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_bandit_updates_reward(self):
        bandit = self.bandit.StrategyBandit()
        arm = bandit.select_arm(insights="coverage_gap", metrics={"l2c_pf_useless": 0})
        self.assertIn(arm, self.bandit.ALL_ARMS)
        bandit.update(arm, 0.05)
        self.assertEqual(bandit.arms[arm].pulls, 1)
        self.assertAlmostEqual(bandit.arms[arm].mean_reward, 0.05)

    def test_play_selection_for_conflict(self):
        play = self.plays.select_play_for_arm("rp_victim", "conflict hot set", 1)
        self.assertEqual(play.id, "conflict_capacity_routing")

    def test_orchestrated_directive_includes_play(self):
        play = self.plays.PLAY_BY_ID["prefetch_aware_rrip"]
        directive = self.directive.synthesize_orchestrated_directive(
            insights="streaming workload",
            metrics={"ipc": 1.2, "l2c_mpki": 10},
            iteration=2,
            arm="metadata_contract",
            play=play,
        )
        self.assertEqual(directive["play_id"], "prefetch_aware_rrip")
        self.assertEqual(directive["metadata_contract_id"], "confidence_rrpv")
        self.assertTrue(directive["edit_prefetcher"])
        self.assertTrue(directive["edit_replacement"])

    def test_blackboard_persistence_and_reward(self):
        board = self.blackboard.Blackboard.load()
        board.set_pending_reward(
            arm="pf_coverage",
            play_id="pf_coverage_gap",
            mode="prefetcher_only",
            parent_ipc=1.0,
            parent_id="parent-1",
            iteration=3,
            contract_id=None,
        )
        board.save()

        reloaded = self.blackboard.Blackboard.load()
        self.assertIsNotNone(reloaded.pending_reward)
        reward = reloaded.record_evaluation_result(
            child_id="child-1",
            parent_id="parent-1",
            child_metrics={"ipc": 1.05, "l2c_mpki": 9},
            parent_metrics={"ipc": 1.0, "l2c_mpki": 10},
        )
        self.assertIsNotNone(reward)
        self.assertAlmostEqual(reward, 0.05 + 0.001, places=3)
        self.assertEqual(reloaded.bandit.arms["pf_coverage"].pulls, 1)

        path = Path(self._tmpdir.name) / "blackboard_test.json"
        self.assertTrue(path.is_file())

    def test_metadata_contract_seed_passes(self):
        split_source = _load("combined_split_test", COMBINED_DIR / "split_source.py")
        markers = split_source.parse_marker_positions(self.initial)
        pf = self.initial[markers["PREFETCHER_BEGIN"] : markers["PREFETCHER_END"]]
        rp = self.initial[markers["REPLACEMENT_BEGIN"] : markers["REPLACEMENT_END"]]
        issues = self.metadata.check_metadata_contract(
            pf, rp, "confidence_rrpv", joint_edit=True
        )
        self.assertEqual(issues, [])

    def test_orchestrator_nodes_without_llm(self):
        board = self.blackboard.Blackboard.load()
        state = {
            "parent_code": self.initial,
            "parent_artifacts": None,
            "parent_metrics": {"ipc": 1.0, "l2c_pf_useless": 50, "l2c_pf_useful": 5},
            "parent_id": "p1",
            "iteration": 1,
            "blackboard": board,
        }
        state = self.orchestrator.node_load_blackboard(state)
        state = self.orchestrator.node_run_analysts(state)
        state = self.orchestrator.node_select_strategy(state)
        state = self.orchestrator.node_synthesize_directive(state)
        self.assertIn("directive", state)
        self.assertIn("play", state)
        self.assertIn(state["directive"]["play_id"], self.plays.PLAY_BY_ID)


if __name__ == "__main__":
    unittest.main()
