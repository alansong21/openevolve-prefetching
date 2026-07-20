"""Tests for drcachesim stats parsing and stage-1 scoring."""

from __future__ import annotations

import unittest

from drcachesim_stats import (
    compute_stage1_proxy,
    parse_drcachesim_stats,
    select_data_cache,
)


OUTPUT = """
Core #0:
  L1D (32KB, 8-way) stats:
    Hits:                           1,200
    Misses:                           300
    Compulsory misses:                100
    Invalidations:                      0
    Miss rate:                      20.00%
    Prefetch hits:                    120
    Prefetch misses:                   30
LL (8MB, 16-way) stats:
    Hits:                             200
    Misses:                           100
    Compulsory misses:                 80
    Invalidations:                      0
    Local miss rate:                33.33%
    Child hits:                     1,200
    Total miss rate:                 6.67%
"""


class DrCacheSimStatsTest(unittest.TestCase):
    def test_parse_cache_blocks(self):
        stats = parse_drcachesim_stats(OUTPUT)
        self.assertEqual(stats["L1D"].hits, 1200)
        self.assertEqual(stats["L1D"].prefetch_hits, 120)
        self.assertAlmostEqual(stats["L1D"].prefetch_accuracy, 0.8)
        self.assertEqual(stats["LL"].misses, 100)
        self.assertAlmostEqual(stats["LL"].miss_rate, 1 / 3, places=3)
        self.assertAlmostEqual(stats["LL"].total_miss_rate, 0.0667)

    def test_selects_modeled_data_cache(self):
        # Prefer L2C when present; otherwise fall back through L2/LL/L1D.
        self.assertEqual(select_data_cache(parse_drcachesim_stats(OUTPUT)).name, "LL")
        l2c_output = OUTPUT.replace("LL (", "L2C (", 1)
        self.assertEqual(select_data_cache(parse_drcachesim_stats(l2c_output)).name, "L2C")

    def test_proxy_rewards_miss_reduction(self):
        baseline = parse_drcachesim_stats(OUTPUT)["L1D"]
        candidate_output = OUTPUT.replace("1,200", "1,250", 1).replace(
            "300", "250", 1
        )
        candidate = parse_drcachesim_stats(candidate_output)["L1D"]
        proxy = compute_stage1_proxy(candidate, baseline)
        self.assertGreater(proxy["ipc_proxy"], 0)
        self.assertAlmostEqual(proxy["demand_miss_reduction"], 1 / 6)


if __name__ == "__main__":
    unittest.main()
