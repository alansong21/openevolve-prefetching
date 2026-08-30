#!/usr/bin/env python3
"""Generate openevolve-components/seeds/sms2_l2c.cc from DPC4 SMS2 sources."""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "openevolve-components" / "seeds" / "sms2_l2c.cc"
BASE = "https://raw.githubusercontent.com/CMU-SAFARI/DPC4/main/submissions/uMAMA/umama/sms2"


def fetch(name: str) -> str:
    with urllib.request.urlopen(f"{BASE}/{name}") as resp:
        return resp.read().decode("utf-8")


def strip_includes_and_guards(text: str) -> str:
    lines: list[str] = []
    skipping_endif = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#ifndef") or stripped.startswith("#define __"):
            skipping_endif = True
            continue
        if stripped.startswith("#pragma once"):
            continue
        if stripped.startswith("#endif"):
            continue
        if stripped.startswith("#include"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def extract_class(text: str) -> str:
    start = text.index("struct sms2")
    end = text.rindex("};", start) + 2
    cls = text[start:end]
    cls = re.sub(
        r"struct sms2\s*:\s*public champsim::modules::prefetcher\s*\{",
        "struct Sms2Core {",
        cls,
        count=1,
    )
    cls = re.sub(r"[ \t]*using champsim::modules::prefetcher::prefetcher;\n", "", cls)
    cls = re.sub(
        r"BloomFilter<L2_BLOOM_N, L2_BLOOM_M>\s*\*bloom;",
        "BloomFilterStub* bloom = nullptr;",
        cls,
        count=1,
    )
    cls = re.sub(
        r"void set_bloom\(BloomFilter<L2_BLOOM_N, L2_BLOOM_M>\s*\*bloom_\)\s*\{[^}]*\}",
        "",
        cls,
        count=1,
    )
    cls = re.sub(
        r"(public:\n)",
        "public:\n"
        " openevolve_prefetcher* host = nullptr;\n\n"
        " explicit Sms2Core(openevolve_prefetcher* pf) : host(pf) {}\n\n",
        cls,
        count=1,
    )
    return cls


def transform_impl(text: str) -> str:
    impl = strip_includes_and_guards(text)
    impl = re.sub(r"(?<!host->)prefetch_line\(", "host->prefetch_line(", impl)
    impl = impl.replace("intern_->", "host->intern_->")
    impl = impl.replace("sms2::", "Sms2Core::")
    impl = impl.replace("find_if(", "std::find_if(")
    return impl.rstrip()


def main() -> None:
    bitmap_h = strip_includes_and_guards(fetch("bitmap2.h"))
    bitmap_cc = transform_impl(fetch("bitmap2.cc"))
    helper_h = strip_includes_and_guards(fetch("sms2_helper.h"))
    header = strip_includes_and_guards(fetch("sms2.h"))
    cls = extract_class(header)
    sms_cc = transform_impl(fetch("sms2.cc"))
    sms_aux = transform_impl(fetch("sms2_aux.cc"))

    text = f"""// SMS2 (Spatial Memory Streaming) L2C prefetcher seed for OpenEvolve combined workflow.
// Adapted from CMU-SAFARI DPC4 submission:
// https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/uMAMA/umama/sms2
//
// Pair with fixed LRU replacement via scripts/seed_combined_checkpoint.py.

#include <algorithm>
#include <bitset>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <deque>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "cache.h"
#include "champsim.h"
#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

struct BloomFilterStub {{
  bool test(uint64_t) const {{ return false; }}
}};

{bitmap_h}

{helper_h}

{cls}

{bitmap_cc}

{sms_cc}

{sms_aux}

namespace {{

std::unique_ptr<Sms2Core> g_sms2;

}} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{{
  g_sms2 = std::make_unique<Sms2Core>(this);
  g_sms2->prefetcher_initialize();
}}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit,
                                                         bool useful_prefetch, access_type type, uint32_t metadata_in)
{{
  return g_sms2->prefetcher_cache_operate(addr, ip, cache_hit, useful_prefetch, type, metadata_in);
}}

uint32_t openevolve_prefetcher::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch,
                                                      champsim::address evicted_addr, uint32_t metadata_in)
{{
  return g_sms2->prefetcher_cache_fill(addr, set, way, prefetch, evicted_addr, metadata_in);
}}

void openevolve_prefetcher::prefetcher_cycle_operate()
{{
  g_sms2->prefetcher_cycle_operate();
}}

// EVOLVE-BLOCK-END
"""

    OUT.write_text(text)
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
