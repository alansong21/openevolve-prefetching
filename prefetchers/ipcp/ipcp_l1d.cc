/***************************************************************************
For the Third Data Prefetching Championship - DPC3

Paper ID: #4
Instruction Pointer Classifying Prefetcher - IPCP

Authors: 
Samuel Pakalapati - samuelpakalapati@gmail.com
Biswabandan Panda - biswap@cse.iitk.ac.in
***************************************************************************/

#include "ipcp_l1d.h"

#include <array>
#include <cstdint>
#include <iostream>
#include <vector>

#include "cache.h"
#include "champsim.h"
#include "modules.h"

#define NUM_IP_TABLE_L1_ENTRIES 1024                        // IP table entries 
#define NUM_GHB_ENTRIES 16                                  // Entries in the GHB
#define NUM_IP_INDEX_BITS 10                                // Bits to index into the IP table 
#define NUM_IP_TAG_BITS 6                                   // Tag bits per IP table entry
#define S_TYPE 1                                            // stream
#define CS_TYPE 2                                           // constant stride
#define CPLX_TYPE 3                                         // complex stride
#define NL_TYPE 4                                           // next line

// #define SIG_DEBUG_PRINT_L1
#ifdef SIG_DEBUG_PRINT_L1
#define SIG_DP(x) x
#else
#define SIG_DP(x)
#endif

namespace {
struct ip_table_l1_entry {
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

std::vector<std::vector<ip_table_l1_entry>> trackers_l1;
std::vector<std::array<delta_pred_entry, 4096>> DPT_l1;
std::vector<std::array<uint64_t, NUM_GHB_ENTRIES>> ghb_l1;
std::vector<uint64_t> prev_cpu_cycle;
std::vector<uint64_t> num_misses;
std::vector<float> mpkc;
std::vector<int> spec_nl;


/***************Updating the signature*************************************/ 
uint16_t update_sig_l1(uint16_t old_sig, int delta)
{
  uint16_t new_sig = 0;
  int sig_delta = 0;

  // 7-bit sign magnitude form, since we need to track deltas from +63 to -63
  sig_delta = (delta < 0) ? (((-1) * delta) + (1 << 6)) : delta;
  new_sig = ((old_sig << 1) ^ sig_delta) & 0xFFF; // 12-bit signature

  return new_sig;
}



/****************Encoding the metadata***********************************/
uint32_t encode_metadata(int stride, uint16_t type, int spec_nl_bit)
{
  uint32_t metadata = 0;

  // first encode stride in the last 8 bits of the metadata
  if (stride > 0)
    metadata = static_cast<uint32_t>(stride);
  else
    metadata = static_cast<uint32_t>((-1 * stride) | 0b1000000);

  // encode the type of IP in the next 4 bits
  metadata |= static_cast<uint32_t>(type << 8);

  // encode the speculative NL bit in the next 1 bit
  metadata |= static_cast<uint32_t>(spec_nl_bit << 12);

  return metadata;
}


/*********************Checking for a global stream (GS class)***************/

void check_for_stream_l1(int index, champsim::block_number cl_addr, std::size_t cpu)
{
  int pos_count = 0, neg_count = 0, count = 0;
  auto check_addr = cl_addr;

  // check for +ve stream
  for (int i = 0; i < NUM_GHB_ENTRIES; i++) {
    check_addr = champsim::block_number{check_addr.to<uint64_t>() - 1};
    for (int j = 0; j < NUM_GHB_ENTRIES; j++)
      if (check_addr == champsim::block_number{ghb_l1[cpu][j]}) {
        pos_count++;
        break;
      }
  }

  check_addr = cl_addr;
  // check for -ve stream
  for (int i = 0; i < NUM_GHB_ENTRIES; i++) {
    check_addr = champsim::block_number{check_addr.to<uint64_t>() + 1};
    for (int j = 0; j < NUM_GHB_ENTRIES; j++)
      if (check_addr == champsim::block_number{ghb_l1[cpu][j]}) {
        neg_count++;
        break;
      }
  }

  if (pos_count > neg_count) { // stream direction is +ve
    trackers_l1[cpu][index].str_dir = 1;
    count = pos_count;
  } else { // stream direction is -ve
    trackers_l1[cpu][index].str_dir = 0;
    count = neg_count;
  }

  if (count > NUM_GHB_ENTRIES / 2) { // stream is detected
    trackers_l1[cpu][index].str_valid = 1;
    if (count >= (NUM_GHB_ENTRIES * 3) / 4) // stream is classified as strong if more than 3/4th entries belong to stream
      trackers_l1[cpu][index].str_strength = 1;
  } else {
    if (trackers_l1[cpu][index].str_strength == 0) // if identified as weak stream, we need to reset
      trackers_l1[cpu][index].str_valid = 0;
  }

}

/**************************Updating confidence for the CS class****************/
int update_conf(int stride, int pred_stride, int conf)
{
  if (stride == pred_stride) { // use 2-bit saturating counter for confidence
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

void ipcp_l1d::prefetcher_initialize()
{
  const auto cpus = NUM_CPUS;

  if (trackers_l1.size() != cpus)
    trackers_l1.assign(cpus, std::vector<ip_table_l1_entry>(NUM_IP_TABLE_L1_ENTRIES));

  if (DPT_l1.size() != cpus)
    DPT_l1.assign(cpus, {});

  if (ghb_l1.size() != cpus)
    ghb_l1.assign(cpus, {});

  if (prev_cpu_cycle.size() != cpus)
    prev_cpu_cycle.assign(cpus, 0);

  if (num_misses.size() != cpus)
    num_misses.assign(cpus, 0);

  if (mpkc.size() != cpus)
    mpkc.assign(cpus, 0.0f);

  if (spec_nl.size() != cpus)
    spec_nl.assign(cpus, 0);
}

uint32_t ipcp_l1d::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                            uint32_t metadata_in)
{
  (void)useful_prefetch;
  (void)type;

  const auto cpu = intern_->cpu;

  auto curr_page = champsim::page_number{addr}.to<uint64_t>();
  auto cl_addr = champsim::block_number{addr};
  auto cl_offset = cl_addr.to<uint64_t>() & 0x3F;
  uint16_t signature = 0, last_signature = 0;
  int prefetch_degree = 0;
  int spec_nl_threshold = 0;
  int num_prefs = 0;
  uint32_t metadata = 0;
  uint16_t ip_tag = (ip.to<uint64_t>() >> NUM_IP_INDEX_BITS) & ((1 << NUM_IP_TAG_BITS) - 1);

  if (NUM_CPUS == 1) {
    prefetch_degree = 3;
    spec_nl_threshold = 15;
  } else { // tightening the degree and MPKC constraints for multi-core
    prefetch_degree = 2;
    spec_nl_threshold = 5;
  }

  // update miss counter
  if (cache_hit == 0)
    num_misses[cpu] += 1;

  // update spec nl bit when num misses crosses certain threshold
  if (num_misses[cpu] == 256) {
    auto curr_cycle = intern_->current_cycle();
    auto delta_cycle = curr_cycle - prev_cpu_cycle[cpu];
    if (delta_cycle == 0)
      delta_cycle = 1;
    mpkc[cpu] = (static_cast<float>(num_misses[cpu]) / static_cast<float>(delta_cycle)) * 1000.0f;
    prev_cpu_cycle[cpu] = curr_cycle;
    if (mpkc[cpu] > spec_nl_threshold)
      spec_nl[cpu] = 0;
    else
      spec_nl[cpu] = 1;
    num_misses[cpu] = 0;
  }

  // calculate the index bit
  int index = static_cast<int>(ip.to<uint64_t>() & ((1ULL << NUM_IP_INDEX_BITS) - 1));
  if (trackers_l1[cpu][index].ip_tag != ip_tag) { // new/conflict IP
    if (trackers_l1[cpu][index].ip_valid == 0) {  // if valid bit is zero, update with latest IP info
      trackers_l1[cpu][index].ip_tag = ip_tag;
      trackers_l1[cpu][index].last_page = curr_page;
      trackers_l1[cpu][index].last_cl_offset = cl_offset;
      trackers_l1[cpu][index].last_stride = 0;
      trackers_l1[cpu][index].signature = 0;
      trackers_l1[cpu][index].conf = 0;
      trackers_l1[cpu][index].str_valid = 0;
      trackers_l1[cpu][index].str_strength = 0;
      trackers_l1[cpu][index].str_dir = 0;
      trackers_l1[cpu][index].ip_valid = 1;
    } else { // otherwise, reset valid bit and leave the previous IP as it is
      trackers_l1[cpu][index].ip_valid = 0;
    }

    // issue a next line prefetch upon encountering new IP
    champsim::block_number pf_address{cl_addr.to<uint64_t>() + 1};
    metadata = encode_metadata(1, NL_TYPE, spec_nl[cpu]);
    prefetch_line(champsim::address{pf_address}, intern_->get_mshr_occupancy_ratio() < 0.5, metadata);
    return metadata_in;
  }
  // if same IP encountered, set valid bit
  trackers_l1[cpu][index].ip_valid = 1;

  // calculate the stride between the current address and the last address
  int64_t stride = 0;
  if (cl_offset > trackers_l1[cpu][index].last_cl_offset)
    stride = static_cast<int64_t>(cl_offset - trackers_l1[cpu][index].last_cl_offset);
  else {
    stride = static_cast<int64_t>(trackers_l1[cpu][index].last_cl_offset - cl_offset) * -1;
  }

  // don't do anything if same address is seen twice in a row
  if (stride == 0)
    return metadata_in;


// page boundary learning
  if (curr_page != trackers_l1[cpu][index].last_page) {
    if (stride < 0)
      stride += 64;
    else
      stride -= 64;
  }

// update constant stride(CS) confidence
  trackers_l1[cpu][index].conf = update_conf(static_cast<int>(stride), static_cast<int>(trackers_l1[cpu][index].last_stride),
                                             trackers_l1[cpu][index].conf);

// update CS only if confidence is zero
  if (trackers_l1[cpu][index].conf == 0)
    trackers_l1[cpu][index].last_stride = stride;

  last_signature = trackers_l1[cpu][index].signature;
// update complex stride(CPLX) confidence
  DPT_l1[cpu][last_signature].conf = update_conf(static_cast<int>(stride), DPT_l1[cpu][last_signature].delta, DPT_l1[cpu][last_signature].conf);

// update CPLX only if confidence is zero
  if (DPT_l1[cpu][last_signature].conf == 0)
    DPT_l1[cpu][last_signature].delta = static_cast<int>(stride);

// calculate and update new signature in IP table
  signature = update_sig_l1(last_signature, static_cast<int>(stride));
  trackers_l1[cpu][index].signature = signature;

// check GHB for stream IP
  check_for_stream_l1(index, cl_addr, cpu);

SIG_DP(
      std::cout << ip.to<uint64_t>() << ", " << static_cast<int>(cache_hit) << ", " << cl_addr << ", " << addr << ", " << stride << "; ";
      std::cout << last_signature << ", " << DPT_l1[cpu][last_signature].delta << ", " << DPT_l1[cpu][last_signature].conf << "; ";
      std::cout << trackers_l1[cpu][index].last_stride << ", " << stride << ", " << trackers_l1[cpu][index].conf << ", " << "; ";
);

  auto fill_here = intern_->get_mshr_occupancy_ratio() < 0.5;

  if (trackers_l1[cpu][index].str_valid == 1) { // stream IP
    // for stream, prefetch with twice the usual degree
    prefetch_degree = prefetch_degree * 2;
    for (int i = 0; i < prefetch_degree; i++) {
      champsim::block_number pf_address{};

      if (trackers_l1[cpu][index].str_dir == 1) { // +ve stream
        pf_address = champsim::block_number{cl_addr.to<uint64_t>() + static_cast<uint64_t>(i + 1)};
        metadata = encode_metadata(1, S_TYPE, spec_nl[cpu]); // stride is 1
      } else {                                               // -ve stream
        pf_address = champsim::block_number{cl_addr.to<uint64_t>() - static_cast<uint64_t>(i + 1)};
        metadata = encode_metadata(-1, S_TYPE, spec_nl[cpu]); // stride is -1
      }

      // Check if prefetch address is in same 4 KB page
      if (champsim::page_number{pf_address} != champsim::page_number{cl_addr}) {
        break;
      }

      prefetch_line(champsim::address{pf_address}, fill_here, metadata);
      num_prefs++;
      SIG_DP(std::cout << "1, ");
    }

  } else if (trackers_l1[cpu][index].conf > 1 && trackers_l1[cpu][index].last_stride != 0) { // CS IP
    for (int i = 0; i < prefetch_degree; i++) {
      auto stride_step = champsim::block_number::difference_type(trackers_l1[cpu][index].last_stride * (i + 1));
      champsim::block_number pf_address{cl_addr + stride_step};

      // Check if prefetch address is in same 4 KB page
      if (champsim::page_number{pf_address} != champsim::page_number{cl_addr}) {
        break;
      }

      metadata = encode_metadata(static_cast<int>(trackers_l1[cpu][index].last_stride), CS_TYPE, spec_nl[cpu]);
      prefetch_line(champsim::address{pf_address}, fill_here, metadata);
      num_prefs++;
      SIG_DP(std::cout << trackers_l1[cpu][index].last_stride << ", ");
    }
  } else if (DPT_l1[cpu][signature].conf >= 0 && DPT_l1[cpu][signature].delta != 0) { // CPLX IP
    int pref_offset = 0;
    for (int i = 0; i < prefetch_degree; i++) {
      pref_offset += DPT_l1[cpu][signature].delta;
      champsim::block_number pf_address{cl_addr.to<uint64_t>() + static_cast<uint64_t>(pref_offset)};

      // Check if prefetch address is in same 4 KB page
      if ((champsim::page_number{pf_address} != champsim::page_number{cl_addr}) || (DPT_l1[cpu][signature].conf == -1) ||
          (DPT_l1[cpu][signature].delta == 0)) {
        // if new entry in DPT or delta is zero, break
        break;
      }

      // we are not prefetching at L2 for CPLX type, so encode delta as 0
      metadata = encode_metadata(0, CPLX_TYPE, spec_nl[cpu]);
      if (DPT_l1[cpu][signature].conf > 0) { // prefetch only when conf>0 for CPLX
        prefetch_line(champsim::address{pf_address}, fill_here, metadata);
        num_prefs++;
        SIG_DP(std::cout << pref_offset << ", ");
      }
      signature = update_sig_l1(signature, DPT_l1[cpu][signature].delta);
    }
  }

// if no prefetches are issued till now, speculatively issue a next_line prefetch
  if (num_prefs == 0 && spec_nl[cpu] == 1) { // NL IP
    champsim::block_number pf_address{cl_addr.to<uint64_t>() + 1};
    metadata = encode_metadata(1, NL_TYPE, spec_nl[cpu]);
    prefetch_line(champsim::address{pf_address}, fill_here, metadata);
    SIG_DP(std::cout << "1, ");
  }

  SIG_DP(std::cout << std::endl);

  // update the IP table entries
  trackers_l1[cpu][index].last_cl_offset = cl_offset;
  trackers_l1[cpu][index].last_page = curr_page;

  // update GHB
  // search for matching cl addr
  int ghb_index = 0;
  for (ghb_index = 0; ghb_index < NUM_GHB_ENTRIES; ghb_index++)
    if (cl_addr == champsim::block_number{ghb_l1[cpu][ghb_index]})
      break;
  // only update the GHB upon finding a new cl address
  if (ghb_index == NUM_GHB_ENTRIES) {
    for (ghb_index = NUM_GHB_ENTRIES - 1; ghb_index > 0; ghb_index--)
      ghb_l1[cpu][ghb_index] = ghb_l1[cpu][ghb_index - 1];
    ghb_l1[cpu][0] = cl_addr.to<uint64_t>();
  }

  return metadata_in;
}

uint32_t ipcp_l1d::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr,
                                         uint32_t metadata_in)
{
  (void)addr;
  (void)set;
  (void)way;
  (void)prefetch;
  (void)evicted_addr;
  return metadata_in;
}

void ipcp_l1d::prefetcher_final_stats()
{
  SIG_DP(std::cout << std::endl);
}
