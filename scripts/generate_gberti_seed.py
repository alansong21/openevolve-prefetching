#!/usr/bin/env python3
"""Generate openevolve-components/seeds/gberti_l2c.cc from DPC4 gBerti sources."""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "openevolve-components" / "seeds" / "gberti_l2c.cc"
BASE = "https://raw.githubusercontent.com/CMU-SAFARI/DPC4/main/submissions/gBerti/gberti"


def fetch(name: str) -> str:
    with urllib.request.urlopen(f"{BASE}/{name}") as resp:
        return resp.read().decode("utf-8")


def strip_includes_and_guards(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#ifndef") or stripped.startswith("#define __GBERTI"):
            continue
        if stripped.startswith("#endif"):
            continue
        if stripped.startswith("#include"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def extract_class(text: str) -> str:
    start = text.index("struct gberti")
    end = text.rindex("};", start) + 2
    cls = text[start:end]
    cls = re.sub(
        r"struct gberti\s*:\s*public champsim::modules::prefetcher\s*\{",
        "struct GBertiCore {",
        cls,
        count=1,
    )
    cls = re.sub(r"[ \t]*using champsim::modules::prefetcher::prefetcher;\n", "", cls)
    cls = re.sub(
        r"(public:\n)",
        "public:\n"
        " openevolve_prefetcher* host = nullptr;\n\n"
        " explicit GBertiCore(openevolve_prefetcher* pf) : host(pf) {}\n\n",
        cls,
        count=1,
    )
    return cls


def extract_impl(text: str) -> str:
    markers = ["#define DEBUG", "void gberti::prefetcher_initialize()"]
    start = min(text.index(marker) for marker in markers if marker in text)
    impl = text[start:]
    impl = impl.replace("intern_->prefetch_line(", "host->prefetch_line(")
    impl = impl.replace("intern_->", "host->intern_->")
    impl = impl.replace("gberti::", "GBertiCore::")
    return impl.rstrip()


def main() -> None:
    header = strip_includes_and_guards(fetch("gberti.h"))
    cc = strip_includes_and_guards(fetch("gberti.cc"))
    structs = header[: header.index("struct gberti")].strip()
    cls = extract_class(header)
    impl = extract_impl(cc)

    text = f"""// gBerti (Global Berti) L2C prefetcher seed for OpenEvolve combined workflow.
// Adapted from CMU-SAFARI DPC4 submission:
// https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/gBerti/gberti
//
// Pair with fixed LRU replacement via scripts/seed_combined_checkpoint.py.

#include <algorithm>
#include <cstdint>
#include <memory>
#include <queue>
#include <tuple>
#include <unordered_map>
#include <vector>

#include "cache.h"
#include "champsim.h"
#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

{structs}

{cls}

{impl}

namespace {{

std::unique_ptr<GBertiCore> g_gberti;

}} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{{
  g_gberti = std::make_unique<GBertiCore>(this);
  g_gberti->prefetcher_initialize();
}}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit,
                                                         bool useful_prefetch, access_type type, uint32_t metadata_in)
{{
  return g_gberti->prefetcher_cache_operate(addr, ip, cache_hit, useful_prefetch, type, metadata_in);
}}

uint32_t openevolve_prefetcher::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch,
                                                      champsim::address evicted_addr, uint32_t metadata_in)
{{
  return g_gberti->prefetcher_cache_fill(addr, set, way, prefetch, evicted_addr, metadata_in);
}}

void openevolve_prefetcher::prefetcher_cycle_operate()
{{
  g_gberti->prefetcher_cycle_operate();
}}

// EVOLVE-BLOCK-END
"""

    OUT.write_text(text)
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
