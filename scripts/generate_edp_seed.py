#!/usr/bin/env python3
"""Generate openevolve-components/seeds/edp_l2c.cc from DPC4 EDP sources."""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "openevolve-components" / "seeds" / "edp_l2c.cc"
BASE = "https://raw.githubusercontent.com/CMU-SAFARI/DPC4/main/submissions/EDP/edp"


def fetch(name: str) -> str:
    with urllib.request.urlopen(f"{BASE}/{name}") as resp:
        return resp.read().decode("utf-8")


def strip_header(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#ifndef") or stripped.startswith("#define") and "_H" in stripped:
            continue
        if stripped.startswith("#endif"):
            continue
        if stripped.startswith("#include"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def fix_num_cpus(text: str) -> str:
    text = "constexpr std::size_t EDP_MAX_CPUS = 16;\n\n" + text
    text = text.replace(", NUM_CPUS>", ", EDP_MAX_CPUS>")
    text = text.replace("NUM_CPUS>", "EDP_MAX_CPUS>")
    text = text.replace("j < NUM_CPUS", "j < EDP_MAX_CPUS")
    text = text.replace("{NUM_CPUS,", "{EDP_MAX_CPUS,")
    return text


def transform_impl_body(body: str) -> str:
    body = re.sub(r"^#define NUM_CPUS 16\s*\n", "", body, flags=re.MULTILINE)
    body = body.replace("edp *cache", "EDPCore *core")
    body = body.replace("trigger_Issues_per_ip(ipTag, cache, cpu)", "trigger_Issues_per_ip(ipTag, core, cpu)")
    return fix_num_cpus(body)


def transform_methods(methods: str) -> tuple[str, str]:
    decls = """
  void prefetcher_initialize();
  uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit,
                                    bool useful_prefetch, access_type type, uint32_t metadata_in);
  uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch,
                                 champsim::address evicted_addr, uint32_t metadata_in);
  void prefetcher_cycle_operate();
"""
    methods = methods.replace("void edp::", "void EDPCore::")
    methods = methods.replace("uint32_t edp::", "uint32_t EDPCore::")
    methods = re.sub(r"\bintern_->", "host->intern_->", methods)
    methods = methods.replace("prefetch_line(", "host->prefetch_line(")
    methods = methods.replace(
        "[this](auto &i) { i.resize(host->intern_->NUM_WAY); }",
        "[this](auto &i) { i.resize(this->host->intern_->NUM_WAY); }",
    )
    methods = methods.replace("train(blockAddr, cycle - latency, ipTag, addrTag, cycle, this, cpu)",
                              "train(blockAddr, cycle - latency, ipTag, addrTag, cycle, this, cpu)")
    methods = methods.replace("trigger(this, ipTag, cycle, cpu)", "trigger(this, ipTag, cycle, cpu)")
    methods = re.sub(
        r"\nvoid EDPCore::prefetcher_final_stats\(\) \{.*?\n\}\n",
        "\n",
        methods,
        flags=re.DOTALL,
    )
    return decls.strip(), methods


def main() -> None:
    edp_cc = fetch("edp.cc")
    cache_struct = strip_header(fetch("cacheStruct.hh"))
    bloom_filter = strip_header(fetch("filter.h"))

    impl_start = edp_cc.index("// Lat Addr")
    impl_end = edp_cc.index("void edp::prefetcher_initialize()")
    impl_body = transform_impl_body(edp_cc[impl_start:impl_end].rstrip())
    method_decls, methods_src = transform_methods(edp_cc[impl_end:])

    text = f"""// EDP (Entangling Data Prefetcher) seed for OpenEvolve combined workflow.
// Adapted from CMU-SAFARI DPC4 submission:
// https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/EDP/edp
//
// Pair with fixed LRU replacement via scripts/seed_combined_checkpoint.py.

#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <deque>
#include <iostream>
#include <map>
#include <memory>
#include <optional>
#include <queue>
#include <set>
#include <string>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "cache.h"
#include "champsim.h"
#include "dpc_api.h"
#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

{cache_struct}

{bloom_filter}

class EDPCore;

{impl_body}

class EDPCore {{
  openevolve_prefetcher* host = nullptr;

public:
  explicit EDPCore(openevolve_prefetcher* pf) : host(pf) {{}}
{method_decls}
}};

{methods_src}

namespace {{

std::unique_ptr<EDPCore> g_edp;

}} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{{
  g_edp = std::make_unique<EDPCore>(this);
  g_edp->prefetcher_initialize();
}}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit,
                                                         bool useful_prefetch, access_type type, uint32_t metadata_in)
{{
  return g_edp->prefetcher_cache_operate(addr, ip, cache_hit, useful_prefetch, type, metadata_in);
}}

uint32_t openevolve_prefetcher::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch,
                                                      champsim::address evicted_addr, uint32_t metadata_in)
{{
  return g_edp->prefetcher_cache_fill(addr, set, way, prefetch, evicted_addr, metadata_in);
}}

void openevolve_prefetcher::prefetcher_cycle_operate()
{{
  g_edp->prefetcher_cycle_operate();
}}

// EVOLVE-BLOCK-END
"""

    OUT.write_text(text)
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
