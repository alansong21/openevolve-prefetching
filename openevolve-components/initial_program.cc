// Simple next-line prefetcher for ChampSim/OpenEvolve.
// Paste this block into initial_program.cc to revert to the baseline behavior.

#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

void openevolve_prefetcher::prefetcher_initialize() {
  // Initialize a small table to track recent access patterns
  for (auto& entry : recent_accesses) {
    entry.valid = false;
    entry.last_block = 0;
    entry.stride = 0;
  }
}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch,
                                                         access_type type, uint32_t metadata_in)
{
  (void)ip;
  (void)cache_hit;
  (void)useful_prefetch;

  if (type != access_type::LOAD)
    return metadata_in;

  champsim::block_number current_block{addr};

  // Check for a matching entry in the recent access table
  for (auto& entry : recent_accesses) {
    if (entry.valid && entry.last_block == current_block) {
      champsim::block_number next_block{current_block + entry.stride};
      prefetch_line(champsim::address{next_block}, true, metadata_in);
      entry.last_block = current_block;
      return metadata_in;
    }
  }

  // No matching entry, update the table with a new stride
  for (auto& entry : recent_accesses) {
    if (!entry.valid) {
      entry.valid = true;
      entry.last_block = current_block;
      entry.stride = 1;
      break;
    }
  }
  return metadata_in;
}

uint32_t openevolve_prefetcher::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr,
                                                      uint32_t metadata_in)
{
  (void)addr;
  (void)set;
  (void)way;
  (void)prefetch;
  (void)evicted_addr;

  return metadata_in;
}

void openevolve_prefetcher::prefetcher_cycle_operate() {}

// EVOLVE-BLOCK-END
