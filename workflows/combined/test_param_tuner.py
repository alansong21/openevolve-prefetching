"""Tests for the Vizier parameter-tuning agent."""

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


class ParamTunerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tuner = _load(
            "combined_param_tuner_test",
            COMBINED_DIR / "agents" / "param_tuner.py",
        )
        cls.plays = _load("combined_plays_vizier_test", COMBINED_DIR / "strategy" / "plays.py")
        cls.bandit = _load("combined_bandit_vizier_test", COMBINED_DIR / "strategy" / "bandit.py")
        cls.initial = (COMBINED_DIR / "initial_program.cc").read_text(encoding="utf-8")

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["OPENEVOLVE_BLACKBOARD_DIR"] = self._tmpdir.name
        os.environ["OPENEVOLVE_RUN_ID"] = "vizier-test"
        os.environ["OPENEVOLVE_VIZIER"] = "true"
        os.environ["OPENEVOLVE_VIZIER_BACKEND"] = "local"
        os.environ["OPENEVOLVE_VIZIER_PROB"] = "0"
        os.environ["OPENEVOLVE_VIZIER_EVERY_N"] = "0"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_search_space_loads_and_fingerprints(self):
        space = self.tuner.load_search_space(self.initial)
        names = {k.name for k in space.knobs}
        self.assertIn("kNumIpTableEntries", names)
        self.assertIn("kDrStrideEntries", names)
        self.assertTrue(space.fingerprint)

    def test_apply_parameters_updates_constexpr_and_derived(self):
        space = self.tuner.load_search_space(self.initial)
        child = self.tuner.apply_parameters(
            self.initial,
            {"kNumIpTableEntries": 2048, "kNumGhbEntries": 32},
            derived=space.derived,
        )
        self.assertIn("constexpr int kNumIpTableEntries = 2048;", child)
        self.assertIn("constexpr int kNumGhbEntries = 32;", child)
        self.assertIn("constexpr int kNumIpIndexBits = 11;", child)
        self.assertNotEqual(child, self.initial)

    def test_local_suggest_and_complete(self):
        result = self.tuner.suggest_parameter_mutation(self.initial)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertNotEqual(result.child_code, self.initial)
        self.assertEqual(result.backend, "local")
        self.assertTrue(result.parameters)

        metric = self.tuner.complete_pending_trial({"ipc": 1.23})
        self.assertAlmostEqual(metric, 1.23)
        pending = Path(self._tmpdir.name) / "vizier_pending_vizier-test.json"
        self.assertFalse(pending.is_file())

        # Local study file should record the completed trial.
        studies = list(Path(self._tmpdir.name).glob("vizier_local_*.json"))
        self.assertTrue(studies)
        data = json.loads(studies[0].read_text(encoding="utf-8"))
        completed = [
            t for t in data["trials"].values() if t.get("status") == "COMPLETED"
        ]
        self.assertEqual(len(completed), 1)
        self.assertAlmostEqual(completed[0]["metric"], 1.23)

    def test_param_tuning_play_and_arm(self):
        self.assertIn("param_tuning", self.bandit.ALL_ARMS)
        play = self.plays.select_play_for_arm("param_tuning", "", 0)
        self.assertEqual(play.id, "vizier_param_tune")

    def test_should_run_forced_mode(self):
        os.environ["OPENEVOLVE_MUTATION_MODE"] = "vizier"
        self.assertTrue(self.tuner.should_run_param_tune(1))
        os.environ["OPENEVOLVE_MUTATION_MODE"] = ""


if __name__ == "__main__":
    unittest.main()
