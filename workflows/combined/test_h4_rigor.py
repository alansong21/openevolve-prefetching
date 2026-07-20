"""Tests for H4 calibration and held-out rigor."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.reward_hacking import assess_reward_hacking
from calibration import ProxyCalibration


class H4RigorTest(unittest.TestCase):
    def test_ridge_calibration_fits_and_persists(self):
        model = ProxyCalibration(minimum_pairs=5)
        for index in range(8):
            miss_reduction = index / 100.0
            model.add_observation(
                {
                    "demand_miss_reduction": miss_reduction,
                    "traffic_growth": index / 500.0,
                    "useless_prefetch_ratio": 0.2 - index / 100.0,
                    "prefetch_accuracy": 0.5 + index / 100.0,
                    "ipc_proxy": miss_reduction,
                },
                ipc_delta=2.0 * miss_reduction - index / 1000.0,
            )
        self.assertIsNotNone(model.coefficients)
        self.assertGreater(model.spearman, 0.9)
        self.assertTrue(model.trusted)
        prediction = model.predict(
            {
                "demand_miss_reduction": 0.09,
                "traffic_growth": 0.018,
                "useless_prefetch_ratio": 0.11,
                "prefetch_accuracy": 0.59,
            }
        )
        self.assertIsNotNone(prediction)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            model.save(path)
            loaded = ProxyCalibration.load(path)
        self.assertEqual(loaded.coefficients, model.coefficients)
        self.assertTrue(loaded.trusted)

    def test_heldout_regression_is_suspicious(self):
        report = assess_reward_hacking(
            {
                "train_ipc": 1.1,
                "heldout_ipc": 0.9,
            },
            {
                "train_ipc": 1.0,
                "heldout_ipc": 1.0,
            },
        )
        self.assertTrue(report.suspicious)
        self.assertTrue(any("held-out" in reason for reason in report.reasons))


if __name__ == "__main__":
    unittest.main()
