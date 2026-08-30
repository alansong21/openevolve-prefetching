#!/usr/bin/env python3
"""Generate openevolve-components/seeds/emender_l2c.cc from DPC4 Emender L2 sources.

Emender L2 is a modified Pythia (custom_pythia) that tracks useless-prefetch
rate and optional L3 throttle metadata. Helper types come from this repo's
ChampSim Pythia module so the seed links against the already-built Pythia
objects.
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "openevolve-components" / "seeds" / "emender_l2c.cc"
BASE = "https://raw.githubusercontent.com/CMU-SAFARI/DPC4/main/submissions/Emender/emender_l2"
PYTHIA_DIR = ROOT / "ChampSim" / "prefetcher" / "pythia"
# Relative to openevolve-components/initial_program.cc (the compile-time include).
PYTHIA_INC = "../ChampSim/prefetcher/pythia"
PYTHIA_HEADERS = [
    "pythia_params.h",
    "pythia_helper.h",
    "learning_engine_base.h",
    "feature_knowledge.h",
    "learning_engine_featurewise.h",
]


def fetch(name: str) -> str:
    with urllib.request.urlopen(f"{BASE}/{name}") as resp:
        return resp.read().decode("utf-8")


def extract_class(text: str) -> str:
    start = text.index("struct custom_pythia")
    end = text.index("};", start) + 2
    cls = text[start:end]
    cls = re.sub(
        r"struct custom_pythia\s*:\s*public champsim::modules::prefetcher\s*\{",
        "struct CustomPythia {",
        cls,
        count=1,
    )
    cls = re.sub(r"[ \t]*using champsim::modules::prefetcher::prefetcher;\n", "", cls)
    cls = re.sub(
        r"(public:\n)",
        "public:\n"
        " openevolve_prefetcher* host = nullptr;\n"
        " bool throttle = false;\n\n"
        " explicit CustomPythia(openevolve_prefetcher* pf) : host(pf) {}\n\n",
        cls,
        count=1,
    )
    cls = re.sub(r"\s*bool throttle;\n", "\n", cls, count=1)
    cls = re.sub(r"\n[ \t]*void prefetcher_final_stats\(\);\n", "\n", cls)
    return cls


def extract_impl(text: str) -> str:
    start = text.index("void custom_pythia::prefetcher_initialize()")
    end = text.index("void emender_l2::prefetcher_initialize()")
    impl = text[start:end]
    impl = re.sub(
        r"\nvoid custom_pythia::prefetcher_final_stats\(\) \{\}\n",
        "\n",
        impl,
    )
    impl = impl.replace("intern_->prefetch_line(", "host->prefetch_line(")
    impl = impl.replace("intern_->", "host->intern_->")
    impl = impl.replace("custom_pythia::", "CustomPythia::")
    impl = impl.replace("find_if(", "std::find_if(")
    return impl.rstrip()


def main() -> None:
    for name in PYTHIA_HEADERS:
        path = PYTHIA_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"Missing ChampSim Pythia header: {path}")

    cc = fetch("emender_l2.cc")
    cls = extract_class(cc)
    impl = extract_impl(cc)

    text = f"""// Emender L2C prefetcher seed for OpenEvolve combined workflow.
// Adapted from CMU-SAFARI DPC4 submission:
// https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/Emender/emender_l2
//
// Emender L2 is a modified Pythia (custom_pythia) with useless-prefetch
// tracking and optional L3 throttle metadata. Helper types come from
// this repo's ChampSim Pythia module (linked against existing Pythia objects).
//
// Pair with fixed LRU replacement via scripts/seed_combined_checkpoint.py.

#include <algorithm>
#include <bitset>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <deque>
#include <iostream>
#include <memory>
#include <random>
#include <string>
#include <unordered_set>
#include <vector>

#include "cache.h"
#include "champsim.h"
#include "dpc_api.h"
#include "openevolve_prefetcher.h"

#include "{PYTHIA_INC}/pythia_helper.h"
#include "{PYTHIA_INC}/learning_engine_featurewise.h"

// EVOLVE-BLOCK-START

#define ENABLE_L3_THROTTLE (1)

{cls}

{impl}

namespace {{

std::unique_ptr<CustomPythia> g_emender;

}} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{{
  g_emender = std::make_unique<CustomPythia>(this);
  g_emender->prefetcher_initialize();
}}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit,
                                                         bool useful_prefetch, access_type type, uint32_t metadata_in)
{{
  return g_emender->prefetcher_cache_operate(addr, ip, cache_hit, useful_prefetch, type, metadata_in);
}}

uint32_t openevolve_prefetcher::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch,
                                                      champsim::address evicted_addr, uint32_t metadata_in)
{{
  return g_emender->prefetcher_cache_fill(addr, set, way, prefetch, evicted_addr, metadata_in);
}}

void openevolve_prefetcher::prefetcher_cycle_operate()
{{
  g_emender->prefetcher_cycle_operate();
}}

// EVOLVE-BLOCK-END
"""

    OUT.write_text(text)
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
