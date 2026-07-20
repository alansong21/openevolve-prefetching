"""Tests for combined hierarchical evaluator gating."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hierarchical_state import next_evaluation, stage1_gate_metrics


class HierarchicalEvaluatorTest(unittest.TestCase):
    def test_first_evaluation_runs_stage2_then_proxy_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            first = stage1_gate_metrics(
                next_evaluation(every_n=10, path=path),
                ipc_proxy=0.2,
                available=True,
                threshold=0.0,
            )
            second = stage1_gate_metrics(
                next_evaluation(every_n=10, path=path),
                ipc_proxy=0.2,
                available=True,
                threshold=0.0,
            )

        self.assertEqual(first["stage2_due"], 1.0)
        self.assertEqual(first["combined_score"], 1.0)
        self.assertEqual(second["stage2_due"], 0.0)
        self.assertLess(second["combined_score"], 0.0)
        self.assertEqual(second["promotion_eligible"], 0.0)

    def test_proxy_quality_gate_can_skip_due_stage2(self):
        with tempfile.TemporaryDirectory() as directory:
            cadence = next_evaluation(
                every_n=10, path=Path(directory) / "state.json"
            )
            result = stage1_gate_metrics(
                cadence,
                ipc_proxy=-0.2,
                available=True,
                threshold=0.1,
            )

        self.assertEqual(result["stage1_passed"], 0.0)
        self.assertEqual(result["stage2_due"], 0.0)
        self.assertLess(result["combined_score"], 0.0)


if __name__ == "__main__":
    unittest.main()
