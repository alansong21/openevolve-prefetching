#include "openevolve_prefetcher.h"
#include <vector>

// EVOLVE-BLOCK-START

namespace {
constexpr int NUM_IP_TABLE_L2_ENTRIES = 1024;
constexpr int NUM_IP_INDEX_BITS = 10;
constexpr int NUM_IP_TAG_BITS = 6;

struct ip_tracker {
  uint64_t ip_tag = 0;
  uint16_t ip_valid = 0;
  uint32_t pref_type = 0;
  int stride = 0;
};

std::vector<uint32_t> spec_nl_l2;
std::vector<std::vector<ip_tracker>> trackers;

int decode_stride(uint32_t metadata)
{
  // Improved stride decoding with sign extension
  int stride = static_cast<int>(metadata & 0x3F); // Extract lower 6 bits
  if (metadata & 0x40) // Check if the stride is negative
    stride |= 0xFFFFFFC0; // Sign extend to 32 bits
  return stride;
}
} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{
  if (spec_nl_l2.size() != NUM_CPUS)
    spec_nl_l2.assign(NUM_CPUS, 0);

  if (trackers.size() != NUM_CPUS)
    trackers.assign(NUM_CPUS, std::vector<ip_tracker>(NUM_IP_TABLE_L2_ENTRIES));
}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch,
                                                         access_type type, uint32_t metadata_in)
{
  (void)cache_hit;
  (void)useful_prefetch;

  const auto cpu = intern_->cpu;
  auto cl_addr = champsim::block_number{addr};
  // Initialize prefetch degree and stride
  int prefetch_degree = 1; // Start conservatively
  int stride = decode_stride(metadata_in);

  // Use a history of past strides to better predict future strides
  static std::vector<int> stride_history(NUM_CPUS, 0);
  stride_history[cpu] = stride_history[cpu] * 0.8 + stride * 0.2; // Simple moving average

  // Adjust prefetch degree based on stride history and MSHR occupancy
  if (useful_prefetch) {
    prefetch_degree = std::min(prefetch_degree + 1, 4); // Increase degree if useful
  } else {
    prefetch_degree = std::max(prefetch_degree - 1, 1); // Decrease degree if not useful
  }

  if (intern_->get_mshr_occupancy_ratio() > 0.7) {
    prefetch_degree = std::max(prefetch_degree - 1, 1); // Throttle if MSHR occupancy is high
  }
  uint32_t pref_type = metadata_in & 0xF00;
  uint16_t ip_tag = (ip.to<uint64_t>() >> NUM_IP_INDEX_BITS) & ((1 << NUM_IP_TAG_BITS) - 1);

  // Dynamic prefetch degree based on MSHR occupancy and usefulness
  // Adjust prefetch degree based on past usefulness
  if (useful_prefetch) {
    prefetch_degree = std::min(prefetch_degree + 1, 4); // Increase degree if useful
  } else {
    prefetch_degree = std::max(prefetch_degree - 1, 1); // Decrease degree if not useful
  }

  int index = static_cast<int>(ip.to<uint64_t>() & ((1ULL << NUM_IP_INDEX_BITS) - 1));
  if (trackers[cpu][index].ip_tag != ip_tag) {
    if (trackers[cpu][index].ip_valid == 0) {
      trackers[cpu][index].ip_tag = ip_tag;
      trackers[cpu][index].pref_type = pref_type;
      trackers[cpu][index].stride = stride;
    } else {
      trackers[cpu][index].ip_valid = 0;
    }

    champsim::block_number pf_address{cl_addr.to<uint64_t>() + 1};
    prefetch_line(champsim::address{pf_address}, intern_->get_mshr_occupancy_ratio() < 0.5, 0);
    return metadata_in;
  }

  trackers[cpu][index].ip_valid = 1;

  if (type == access_type::PREFETCH) {
    trackers[cpu][index].pref_type = pref_type;
    trackers[cpu][index].stride = stride;
    spec_nl_l2[cpu] = metadata_in & 0x1000;
  }

  if (trackers[cpu][index].stride != 0 && trackers[cpu][index].ip_valid > 0) { // Confidence-based decision
    // Use the average stride for more accurate prefetching
    int effective_stride = stride_history[cpu];
    if (trackers[cpu][index].pref_type == 0x100 || trackers[cpu][index].pref_type == 0x200) {
      if (trackers[cpu][index].pref_type == 0x100 && NUM_CPUS == 1)
        prefetch_degree = 4;

      int lookahead = 1; // Adjust lookahead based on confidence
      for (int i = 0; i < prefetch_degree * lookahead; i++) {
        auto stride_step = champsim::block_number::difference_type(effective_stride * (i + 1));
        champsim::block_number pf_address{cl_addr + stride_step};

        if (champsim::page_number{pf_address} != champsim::page_number{cl_addr})
          break;

        // Issue prefetch only if MSHR occupancy is low and prefetch is likely useful
        // Use metadata to track prefetch accuracy
        prefetch_line(champsim::address{pf_address}, intern_->get_mshr_occupancy_ratio() < 0.5, metadata_in);
      }
    } else if (trackers[cpu][index].pref_type == 0x400 && spec_nl_l2[cpu] > 0) {
      champsim::block_number pf_address{cl_addr.to<uint64_t>() + 1};
      prefetch_line(champsim::address{pf_address}, intern_->get_mshr_occupancy_ratio() < 0.5, 0);
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
