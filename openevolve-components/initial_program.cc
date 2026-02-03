// Simple next-line prefetcher for ChampSim/OpenEvolve.
// Paste this block into initial_program.cc to revert to the baseline behavior.

#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

namespace {
constexpr std::size_t STRIDE_TABLE_SIZE = 64;

struct StrideEntry {
  champsim::address ip{};
  champsim::block_number last_block{};
  int64_t stride{0};
  bool valid{false};
};

StrideEntry stride_table[STRIDE_TABLE_SIZE];
} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{
  for (auto& e : stride_table) {
    e.valid = false;
    e.stride = 0;
    e.last_block = champsim::block_number{0};
    e.ip = champsim::address{0};
  }
}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch,
                                                         access_type type, uint32_t metadata_in)
{
  (void)cache_hit;
  (void)useful_prefetch;

  // Only prefetch on load accesses
  if (type != access_type::LOAD)
    return metadata_in;

  champsim::block_number current_block{addr};

  // Simple PC-indexed stride table
  std::size_t index = ip.to<uint64_t>() % STRIDE_TABLE_SIZE;
  auto& entry = stride_table[index];

  if (!entry.valid || entry.ip != ip) {
    // Initialize a new entry
    entry.valid = true;
    entry.ip = ip;
    entry.stride = 0;
    entry.last_block = current_block;
  } else {
    int64_t delta = static_cast<int64_t>(current_block.to<uint64_t>()) -
                    static_cast<int64_t>(entry.last_block.to<uint64_t>());

    if (delta != 0) {
      if (delta == entry.stride) {
        // Consistent stride detected, issue up to two lookahead prefetches
        for (int lookahead = 1; lookahead <= 2; ++lookahead) {
          champsim::block_number pf_block = current_block + (entry.stride * lookahead);
          prefetch_line(champsim::address{pf_block}, true, metadata_in);
        }
      } else {
        // Update stride if it changes
        entry.stride = delta;
      }
      entry.last_block = current_block;
    }
  }

  // Fallback next-line prefetch until stride is determined
  if (entry.stride == 0) {
    champsim::block_number next_block{current_block + 1};
    prefetch_line(champsim::address{next_block}, true, metadata_in);
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
