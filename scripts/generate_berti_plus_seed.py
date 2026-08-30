#!/usr/bin/env python3
"""Generate openevolve-components/seeds/berti_plus_l2c.cc from DPC4 berti_plus sources."""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "openevolve-components" / "seeds" / "berti_plus_l2c.cc"
BASE = "https://raw.githubusercontent.com/CMU-SAFARI/DPC4/main/submissions/SPPAM/berti_plus"


def fetch(name: str) -> str:
    with urllib.request.urlopen(f"{BASE}/{name}") as resp:
        return resp.read().decode("utf-8")


def extract_parameters(text: str) -> str:
    match = re.search(r"namespace berti_plus_space\s*\{(.*)\};", text, re.DOTALL)
    if not match:
        raise RuntimeError("Could not parse berti_plus_parameters.h")
    lines: list[str] = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            lines.append(re.sub(r"^#\s*define", "#define", stripped))
        elif stripped.startswith("/*") or stripped.startswith("*") or stripped.startswith("//"):
            lines.append(line.rstrip())
    return "\n".join(lines).strip()


def extract_class(text: str) -> str:
    start = text.index("class berti_plus")
    end = text.rindex("};", start) + 2
    cls = text[start:end]
    cls = cls.replace(
        "class berti_plus : public champsim::modules::prefetcher {",
        "class BertiPlusCore {",
    )
    cls = cls.replace("    using prefetcher::prefetcher;\n", "")
    cls = cls.replace(
        "    void prefetcher_initialize();",
        "    openevolve_prefetcher* host = nullptr;\n\n"
        "    explicit BertiPlusCore(openevolve_prefetcher* pf) : host(pf) {}\n\n"
        "    void prefetcher_initialize();",
    )
    cls = re.sub(r"\n    void prefetcher_final_stats\(\);\n", "\n", cls)
    return cls


def extract_impl(text: str) -> str:
    start = text.index("uint8_t berti_plus::LatencyTable::add")
    impl = text[start:]
    impl = impl.replace("berti_plus::", "BertiPlusCore::")
    impl = re.sub(r"\bintern_->", "host->intern_->", impl)
    impl = impl.replace("prefetch_line(", "host->prefetch_line(")
    impl = impl.replace(
        "host->intern_->current_cycle()",
        "(host->intern_->current_time.time_since_epoch() / host->intern_->clock_period)",
    )
    impl = re.sub(
        r"  uint64_t additional_state = .*?\n  uint64_t vanilla_state = .*?\n  auto total_state = .*?\n  fmt::print\([^\n]*\n",
        "  std::cout << \"Berti+ Prefetcher initialized\" << std::endl;\n",
        impl,
        count=1,
    )
    impl = re.sub(
        r"\nvoid BertiPlusCore::prefetcher_final_stats\(\).*?\n\}\n?\s*$",
        "\n",
        impl,
        flags=re.DOTALL,
    )
    return impl.rstrip()


def main() -> None:
    params = extract_parameters(fetch("berti_plus_parameters.h"))
    header = fetch("berti_plus.h")
    impl = extract_impl(fetch("berti_plus.cc"))
    cls = extract_class(header)

    text = f"""// Berti+ L2C prefetcher seed for OpenEvolve combined workflow.
// Adapted from CMU-SAFARI DPC4 submission:
// https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/SPPAM/berti_plus
//
// Pair with fixed LRU replacement via scripts/seed_combined_checkpoint.py.

#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <map>
#include <memory>
#include <optional>
#include <queue>
#include <tuple>
#include <vector>

#include "cache.h"
#include "champsim.h"
#include "msl/lru_table.h"
#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

{params}

{cls}

{impl}

namespace {{

std::unique_ptr<BertiPlusCore> g_berti_plus;

}} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{{
  g_berti_plus = std::make_unique<BertiPlusCore>(this);
  g_berti_plus->prefetcher_initialize();
}}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit,
                                                         bool useful_prefetch, access_type type, uint32_t metadata_in)
{{
  return g_berti_plus->prefetcher_cache_operate(addr, ip, cache_hit != 0, useful_prefetch, type, metadata_in);
}}

uint32_t openevolve_prefetcher::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch,
                                                      champsim::address evicted_addr, uint32_t metadata_in)
{{
  return g_berti_plus->prefetcher_cache_fill(addr, set, way, prefetch != 0, evicted_addr, metadata_in);
}}

void openevolve_prefetcher::prefetcher_cycle_operate()
{{
  g_berti_plus->prefetcher_cycle_operate();
}}

// EVOLVE-BLOCK-END
"""

    OUT.write_text(text)
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
