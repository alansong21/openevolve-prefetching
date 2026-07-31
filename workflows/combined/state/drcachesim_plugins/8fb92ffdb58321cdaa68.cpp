// drcachesim backend seed: bounded per-PC constant-stride prefetcher.
#include "simulator/prefetcher_plugin.h"

#include "common/trace_entry.h"
#include "simulator/caching_device.h"

#include <algorithm>
#include <array>
#include <cstdint>

// EVOLVE-BLOCK-START
namespace dynamorio::drmemtrace {
namespace {
struct dr_stride_entry {
  addr_t pc = 0;
  addr_t last_line = 0;
  int64_t stride = 0;
  uint8_t confidence = 0;
  bool valid = false;
};

// VIZIER_KNOB[discrete,64|128|256|512]
constexpr std::size_t kDrStrideEntries = 256;

class openevolve_dr_prefetcher final : public prefetcher_t {
public:
  explicit openevolve_dr_prefetcher(int block_size) : prefetcher_t(block_size) {}

  void prefetch(caching_device_t* cache, const memref_t& demand, bool missed) override
  {
    if (demand.data.type == TRACE_TYPE_HARDWARE_PREFETCH)
      return;
    const addr_t line = demand.data.addr / static_cast<addr_t>(block_size_);
    auto& entry = entries_[demand.data.pc % entries_.size()];
    if (entry.valid && entry.pc == demand.data.pc) {
      const int64_t stride = static_cast<int64_t>(line) - static_cast<int64_t>(entry.last_line);
      if (stride == entry.stride && stride != 0) {
        entry.confidence = static_cast<uint8_t>(std::min<int>(3, entry.confidence + 1));
      } else {
        entry.stride = stride;
        entry.confidence = 0;
      }
      if (entry.confidence >= 1 && missed) {
        memref_t request = demand;
        request.data.type = TRACE_TYPE_HARDWARE_PREFETCH;
        request.data.addr = static_cast<addr_t>(
            (static_cast<int64_t>(line) + entry.stride) * block_size_);
        cache->request(request);
      }
    } else {
      entry = {};
      entry.pc = demand.data.pc;
      entry.last_line = line;
      entry.valid = true;
    }
    entry.last_line = line;
  }

private:
  std::array<dr_stride_entry, kDrStrideEntries> entries_{};
};

class openevolve_dr_prefetcher_factory final : public prefetcher_factory_t {
public:
  prefetcher_t* create_prefetcher(int block_size) override
  {
    return new openevolve_dr_prefetcher(block_size);
  }
};
} // namespace
} // namespace dynamorio::drmemtrace

extern "C" uint64_t drcachesim_prefetcher_plugin_abi_version()
{
  return dynamorio::drmemtrace::DRCACHESIM_PREFETCHER_PLUGIN_ABI_VERSION;
}

extern "C" dynamorio::drmemtrace::prefetcher_factory_t* drcachesim_create_prefetcher_factory()
{
  return new dynamorio::drmemtrace::openevolve_dr_prefetcher_factory();
}

extern "C" void drcachesim_destroy_prefetcher_factory(dynamorio::drmemtrace::prefetcher_factory_t* factory)
{
  delete factory;
}
// EVOLVE-BLOCK-END

// drcachesim backend seed: LRU with per-way age counters (matches stock policy_lru).
#include "simulator/replacement_policy_plugin.h"

#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>

// EVOLVE-BLOCK-START
namespace dynamorio::drmemtrace {
namespace {
// VIZIER_KNOB[discrete,32768|65536]
constexpr std::size_t kDrMaxReplacementLines = 32768;

class openevolve_dr_replacement final : public cache_replacement_policy_t {
public:
  openevolve_dr_replacement(int num_sets, int associativity)
      : cache_replacement_policy_t(num_sets, associativity)
  {
    for (int set = 0; set < num_sets; ++set) {
      const std::size_t base = static_cast<std::size_t>(set * associativity);
      for (int way = 0; way < associativity; ++way)
        lru_counters_[base + static_cast<std::size_t>(way)] =
            static_cast<uint8_t>(way);
    }
  }

  void access_update(int set_idx, int way, cache_access_outcome_t /*outcome*/) override
  {
    const std::size_t base = static_cast<std::size_t>(set_idx * associativity_);
    const std::size_t position = base + static_cast<std::size_t>(way);
    const uint8_t rank = lru_counters_[position];
    if (rank == 0)
      return;
    for (int candidate = 0; candidate < associativity_; ++candidate) {
      const std::size_t other = base + static_cast<std::size_t>(candidate);
      if (candidate != way && lru_counters_[other] < rank)
        ++lru_counters_[other];
    }
    lru_counters_[position] = 0;
  }

  void eviction_update(int /*set_idx*/, int /*way*/) override {}

  void invalidation_update(int set_idx, int way) override
  {
    lru_counters_[static_cast<std::size_t>(set_idx * associativity_ + way)] =
        static_cast<uint8_t>(associativity_);
  }

  int get_next_way_to_replace(int set_idx) const override
  {
    const std::size_t base = static_cast<std::size_t>(set_idx * associativity_);
    int victim = 0;
    for (int way = 1; way < associativity_; ++way) {
      if (lru_counters_[base + static_cast<std::size_t>(way)] >
          lru_counters_[base + static_cast<std::size_t>(victim)])
        victim = way;
    }
    return victim;
  }

  std::string get_name() const override { return "openevolve_lru"; }

private:
  uint8_t lru_counters_[kDrMaxReplacementLines]{};
};
} // namespace
} // namespace dynamorio::drmemtrace

extern "C" uint64_t drcachesim_replacement_policy_plugin_abi_version()
{
  return dynamorio::drmemtrace::DRCACHESIM_REPLACEMENT_POLICY_PLUGIN_ABI_VERSION;
}

extern "C" dynamorio::drmemtrace::cache_replacement_policy_t*
drcachesim_create_replacement_policy(int num_sets, int associativity)
{
  return new dynamorio::drmemtrace::openevolve_dr_replacement(num_sets, associativity);
}
// EVOLVE-BLOCK-END
