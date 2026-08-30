#!/usr/bin/env python3
"""Generate openevolve-components/seeds/sberti_l2c.cc from DPC4 sBerti sources."""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "openevolve-components" / "seeds" / "sberti_l2c.cc"
BASE = "https://raw.githubusercontent.com/CMU-SAFARI/DPC4/main/submissions/sBerti/sberti"


def fetch(name: str) -> str:
    with urllib.request.urlopen(f"{BASE}/{name}") as resp:
        return resp.read().decode("utf-8")


def strip_includes_and_guards(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#ifndef") or stripped.startswith("#define __SBERTI"):
            continue
        if stripped.startswith("#endif"):
            continue
        if stripped.startswith("#include"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def extract_class(text: str) -> str:
    start = text.index("class sberti")
    end = text.rindex("};", start) + 2
    cls = text[start:end]
    cls = re.sub(
        r"class sberti\s*:\s*public champsim::modules::prefetcher\s*\{",
        "class SBertiCore {",
        cls,
        count=1,
    )
    cls = re.sub(r"[ \t]*using champsim::modules::prefetcher::prefetcher;\n", "", cls)
    cls = re.sub(
        r"(public:\n)",
        "public:\n"
        "  openevolve_prefetcher* host = nullptr;\n\n"
        "  explicit SBertiCore(openevolve_prefetcher* pf) : host(pf) {}\n\n",
        cls,
        count=1,
    )
    return cls


def extract_impl(text: str) -> str:
    start = text.index("void sberti::addRecentPrefetch")
    impl = text[start:]
    impl = impl.replace("intern_->prefetch_line(", "host->prefetch_line(")
    impl = impl.replace("intern_->", "host->intern_->")
    impl = impl.replace("sberti::", "SBertiCore::")
    return impl.rstrip()


def main() -> None:
    header = strip_includes_and_guards(fetch("sberti.h"))
    cc = strip_includes_and_guards(fetch("sberti.cc"))
    structs = header[: header.index("class sberti")].strip()
    cls = extract_class(header)
    impl = extract_impl(cc)

    text = f"""// sBerti (Smart Stride + Berti) L2C prefetcher seed for OpenEvolve combined workflow.
// Adapted from CMU-SAFARI DPC4 submission:
// https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/sBerti/sberti
//
// Pair with fixed LRU replacement via scripts/seed_combined_checkpoint.py.

#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <memory>

#include "cache.h"
#include "champsim.h"
#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

{structs}

{cls}

{impl}

namespace {{

std::unique_ptr<SBertiCore> g_sberti;

}} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{{
  g_sberti = std::make_unique<SBertiCore>(this);
  g_sberti->prefetcher_initialize();
}}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit,
                                                         bool useful_prefetch, access_type type, uint32_t metadata_in)
{{
  return g_sberti->prefetcher_cache_operate(addr, ip, cache_hit, useful_prefetch, type, metadata_in);
}}

uint32_t openevolve_prefetcher::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch,
                                                      champsim::address evicted_addr, uint32_t metadata_in)
{{
  return g_sberti->prefetcher_cache_fill(addr, set, way, prefetch, evicted_addr, metadata_in);
}}

void openevolve_prefetcher::prefetcher_cycle_operate()
{{
  g_sberti->prefetcher_cycle_operate();
}}

// EVOLVE-BLOCK-END
"""

    OUT.write_text(text)
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
