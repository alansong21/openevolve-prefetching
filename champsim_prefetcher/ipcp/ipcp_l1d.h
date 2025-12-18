#ifndef PREFETCHER_IPCP_L1D_H
#define PREFETCHER_IPCP_L1D_H

#include <cstdint>

#include "address.h"
#include "champsim.h"
#include "modules.h"

struct ipcp_l1d : public champsim::modules::prefetcher {
  using prefetcher::prefetcher;

  void prefetcher_initialize();
  uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                    uint32_t metadata_in);
  uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);
  void prefetcher_final_stats();
};

#endif // PREFETCHER_IPCP_L1D_H
