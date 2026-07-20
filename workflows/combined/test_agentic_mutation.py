"""Tests for Phase 2 agentic mutation building blocks."""

from __future__ import annotations

import importlib.util
import sys
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


class Phase2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.merge = _load("combined_merge_test", COMBINED_DIR / "merge.py")
        cls.critic = _load("combined_critic_test", COMBINED_DIR / "agents" / "critic.py")
        cls.directive = _load("combined_directive_test", COMBINED_DIR / "agents" / "directive.py")
        cls.implementer = _load(
            "combined_implementer_test", COMBINED_DIR / "agents" / "implementer.py"
        )
        cls.initial = (COMBINED_DIR / "initial_program.cc").read_text(encoding="utf-8")

    def test_merge_round_trip(self):
        layout = self.merge.extract_layout(self.initial)
        merged = self.merge.merge_sections(
            layout,
            layout.prefetcher_section,
            layout.replacement_section,
        )
        self.merge.validate_combined_source(merged)

    def test_dual_backend_split(self):
        split = sys.modules["split_source"].split_dual_backend_source(self.initial)
        self.assertEqual(len(split), 4)
        self.assertIn("openevolve_prefetcher", split[0])
        self.assertIn("openevolve_replacement", split[1])
        self.assertIn("drcachesim_create_prefetcher_factory", split[2])
        self.assertIn("drcachesim_create_replacement_policy", split[3])

    def test_critic_accepts_seed(self):
        report = self.critic.review_combined_source(self.initial)
        self.assertTrue(report.approved, report.reasons)

    def test_critic_rejects_missing_marker(self):
        broken = self.initial.replace(self.merge.PREFETCHER_BEGIN, "// broken")
        report = self.critic.review_combined_source(broken)
        self.assertFalse(report.approved)

    def test_unified_implementer_receives_all_backends(self):
        prompt = self.implementer.build_implementer_prompt(
            combined_source=self.initial,
            directive_text="joint design",
            insights="stride stream",
        )
        self.assertIn("OPENEVOLVE_PREFETCHER_BEGIN", prompt)
        self.assertIn("OPENEVOLVE_REPLACEMENT_BEGIN", prompt)
        self.assertIn("OPENEVOLVE_DR_PREFETCHER_BEGIN", prompt)
        self.assertIn("OPENEVOLVE_DR_REPLACEMENT_BEGIN", prompt)

    def test_directive_modes(self):
        d = self.directive.synthesize_directive("coverage_gap conflict", {"l2c_pf_useless": 100, "l2c_pf_useful": 1}, 1)
        self.assertEqual(d["mode"], "joint")
        self.assertEqual(d["focus_component"], "replacement")
        self.assertTrue(d["edit_prefetcher"])
        self.assertTrue(d["edit_replacement"])


if __name__ == "__main__":
    unittest.main()
