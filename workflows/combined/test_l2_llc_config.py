"""Tests for ChampSim-matched L2C→LLC drcachesim configs (DPC4 1C.limitBW)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from drcachesim_runner import (
    load_hierarchy_geometry,
    load_l2_geometry,
    render_l2_llc_config,
    write_l2_only_config,
)


class L2LlcConfigTest(unittest.TestCase):
    def test_hierarchy_matches_combined_champsim(self):
        hierarchy = load_hierarchy_geometry()
        self.assertEqual(hierarchy.line_size, 64)
        self.assertEqual(hierarchy.l2c.sets, 2048)
        self.assertEqual(hierarchy.l2c.ways, 16)
        self.assertEqual(hierarchy.l2c.size_bytes, 2 * 1024 * 1024)
        self.assertEqual(hierarchy.l2c.latency_cycles, 10)
        self.assertEqual(hierarchy.llc.sets, 4096)
        self.assertEqual(hierarchy.llc.ways, 12)
        self.assertEqual(hierarchy.llc.size_bytes, 3 * 1024 * 1024)
        self.assertEqual(hierarchy.llc.latency_cycles, 35)
        self.assertEqual(hierarchy.physical_memory.get("tCAS"), 6)
        self.assertEqual(hierarchy.physical_memory.get("tRCD"), 6)
        self.assertEqual(hierarchy.physical_memory.get("tRP"), 6)
        self.assertEqual(hierarchy.physical_memory.get("tRAS"), 13)
        self.assertEqual(hierarchy.physical_memory.get("data_rate"), 800)
        self.assertFalse(hierarchy.memory_latency_supported)

    def test_l2_geometry_compat_wrapper(self):
        geometry = load_l2_geometry()
        self.assertEqual(geometry.sets, 2048)
        self.assertEqual(geometry.ways, 16)
        self.assertEqual(geometry.size_label, "2M")

    def test_config_is_l2c_to_llc(self):
        hierarchy = load_hierarchy_geometry()
        text = render_l2_llc_config(
            hierarchy=hierarchy,
            replacement="CUSTOM",
            prefetcher="custom",
            warmup_refs=10,
            sim_refs=20,
        )
        self.assertIn("num_cores       1", text)
        self.assertIn("L2C {", text)
        self.assertIn("LLC {", text)
        self.assertIn("size            2M", text)
        self.assertIn("assoc           16", text)
        self.assertIn("size            3M", text)
        self.assertIn("assoc           12", text)
        self.assertIn("parent          LLC", text)
        self.assertIn("parent          memory", text)
        self.assertIn("replace_policy  CUSTOM", text)
        self.assertIn("prefetcher      custom", text)
        self.assertIn("replace_policy  LRU", text)
        self.assertIn("prefetcher      none", text)
        self.assertIn("tCAS=6", text)
        self.assertIn("L2C=10c", text)
        self.assertIn("LLC=35c", text)
        self.assertNotIn("L1D", text)
        self.assertNotIn("L1I", text)

    def test_write_config_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_l2_only_config(
                Path(directory) / "hierarchy.config",
                replacement="LRU",
                prefetcher="none",
            )
            text = path.read_text(encoding="utf-8")
        self.assertIn("L2C {", text)
        self.assertIn("LLC {", text)
        self.assertIn("replace_policy  LRU", text)
        self.assertIn("prefetcher      none", text)


if __name__ == "__main__":
    unittest.main()
