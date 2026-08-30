#!/usr/bin/env python3
"""Generate openevolve-components/seeds/anelin_l2c.cc from DPC4 ANeLin sources."""

from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "openevolve-components" / "seeds" / "anelin_l2c.cc"
BASE = "https://raw.githubusercontent.com/CMU-SAFARI/DPC4/main/submissions/BertiGO/ANeLin"


def fetch(name: str) -> str:
    with urllib.request.urlopen(f"{BASE}/{name}") as resp:
        return resp.read().decode("utf-8")


def main() -> None:
    anelin_cc = fetch("ANeLin.cc")

    # Strip upstream includes / interface; keep anelin namespace body.
    start = anelin_cc.index("namespace anelin {")
    end = anelin_cc.index("// INTERFACE")
    body = anelin_cc[start:end].rstrip()
    body += "\n\n} // namespace anelin\n"

    # Upstream markdown/raw fetch sometimes drops angle brackets.
    body = body.replace("std::vector global(NUM_CPUS);", "std::vector<PrefetchStats> global(NUM_CPUS);")
    body = body.replace("std::vector prefetches(NUM_CPUS);", "std::vector<PrefetchStats> prefetches(NUM_CPUS);")

    mycache_raw = fetch("MyCache.h")
    mycache_lines = [
        ln
        for ln in mycache_raw.splitlines()
        if not ln.strip().startswith("#include")
    ]
    mycache_body = "\n".join(mycache_lines)
    mycache_body = mycache_body.replace(
        "template <typename ENTRY, size_t NUM_SET_BITS, size_t NUM_WAYS, size_t TAG_BITS = 58, bool DRRIP = false>",
        "",
        1,
    )
    mycache = (
        "template <typename ENTRY, size_t NUM_SET_BITS, size_t NUM_WAYS, size_t TAG_BITS, bool DRRIP = true>\n"
        + mycache_body.strip()
        + "\n"
    )

    core_methods = """
class ANeLinCore {
  openevolve_prefetcher* host = nullptr;

public:
  explicit ANeLinCore(openevolve_prefetcher* pf) : host(pf) {}

  void prefetcher_initialize()
  {
    std::cout << "Adaptive Next Line" << std::endl;
    anelin::init_ongoing_table();
    anelin::print_size();
  }

  uint32_t prefetcher_cache_operate(champsim::address addr_, champsim::address ip_, uint8_t cache_hit, bool useful_prefetch,
                                    access_type type, uint32_t metadata_in)
  {
    uint64_t addr = addr_.to<uint64_t>();
    uint64_t line_addr = addr >> LOG2_BLOCK_SIZE;
    uint64_t ip = ip_.to<uint64_t>();
    size_t cpu = host->intern_->cpu;
    anelin::current_cycle = host->intern_->current_time.time_since_epoch() / host->intern_->clock_period;
    anelin::warmup = host->intern_->warmup;

    if (!cache_hit || useful_prefetch) {
      uint64_t pf_addr = (line_addr + 1) << LOG2_BLOCK_SIZE;
      if (anelin::isIPEnabled(cpu, ip) && anelin::get_active_cores() <= 1) {
        host->prefetch_line(champsim::address{pf_addr}, true, metadata_in);
      }
      anelin::add_ongoing_entry(pf_addr >> LOG2_BLOCK_SIZE, false);
      size_t set = (pf_addr >> LOG2_BLOCK_SIZE) % ((1 << SAMPLE_CACHE_SET_BITS_1C) * NUM_CPUS);
      if (set < SAMPLE_CACHE_NUM_SETS_USED) {
        anelin::SampleCacheEntry entry{pf_addr >> LOG2_BLOCK_SIZE, anelin::PREFETCHED, ip & IP_MASK, static_cast<uint8_t>(cpu)};
        auto evicted_entry = (NUM_CPUS == 1) ? anelin::sample_cache_1c.insert(entry, static_cast<uint32_t>(cpu))
                                             : anelin::sample_cache_4c.insert(entry, static_cast<uint32_t>(cpu));
        if (evicted_entry.tag)
          anelin::countStats(evicted_entry.status, evicted_entry.ip, evicted_entry.cpu);
      }
    }

    size_t set = line_addr % ((1 << SAMPLE_CACHE_SET_BITS_1C) * NUM_CPUS);
    if (set < SAMPLE_CACHE_NUM_SETS_USED) {
      auto entry = (NUM_CPUS == 1) ? anelin::sample_cache_1c.find(line_addr) : anelin::sample_cache_4c.find(line_addr);
      if (entry) {
        if (entry->status == anelin::PREFETCHED) {
          if (cache_hit && !useful_prefetch) {
            entry->status = anelin::DEMANDED;
          } else if (!cache_hit || (cache_hit && useful_prefetch)) {
            if (!anelin::is_ongoing_request(line_addr) || anelin::get_latency_ongoing(line_addr) > anelin::mean_dram_latency) {
              entry->status = anelin::TIMELY;
            } else if (anelin::get_latency_ongoing(line_addr) > anelin::mean_dram_latency * 0.2) {
              entry->status = anelin::LATE;
            } else {
              entry->status = anelin::DEMANDED;
            }
          } else {
            assert(false);
          }
        }
      }
    }

    if (!cache_hit) {
      anelin::add_ongoing_entry(line_addr, true);
    }

    if (set < SAMPLE_CACHE_NUM_SETS_USED) {
      anelin::SampleCacheEntry new_entry{line_addr, anelin::DEMANDED, ip & IP_MASK};
      auto evicted_entry = (NUM_CPUS == 1) ? anelin::sample_cache_1c.insert(new_entry, static_cast<uint32_t>(cpu))
                                           : anelin::sample_cache_4c.insert(new_entry, static_cast<uint32_t>(cpu));
      if (evicted_entry.tag)
        anelin::countStats(evicted_entry.status, evicted_entry.ip, evicted_entry.cpu);
    }

    return metadata_in;
  }

  uint32_t prefetcher_cache_fill(champsim::address addr_, long set, long way, uint8_t prefetch, champsim::address evicted_addr_,
                                 uint32_t metadata_in)
  {
    (void)set;
    (void)way;
    (void)prefetch;
    (void)evicted_addr_;

    uint64_t addr = addr_.to<uint64_t>();
    uint64_t line_addr = (addr >> LOG2_BLOCK_SIZE);
    anelin::current_cycle = host->intern_->current_time.time_since_epoch() / host->intern_->clock_period;

    if (addr) {
      uint64_t latency = anelin::get_latency_ongoing(line_addr);
      if (latency > 10 && anelin::is_ongoing_demand_request(line_addr)) {
        anelin::mean_dram_latency = anelin::mean_dram_latency * 0.95 + latency * 0.05;
      }
      anelin::invalid_ongoing_entry(line_addr);
    }

    if (++anelin::fills >= anelin::RESET_INTERVAL) {
      for (int i = 0; i < 4; i++)
        anelin::cpu_seen[i] = 0;
      anelin::fills = 0;
    }

    auto cpu = host->intern_->cpu;
    if (cpu < 4)
      anelin::cpu_seen[cpu] = 1;

    int cores_active = anelin::cpu_seen[0] + anelin::cpu_seen[1] + anelin::cpu_seen[2] + anelin::cpu_seen[3];
    if (cores_active > 1) {
      return anelin::MULTICORE_SIGNAL;
    }

    return metadata_in;
  }

  void prefetcher_cycle_operate() {}
};
"""

    text = f"""// ANeLin L2C prefetcher seed for OpenEvolve combined workflow.
// Adapted from CMU-SAFARI DPC4 submission:
// https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/BertiGO/ANeLin
//
// Pair with fixed LRU replacement via scripts/seed_combined_checkpoint.py.

#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <vector>

#include "cache.h"
#include "champsim.h"
#include "dpc_api.h"
#include "msl/fwcounter.h"
#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

{mycache}

{body}

{core_methods}

namespace {{

std::unique_ptr<ANeLinCore> g_anelin;

}} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{{
  g_anelin = std::make_unique<ANeLinCore>(this);
  g_anelin->prefetcher_initialize();
}}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit,
                                                         bool useful_prefetch, access_type type, uint32_t metadata_in)
{{
  return g_anelin->prefetcher_cache_operate(addr, ip, cache_hit, useful_prefetch, type, metadata_in);
}}

uint32_t openevolve_prefetcher::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch,
                                                      champsim::address evicted_addr, uint32_t metadata_in)
{{
  return g_anelin->prefetcher_cache_fill(addr, set, way, prefetch, evicted_addr, metadata_in);
}}

void openevolve_prefetcher::prefetcher_cycle_operate()
{{
  g_anelin->prefetcher_cycle_operate();
}}

// EVOLVE-BLOCK-END
"""

    OUT.write_text(text)
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
