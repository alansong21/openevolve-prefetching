// Combined initial program for joint prefetcher + replacement evolution.
//
// LAYOUT
// ======
// This single file holds two independent C++ translation units glued together
// by the OpenEvolve combined evaluator:
//
//   1. The L2C prefetcher implementation (`openevolve_prefetcher`).
//   2. The L2C replacement-policy implementation (`openevolve_replacement`).
//
// The evaluator splits this file on the // === OPENEVOLVE_PREFETCHER_BEGIN ===
// / // === OPENEVOLVE_PREFETCHER_END === and // === OPENEVOLVE_REPLACEMENT_BEGIN ===
// / // === OPENEVOLVE_REPLACEMENT_END === markers and writes each section to
// the matching ChampSim shim before building. The markers MUST stay verbatim
// (and on their own line). Each section must remain a self-contained C++
// translation unit; do not share state or `using` directives across sections.
//
// EVOLVE-BLOCK markers tell OpenEvolve which regions are mutable. There are
// two evolve blocks (one per section); mutations may reshape state, helpers,
// and member function bodies inside those blocks but must keep the public
// member signatures unchanged.

// === OPENEVOLVE_PREFETCHER_BEGIN ===
// Seed: IPCP (ISCA'20) prefetcher adapted for the OpenEvolve L2C hook.
// Combines per-PC stride tracking, a global history buffer for stream
// detection, a delta-prediction table for complex patterns, and an MPKC-gated
// next-line fallback. Evolve toward more aggressive table sizing,
// per-region throttling, replacement-aware insertion, etc.

#include <array>
#include <cstdint>
#include <iostream>

#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START
namespace {
constexpr int kNumIpTableEntries = 1024;
constexpr int kNumGhbEntries = 16;
constexpr int kNumIpIndexBits = 10;
constexpr int kNumIpTagBits = 6;
constexpr int kSignatureBits = 12;
constexpr int kDptEntries = 1 << kSignatureBits;
constexpr int kStreamType = 1;
constexpr int kConstStrideType = 2;
constexpr int kComplexStrideType = 3;
constexpr int kNextLineType = 4;

struct ip_table_entry {
  uint64_t ip_tag = 0;
  uint64_t last_page = 0;
  uint64_t last_cl_offset = 0;
  int64_t last_stride = 0;
  uint16_t ip_valid = 0;
  int conf = 0;
  uint16_t signature = 0;
  uint16_t str_dir = 0;
  uint16_t str_valid = 0;
  uint16_t str_strength = 0;
};

struct delta_pred_entry {
  int delta = 0;
  int conf = 0;
};

std::array<ip_table_entry, kNumIpTableEntries> trackers_l1;
std::array<delta_pred_entry, kDptEntries> dpt_l1;
std::array<uint64_t, kNumGhbEntries> ghb_l1;
uint64_t prev_cpu_cycle = 0;
uint64_t num_misses = 0;
float mpkc = 0.0f;
int spec_nl = 0;

uint16_t update_sig_l1(uint16_t old_sig, int delta)
{
  const int sig_delta = (delta < 0) ? (((-1) * delta) + (1 << 6)) : delta;
  return static_cast<uint16_t>(((old_sig << 1) ^ sig_delta) & 0xFFF);
}

uint32_t encode_metadata(int stride, uint16_t type, int spec_nl_bit)
{
  uint32_t metadata = 0;
  if (stride > 0) {
    metadata = static_cast<uint32_t>(stride);
  } else {
    metadata = static_cast<uint32_t>((-1 * stride) | 0b1000000);
  }

  metadata |= static_cast<uint32_t>(type << 8);
  metadata |= static_cast<uint32_t>(spec_nl_bit << 12);
  return metadata;
}

void check_for_stream_l1(int index, uint64_t cl_addr)
{
  int pos_count = 0;
  int neg_count = 0;
  int count = 0;
  uint64_t check_addr = cl_addr;

  for (int i = 0; i < kNumGhbEntries; i++) {
    check_addr--;
    for (int j = 0; j < kNumGhbEntries; j++) {
      if (check_addr == ghb_l1[j]) {
        pos_count++;
        break;
      }
    }
  }

  check_addr = cl_addr;
  for (int i = 0; i < kNumGhbEntries; i++) {
    check_addr++;
    for (int j = 0; j < kNumGhbEntries; j++) {
      if (check_addr == ghb_l1[j]) {
        neg_count++;
        break;
      }
    }
  }

  if (pos_count > neg_count) {
    trackers_l1[index].str_dir = 1;
    count = pos_count;
  } else {
    trackers_l1[index].str_dir = 0;
    count = neg_count;
  }

  if (count > kNumGhbEntries / 2) {
    trackers_l1[index].str_valid = 1;
    if (count >= (kNumGhbEntries * 3) / 4)
      trackers_l1[index].str_strength = 1;
  } else {
    if (trackers_l1[index].str_strength == 0)
      trackers_l1[index].str_valid = 0;
  }
}

int update_conf(int stride, int pred_stride, int conf)
{
  if (stride == pred_stride) {
    conf++;
    if (conf > 3)
      conf = 3;
  } else {
    conf--;
    if (conf < 0)
      conf = 0;
  }

  return conf;
}
} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{
  trackers_l1.fill({});
  dpt_l1.fill({});
  ghb_l1.fill(0);
  prev_cpu_cycle = 0;
  num_misses = 0;
  mpkc = 0.0f;
  spec_nl = 0;

  std::cout << "IPCP_AT_L2C_CONFIG" << std::endl
            << "NUM_IP_TABLE_L1_ENTRIES " << kNumIpTableEntries << std::endl
            << "NUM_GHB_ENTRIES " << kNumGhbEntries << std::endl
            << "NUM_IP_INDEX_BITS " << kNumIpIndexBits << std::endl
            << "NUM_IP_TAG_BITS " << kNumIpTagBits << std::endl
            << "S_TYPE " << kStreamType << std::endl
            << "CS_TYPE " << kConstStrideType << std::endl
            << "CPLX_TYPE " << kComplexStrideType << std::endl
            << "NL_TYPE " << kNextLineType << std::endl
            << std::endl;
}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address address, champsim::address ip_addr, uint8_t cache_hit,
                                                         bool useful_prefetch, access_type type, uint32_t metadata_in)
{
  (void)useful_prefetch;

  if (type != access_type::LOAD && type != access_type::PREFETCH)
    return metadata_in;

  const uint64_t addr = address.to<uint64_t>();
  const uint64_t ip = ip_addr.to<uint64_t>();
  const uint64_t curr_page = addr >> LOG2_PAGE_SIZE;
  const uint64_t cl_addr = addr >> LOG2_BLOCK_SIZE;
  const uint64_t cl_offset = (addr >> LOG2_BLOCK_SIZE) & 0x3F;
  uint16_t signature = 0;
  uint16_t last_signature = 0;
  int prefetch_degree = 3;
  int spec_nl_threshold = 15;
  int num_prefs = 0;
  uint32_t metadata = 0;
  const uint16_t ip_tag = static_cast<uint16_t>((ip >> kNumIpIndexBits) & ((1 << kNumIpTagBits) - 1));

  if (cache_hit == 0)
    num_misses += 1;

  const uint64_t ct = intern_->current_time.time_since_epoch() / intern_->clock_period;
  if (num_misses == 256) {
    mpkc = (static_cast<float>(num_misses) / static_cast<float>(ct - prev_cpu_cycle)) * 1000.0f;
    prev_cpu_cycle = ct;
    spec_nl = (mpkc > static_cast<float>(spec_nl_threshold)) ? 0 : 1;
    num_misses = 0;
  }

  const int index = static_cast<int>(ip & ((1 << kNumIpIndexBits) - 1));
  if (trackers_l1[index].ip_tag != ip_tag) {
    if (trackers_l1[index].ip_valid == 0) {
      trackers_l1[index].ip_tag = ip_tag;
      trackers_l1[index].last_page = curr_page;
      trackers_l1[index].last_cl_offset = cl_offset;
      trackers_l1[index].last_stride = 0;
      trackers_l1[index].signature = 0;
      trackers_l1[index].conf = 0;
      trackers_l1[index].str_valid = 0;
      trackers_l1[index].str_strength = 0;
      trackers_l1[index].str_dir = 0;
      trackers_l1[index].ip_valid = 1;
    } else {
      trackers_l1[index].ip_valid = 0;
    }

    const uint64_t pf_address = ((addr >> LOG2_BLOCK_SIZE) + 1) << LOG2_BLOCK_SIZE;
    metadata = encode_metadata(1, kNextLineType, spec_nl);
    prefetch_line(champsim::address{pf_address}, true, metadata);
    return metadata_in;
  }

  trackers_l1[index].ip_valid = 1;

  int64_t stride = 0;
  if (cl_offset > trackers_l1[index].last_cl_offset) {
    stride = static_cast<int64_t>(cl_offset - trackers_l1[index].last_cl_offset);
  } else {
    stride = static_cast<int64_t>(trackers_l1[index].last_cl_offset - cl_offset);
    stride *= -1;
  }

  if (stride == 0)
    return metadata_in;

  if (curr_page != trackers_l1[index].last_page) {
    if (stride < 0)
      stride += 64;
    else
      stride -= 64;
  }

  trackers_l1[index].conf = update_conf(static_cast<int>(stride), static_cast<int>(trackers_l1[index].last_stride), trackers_l1[index].conf);
  if (trackers_l1[index].conf == 0)
    trackers_l1[index].last_stride = stride;

  last_signature = trackers_l1[index].signature;
  dpt_l1[last_signature].conf =
      update_conf(static_cast<int>(stride), dpt_l1[last_signature].delta, dpt_l1[last_signature].conf);

  if (dpt_l1[last_signature].conf == 0)
    dpt_l1[last_signature].delta = static_cast<int>(stride);

  signature = update_sig_l1(last_signature, static_cast<int>(stride));
  trackers_l1[index].signature = signature;

  check_for_stream_l1(index, cl_addr);

  if (trackers_l1[index].str_valid == 1) {
    prefetch_degree *= 2;
    for (int i = 0; i < prefetch_degree; i++) {
      uint64_t pf_address = 0;

      if (trackers_l1[index].str_dir == 1) {
        pf_address = (cl_addr + i + 1) << LOG2_BLOCK_SIZE;
        metadata = encode_metadata(1, kStreamType, spec_nl);
      } else {
        pf_address = (cl_addr - i - 1) << LOG2_BLOCK_SIZE;
        metadata = encode_metadata(-1, kStreamType, spec_nl);
      }

      if ((pf_address >> LOG2_PAGE_SIZE) != (addr >> LOG2_PAGE_SIZE))
        break;

      prefetch_line(champsim::address{pf_address}, true, metadata);
      num_prefs++;
    }

  } else if (trackers_l1[index].conf > 1 && trackers_l1[index].last_stride != 0) {
    for (int i = 0; i < prefetch_degree; i++) {
      const uint64_t pf_address =
          (cl_addr + (trackers_l1[index].last_stride * static_cast<int64_t>(i + 1))) << LOG2_BLOCK_SIZE;

      if ((pf_address >> LOG2_PAGE_SIZE) != (addr >> LOG2_PAGE_SIZE))
        break;

      metadata = encode_metadata(static_cast<int>(trackers_l1[index].last_stride), kConstStrideType, spec_nl);
      prefetch_line(champsim::address{pf_address}, true, metadata);
      num_prefs++;
    }
  } else if (dpt_l1[signature].conf >= 0 && dpt_l1[signature].delta != 0) {
    int pref_offset = 0;
    for (int i = 0; i < prefetch_degree; i++) {
      pref_offset += dpt_l1[signature].delta;
      const uint64_t pf_address = (cl_addr + pref_offset) << LOG2_BLOCK_SIZE;

      if ((pf_address >> LOG2_PAGE_SIZE) != (addr >> LOG2_PAGE_SIZE) || (dpt_l1[signature].conf == -1) ||
          (dpt_l1[signature].delta == 0)) {
        break;
      }

      metadata = encode_metadata(0, kComplexStrideType, spec_nl);
      if (dpt_l1[signature].conf > 0) {
        prefetch_line(champsim::address{pf_address}, true, metadata);
        num_prefs++;
      }
      signature = update_sig_l1(signature, dpt_l1[signature].delta);
    }
  }

  if (num_prefs == 0 && spec_nl == 1) {
    const uint64_t pf_address = ((addr >> LOG2_BLOCK_SIZE) + 1) << LOG2_BLOCK_SIZE;
    metadata = encode_metadata(1, kNextLineType, spec_nl);
    prefetch_line(champsim::address{pf_address}, true, metadata);
  }

  trackers_l1[index].last_cl_offset = cl_offset;
  trackers_l1[index].last_page = curr_page;

  int ghb_index = 0;
  for (ghb_index = 0; ghb_index < kNumGhbEntries; ghb_index++) {
    if (cl_addr == ghb_l1[ghb_index])
      break;
  }
  if (ghb_index == kNumGhbEntries) {
    for (ghb_index = kNumGhbEntries - 1; ghb_index > 0; ghb_index--)
      ghb_l1[ghb_index] = ghb_l1[ghb_index - 1];
    ghb_l1[0] = cl_addr;
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
// === OPENEVOLVE_PREFETCHER_END ===

// === OPENEVOLVE_REPLACEMENT_BEGIN ===
// Baseline: DRRIP (Dynamic RRIP), aligned with ChampSim replacement/drrip.
// Evolve toward coordinated prefetcher/replacement behaviour; keep API compatible
// with openevolve_replacement.h (do not change the header).

#include <algorithm>
#include <cassert>
#include <cstdint>
#include <vector>

#include "msl/fwcounter.h"
#include "openevolve_replacement.h"

// EVOLVE-BLOCK-START
namespace {

constexpr unsigned maxRRPV = 3;
constexpr unsigned BRRIP_MAX = 32;
constexpr std::size_t PSEL_WIDTH = 10;

enum class drrip_set_kind { follower, brrip_leader, srrip_leader };

struct openevolve_replacement_state {
  long num_way = 0;
  unsigned brrip_counter = 0;
  std::vector<champsim::msl::fwcounter<PSEL_WIDTH>> PSEL;
  std::vector<unsigned> rrpv;
};

openevolve_replacement_state oer_state{};

unsigned& access_rrpv(long set, long way)
{
  return oer_state.rrpv.at(static_cast<std::size_t>(set * oer_state.num_way + way));
}

void update_brrip(long set, long way)
{
  access_rrpv(set, way) = maxRRPV;
  oer_state.brrip_counter++;
  if (oer_state.brrip_counter == BRRIP_MAX) {
    oer_state.brrip_counter = 0;
    access_rrpv(set, way) = maxRRPV - 1;
  }
}

void update_srrip(long set, long way) { access_rrpv(set, way) = maxRRPV - 1; }

drrip_set_kind set_kind(long set, openevolve_replacement* self)
{
  switch (self->get_set_sample_category(set)) {
    case 0:
      return drrip_set_kind::brrip_leader;
    case 1:
      return drrip_set_kind::srrip_leader;
    default:
      return drrip_set_kind::follower;
  }
}

} // namespace

openevolve_replacement::openevolve_replacement(CACHE* cache) : openevolve_replacement(cache, cache->NUM_SET, cache->NUM_WAY) {}

openevolve_replacement::openevolve_replacement(CACHE* cache, long sets, long ways) : replacement(cache)
{
  oer_state.num_way = ways;
  oer_state.brrip_counter = 0;
  oer_state.rrpv.assign(static_cast<std::size_t>(sets * ways), 0);
  oer_state.PSEL.clear();
  oer_state.PSEL.reserve(NUM_CPUS);
  const auto psel_mid = static_cast<long long>(1) << (PSEL_WIDTH - 1);
  for (std::size_t i = 0; i < NUM_CPUS; ++i) {
    oer_state.PSEL.emplace_back(psel_mid);
  }
}

long openevolve_replacement::find_victim(uint32_t triggering_cpu, uint64_t instr_id, long set, const champsim::cache_block* current_set,
                                         champsim::address ip, champsim::address full_addr, access_type type)
{
  (void)triggering_cpu;
  (void)instr_id;
  (void)current_set;
  (void)ip;
  (void)full_addr;
  (void)type;

  auto begin = std::next(std::begin(oer_state.rrpv), set * oer_state.num_way);
  auto end = std::next(begin, oer_state.num_way);
  auto victim = std::max_element(begin, end);
  if (const unsigned rrpv_update = maxRRPV - *victim; rrpv_update != 0) {
    for (auto it = begin; it != end; ++it) {
      *it += rrpv_update;
    }
  }
  assert(begin <= victim);
  assert(victim < end);
  return std::distance(begin, victim);
}

void openevolve_replacement::replacement_cache_fill(uint32_t triggering_cpu, long set, long way, champsim::address full_addr, champsim::address ip,
                                                    champsim::address victim_addr, access_type type)
{
  (void)full_addr;
  (void)ip;
  (void)victim_addr;

  if (access_type{type} == access_type::WRITE) {
    access_rrpv(set, way) = maxRRPV - 1;
    return;
  }

  auto& selector = oer_state.PSEL.at(triggering_cpu);
  switch (set_kind(set, this)) {
    case drrip_set_kind::follower:
      if (selector.value() > (selector.maximum / 2)) {
        update_brrip(set, way);
      } else {
        update_srrip(set, way);
      }
      break;
    case drrip_set_kind::brrip_leader:
      oer_state.PSEL[triggering_cpu]--;
      update_brrip(set, way);
      break;
    case drrip_set_kind::srrip_leader:
      oer_state.PSEL[triggering_cpu]++;
      update_srrip(set, way);
      break;
  }
}

void openevolve_replacement::update_replacement_state(uint32_t triggering_cpu, long set, long way, champsim::address full_addr, champsim::address ip,
                                                      champsim::address victim_addr, access_type type, uint8_t hit)
{
  (void)triggering_cpu;
  (void)full_addr;
  (void)ip;
  (void)victim_addr;

  if (hit) {
    if (access_type{type} == access_type::WRITE) {
      access_rrpv(set, way) = maxRRPV - 1;
      return;
    }
    access_rrpv(set, way) = 0;
  }
}
// EVOLVE-BLOCK-END
// === OPENEVOLVE_REPLACEMENT_END ===
