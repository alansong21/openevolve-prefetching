#!/usr/bin/env python3
"""Generate openevolve-components/seeds/bingo_plus_l2c.cc from DPC4 bingo_plus sources."""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "openevolve-components" / "seeds" / "bingo_plus_l2c.cc"
BASE = "https://raw.githubusercontent.com/CMU-SAFARI/DPC4/main/submissions/SPPAM/bingo_plus"
PARAMS_BASE = "https://raw.githubusercontent.com/CMU-SAFARI/DPC4/main/submissions/SPPAM/berti_plus"


def fetch(url_base: str, name: str) -> str:
    with urllib.request.urlopen(f"{url_base}/{name}") as resp:
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
    return "\n".join(lines).strip()


def extract_support_header(text: str) -> str:
    start = text.index("namespace Bingo_plus")
    end = text.index("class bingo_plus")
    return text[start:end].strip()


def extract_core_class(text: str) -> str:
    start = text.index("class bingo_plus")
    end = text.rindex("};", start) + 2
    cls = text[start:end]
    cls = cls.replace(
        "class bingo_plus : public champsim::modules::prefetcher {",
        "class BingoPlusCore {",
    )
    cls = cls.replace("        using prefetcher::prefetcher;\n", "")
    cls = cls.replace(
        "        void prefetcher_initialize();",
        "    openevolve_prefetcher* host = nullptr;\n\n"
        "    explicit BingoPlusCore(openevolve_prefetcher* pf) : host(pf) {}\n\n"
        "    void prefetcher_initialize();",
    )
    cls = re.sub(r"\n        void prefetcher_final_stats\(\);\n", "\n", cls)
    return cls


def extract_support_impl(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#include"):
            continue
        if stripped == "using namespace Bingo_plus;":
            continue
        out.append(line)
    body = "\n".join(out).strip()
    start = body.index("uint64_t hash_index_plus")
    end = body.index("void bingo_plus::prefetcher_initialize()")
    return body[start:end].strip()


def extract_core_impl(text: str) -> str:
    start = text.index("void bingo_plus::prefetcher_initialize()")
    impl = text[start:]
    impl = impl.replace("bingo_plus::", "BingoPlusCore::")
    impl = re.sub(r"\bintern_->", "host->intern_->", impl)
    impl = impl.replace("prefetchers.emplace_back(host->intern_,this,i)", "prefetchers.emplace_back(host->intern_, host, i)")
    impl = impl.replace("prefetchers.emplace_back(intern_,this,i)", "prefetchers.emplace_back(host->intern_, host, i)")
    impl = re.sub(
        r"    uint64_t bingo_state = .*?\n    auto bingo_bytes = .*?\n",
        "",
        impl,
        count=1,
    )
    impl = re.sub(
        r"    fmt::print\([^\n]*\n",
        '    std::cout << "Bingo+ Prefetcher initialized" << std::endl;\n',
        impl,
        count=1,
    )
    impl = re.sub(
        r"\nvoid BingoPlusCore::prefetcher_final_stats\(\) \{.*?\n\}\n",
        "\n",
        impl,
        flags=re.DOTALL,
    )
    return impl.strip()


def main() -> None:
    params = extract_parameters(fetch(PARAMS_BASE, "berti_plus_parameters.h"))
    header = fetch(BASE, "bingo_plus.h")
    cc = fetch(BASE, "bingo_plus.cc")
    support_header = extract_support_header(header)
    core_class = extract_core_class(header)
    support_impl = extract_support_impl(cc)
    core_impl = extract_core_impl(cc)

    text = f"""// Bingo+ L2C prefetcher seed for OpenEvolve combined workflow.
// Adapted from CMU-SAFARI DPC4 submission:
// https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/SPPAM/bingo_plus
//
// Pair with fixed LRU replacement via scripts/seed_combined_checkpoint.py.

#include <algorithm>
#include <bitset>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

#include "cache.h"
#include "champsim.h"
#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

{params}

{support_header}

{core_class}

using namespace Bingo_plus;

{support_impl}

{core_impl}

namespace {{

std::unique_ptr<BingoPlusCore> g_bingo_plus;

}} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{{
  g_bingo_plus = std::make_unique<BingoPlusCore>(this);
  g_bingo_plus->prefetcher_initialize();
}}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit,
                                                         bool useful_prefetch, access_type type, uint32_t metadata_in)
{{
  return g_bingo_plus->prefetcher_cache_operate(addr, ip, cache_hit != 0, useful_prefetch, type, metadata_in);
}}

uint32_t openevolve_prefetcher::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch,
                                                      champsim::address evicted_addr, uint32_t metadata_in)
{{
  return g_bingo_plus->prefetcher_cache_fill(addr, set, way, prefetch != 0, evicted_addr, metadata_in);
}}

void openevolve_prefetcher::prefetcher_cycle_operate()
{{
  g_bingo_plus->prefetcher_cycle_operate();
}}

// EVOLVE-BLOCK-END
"""

    OUT.write_text(text)
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
