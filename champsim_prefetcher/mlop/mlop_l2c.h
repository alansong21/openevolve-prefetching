#ifndef PREFETCHER_MLOP_L2C_H
#define PREFETCHER_MLOP_L2C_H

#include <cstdint>

#include "address.h"
#include "champsim.h"
#include "modules.h"

struct mlop_l2c : public champsim::modules::prefetcher {
  using champsim::modules::prefetcher::prefetcher;

  void prefetcher_initialize();
  uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                    uint32_t metadata_in);
  uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);
  void prefetcher_final_stats();
};

#endif // PREFETCHER_MLOP_L2C_H
