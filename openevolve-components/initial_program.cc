// Simple next-line prefetcher for ChampSim/OpenEvolve.
// Paste this block into initial_program.cc to revert to the baseline behavior.

#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

#include <unordered_map> // Use unordered map for faster access

struct StrideEntry {
  champsim::block_number last_block;
  int64_t primary_stride;
  int64_t secondary_stride; // Add secondary stride for mixed pattern detection
  int confidence; // Add confidence to track accuracy
};

std::unordered_map<uint64_t, StrideEntry> stride_table; // Change to unordered map for performance

void openevolve_prefetcher::prefetcher_initialize() {
  stride_table.clear();
}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch,
                                                         access_type type, uint32_t metadata_in)
{


  uint64_t ip_val = ip.to<uint64_t>();
  champsim::block_number current_block{addr};
  int64_t current = current_block.to<int64_t>();
  bool prefetch_issued = false;

  auto it = stride_table.find(ip_val);
  if (it != stride_table.end()) {
    int64_t last = it->second.last_block.to<int64_t>();
    int64_t stride = current - last;

    if ((stride == it->second.primary_stride || stride == it->second.secondary_stride) && it->second.confidence > 1) {
      int prefetch_distance = std::min(3, it->second.confidence); // Limit prefetch distance to 3
      for (int i = 1; i <= prefetch_distance; ++i) {
        champsim::block_number next_block{current_block + stride * i};
        prefetch_issued |= prefetch_line(champsim::address{next_block}, true, metadata_in);
      }
      if (prefetch_issued) {
        it->second.confidence = std::min(it->second.confidence + 1, 5); // Increase confidence if prefetch was issued
      }
      // Update confidence based on usefulness
      if (useful_prefetch && prefetch_issued) {
        it->second.confidence = std::min(it->second.confidence + 2, 5); // Increase confidence more aggressively if useful
      } else if (!useful_prefetch) {
        it->second.confidence = std::max(it->second.confidence - 2, 0); // Decrease confidence more aggressively if not useful
      }
    } else {
      // Track secondary stride
      if (stride != it->second.primary_stride) {
        it->second.secondary_stride = it->second.primary_stride;
        it->second.primary_stride = stride;
        it->second.confidence = 1; // Reset confidence on stride change
      }
    }
    it->second.last_block = current_block;
  } else {
    stride_table[ip_val] = StrideEntry{current_block, 0, 1};
  }

  if (type != access_type::LOAD || cache_hit || it->second.confidence < 4) // Increase confidence threshold for prefetching
    return metadata_in;


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

void openevolve_prefetcher::prefetcher_cycle_operate() {
  // Adaptive decay based on recent prefetch usefulness
  static int cycle_count = 0;
  cycle_count++;
  if (cycle_count % 1000 == 0) { // Decay confidence every 1000 cycles
    for (auto& entry : stride_table) {
      if (entry.second.confidence > 0) {
        entry.second.confidence = std::max(entry.second.confidence - 1, 0); // Periodic decay
      }
    }
  }
}

// EVOLVE-BLOCK-END
