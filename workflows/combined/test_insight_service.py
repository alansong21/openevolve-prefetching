"""Tests for Phase 1 combined workflow insight service."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMBINED_DIR = REPO_ROOT / "workflows" / "combined"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class InsightServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if str(COMBINED_DIR) not in sys.path:
            sys.path.insert(0, str(COMBINED_DIR))
        cls.insight = _load_module(
            "combined_insight_service_test",
            COMBINED_DIR / "insight_service.py",
        )

    def test_workload_characterization_from_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp)
            (profile_dir / "foo.champsimtrace.json").write_text(
                json.dumps(
                    {
                        "access_pattern_taxonomy": "pointer_chasing",
                        "memory_intensity": 0.4,
                        "page_crossing_rate": 0.2,
                        "scores": {
                            "streaming": 0.1,
                            "pointer_chasing": 0.5,
                            "irregular": 0.2,
                        },
                        "top_pc_delta_summary": [
                            {
                                "pc": "0x401000",
                                "load_count": 100,
                                "dominant_delta_hex": "0x40",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            text = self.insight.characterize_workloads(["foo.champsimtrace.xz"], profile_dir)
            self.assertIn("pointer_chasing", text)
            self.assertIn("Prefetcher bias", text)
            self.assertIn("Replacement bias", text)

    def test_miss_log_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            miss = tmp_path / "misses.txt"
            miss.write_text(
                "cache cpu pc address misses\n"
                "cpu0_L2C 0 0x402dc0 0x1000 50\n"
                "cpu0_L2C 0 0x402dc0 0x1040 30\n"
                "cpu0_L2C 0 0x403000 0x2000 20\n",
                encoding="utf-8",
            )
            runs = [{"name": "t.xz", "miss_log_path": str(miss), "baseline_miss_log_path": None}]
            text = self.insight.analyze_miss_logs(runs)
            self.assertIn("0x402dc0", text)
            self.assertIn("Ranked hypotheses", text)

    def test_build_insight_bundle(self):
        bundle = self.insight.build_insight_bundle(
            "test task",
            artifacts={
                "trace_results": {
                    "trace_1_name": "605.mcf_s-472B.champsimtrace.xz",
                }
            },
            token_budget=10000,
            profile_dir=REPO_ROOT / "workflows" / "combined" / "profiles",
        )
        self.assertIn("Workload characterization", bundle)
        self.assertIn("605.mcf_s-472B", bundle)


if __name__ == "__main__":
    unittest.main()
