#include "mlop_l2c.h"

#include <iostream>
#include "cache.h"

void mlop_l2c::prefetcher_initialize()
{
  std::cout << "CPU " << intern_->cpu << " L2C next line prefetcher" << std::endl;
}

uint32_t mlop_l2c::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                            uint32_t metadata_in)
{
  (void)ip;
  (void)cache_hit;
  (void)useful_prefetch;

  if (type != access_type::LOAD)
    return metadata_in;

  champsim::block_number pf_block{champsim::block_number{addr} + 1};
  prefetch_line(champsim::address{pf_block}, true, metadata_in);

  return metadata_in;
}

uint32_t mlop_l2c::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr,
                                         uint32_t metadata_in)
{
  (void)addr;
  (void)set;
  (void)way;
  (void)prefetch;
  (void)evicted_addr;
  return metadata_in;
}

void mlop_l2c::prefetcher_final_stats()
{
  std::cout << "CPU " << intern_->cpu << " L2C next line prefetcher final stats" << std::endl;
}
