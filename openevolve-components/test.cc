// Simple next-line prefetcher for ChampSim/OpenEvolve.
// Paste this block into initial_program.cc to revert to the baseline behavior.

#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

namespace {
static const int IP_TABLE_SIZE = 64;
static champsim::address last_ip[IP_TABLE_SIZE];
static champsim::address last_addr[IP_TABLE_SIZE];
static long long last_stride[IP_TABLE_SIZE];
}

void openevolve_prefetcher::prefetcher_initialize() {
  for (int i = 0; i < IP_TABLE_SIZE; i++) {
    last_ip[i] = champsim::address{0};
    last_addr[i] = champsim::address{0};
    last_stride[i] = 0;
  }
}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch,
                                                         access_type type, uint32_t metadata_in)
{
  (void)cache_hit;
  (void)useful_prefetch;

  if (type != access_type::LOAD)
    return metadata_in;

  champsim::block_number current_block{addr};

  auto index = static_cast<uint32_t>(ip.to<uint64_t>() & 0xffffffff) % IP_TABLE_SIZE;
  if (last_ip[index] == ip) {
    long long stride = static_cast<long long>(offset(champsim::block_number{last_addr[index]}, current_block));
    if (stride != 0) {
      champsim::block_number next_block{current_block + stride};
      prefetch_line(champsim::address{next_block}, true, metadata_in);
    }
    last_stride[index] = stride;
    last_addr[index] = addr;
  } else {
    last_ip[index] = ip;
    last_addr[index] = addr;
    last_stride[index] = 0;
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
