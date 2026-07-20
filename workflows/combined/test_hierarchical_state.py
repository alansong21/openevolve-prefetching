"""Tests for file-backed hierarchical evaluation cadence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hierarchical_state import next_evaluation


class HierarchicalStateTest(unittest.TestCase):
    def test_first_then_every_tenth_is_due(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            results = [next_evaluation(every_n=10, path=path) for _ in range(20)]
        due = [item["evaluation_count"] for item in results if item["stage2_due"]]
        self.assertEqual(due, [1, 10, 20])

    def test_rejects_invalid_cadence(self):
        with self.assertRaises(ValueError):
            next_evaluation(every_n=0)


if __name__ == "__main__":
    unittest.main()
