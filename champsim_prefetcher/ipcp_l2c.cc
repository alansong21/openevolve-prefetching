/*
 *    IPCP L2 prefetcher, adapted for modern ChampSim prefetcher API
 */

#include "ipcp_l2c.h"

#include <array>
#include <cstdint>
#include <iostream>
#include <vector>

#include "champsim.h"
#include "cache.h"
#include "modules.h"

#define NUM_IP_TABLE_L2_ENTRIES 1024
#define NUM_IP_INDEX_BITS 10
#define NUM_IP_TAG_BITS 6
#define S_TYPE 1  // global stream (GS)
#define CS_TYPE 2 // constant stride (CS)
#define CPLX_TYPE 3
#define NL_TYPE 4 // next line

// #define SIG_DEBUG_PRINT_L2
#ifdef SIG_DEBUG_PRINT_L2
#define SIG_DP(x) x
#else
#define SIG_DP(x)
#endif

namespace {
struct ip_tracker {
  uint64_t ip_tag = 0;
  uint16_t ip_valid = 0;
  uint32_t pref_type = 0; // prefetch class type
  int stride = 0;         // last stride sent by metadata
};

std::vector<uint32_t> spec_nl_l2;
std::vector<std::vector<ip_tracker>> trackers;

int decode_stride(uint32_t metadata)
{
  if (metadata & 0b1000000)
    return -1 * static_cast<int>(metadata & 0b111111);
  return static_cast<int>(metadata & 0b111111);
}
} // namespace

void ipcp_l2c::prefetcher_initialize()
{
  if (spec_nl_l2.size() != NUM_CPUS) {
    spec_nl_l2.assign(NUM_CPUS, 0);
  }

  if (trackers.size() != NUM_CPUS) {
    trackers.assign(NUM_CPUS, std::vector<ip_tracker>(NUM_IP_TABLE_L2_ENTRIES));
  }
}

uint32_t ipcp_l2c::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                            uint32_t metadata_in)
{
  (void)cache_hit;
  (void)useful_prefetch;

  const auto cpu = intern_->cpu;
  auto cl_addr = champsim::block_number{addr};
  int prefetch_degree = 0;
  int stride = decode_stride(metadata_in);
  uint32_t pref_type = metadata_in & 0xF00;
  uint16_t ip_tag = (ip.to<uint64_t>() >> NUM_IP_INDEX_BITS) & ((1 << NUM_IP_TAG_BITS) - 1);

  // degree policy
  if (NUM_CPUS == 1) {
    prefetch_degree = (intern_->get_mshr_occupancy_ratio() < 0.5) ? 4 : 3;
  } else {
    prefetch_degree = 2; // tighten for multi-core
  }

  // calculate the index bit
  int index = static_cast<int>(ip.to<uint64_t>() & ((1ULL << NUM_IP_INDEX_BITS) - 1));
  if (trackers[cpu][index].ip_tag != ip_tag) {
    if (trackers[cpu][index].ip_valid == 0) {
      trackers[cpu][index].ip_tag = ip_tag;
      trackers[cpu][index].pref_type = pref_type;
      trackers[cpu][index].stride = stride;
    } else {
      trackers[cpu][index].ip_valid = 0;
    }

    // issue a next line prefetch upon encountering new IP
    champsim::block_number pf_address{cl_addr.to<uint64_t>() + 1};
    prefetch_line(champsim::address{pf_address}, intern_->get_mshr_occupancy_ratio() < 0.5, 0);
    SIG_DP(std::cout << "1, ");
    return metadata_in;
  }

  // if same IP encountered, set valid bit
  trackers[cpu][index].ip_valid = 1;

  // update the IP table upon receiving metadata from prefetch
  if (type == access_type::PREFETCH) {
    trackers[cpu][index].pref_type = pref_type;
    trackers[cpu][index].stride = stride;
    spec_nl_l2[cpu] = metadata_in & 0x1000;
  }

  SIG_DP(
      std::cout << ip.to<uint64_t>() << ", " << static_cast<int>(cache_hit) << ", " << cl_addr << ", ";
      std::cout << ", " << stride << "; ";
  );

  // prefetch for GS, CS, NL classes
  if (trackers[cpu][index].stride != 0) {
    if (trackers[cpu][index].pref_type == 0x100 || trackers[cpu][index].pref_type == 0x200) {
      if (trackers[cpu][index].pref_type == 0x100 && NUM_CPUS == 1)
        prefetch_degree = 4;

      for (int i = 0; i < prefetch_degree; i++) {
        auto stride_step = champsim::block_number::difference_type(trackers[cpu][index].stride * (i + 1));
        champsim::block_number pf_address{cl_addr + stride_step};

        // Check if prefetch address is in same 4 KB page
        if (champsim::page_number{pf_address} != champsim::page_number{cl_addr})
          break;

        prefetch_line(champsim::address{pf_address}, intern_->get_mshr_occupancy_ratio() < 0.5, 0);
        SIG_DP(std::cout << trackers[cpu][index].stride << ", ");
      }
    } else if (trackers[cpu][index].pref_type == 0x400 && spec_nl_l2[cpu] > 0) {
      champsim::block_number pf_address{cl_addr.to<uint64_t>() + 1};
      prefetch_line(champsim::address{pf_address}, intern_->get_mshr_occupancy_ratio() < 0.5, 0);
      SIG_DP(std::cout << "1;");
    }
  }

  SIG_DP(std::cout << std::endl);
  return metadata_in;
}

uint32_t ipcp_l2c::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr,
                                         uint32_t metadata_in)
{
  (void)addr;
  (void)set;
  (void)way;
  (void)prefetch;
  (void)evicted_addr;
  return metadata_in;
}

void ipcp_l2c::prefetcher_final_stats() {}
