// SMS2 (Spatial Memory Streaming) L2C prefetcher seed for OpenEvolve combined workflow.
// Adapted from CMU-SAFARI DPC4 submission:
// https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/uMAMA/umama/sms2
//
// Pair with fixed LRU replacement via scripts/seed_combined_checkpoint.py.

#include <algorithm>
#include <bitset>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <deque>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "cache.h"
#include "champsim.h"
#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

struct BloomFilterStub {
  bool test(uint64_t) const { return false; }
};

//=======================================================================================//
// File             : sms/bitmap.h
// Author           : Rahul Bera, SAFARI Research Group (write2bera@gmail.com)
// Date             : 19/AUG/2025
// Description      : Implements bitmap functionality required for SMS
//=======================================================================================//


#define BITMAP2_MAX_SIZE 64

typedef std::bitset<BITMAP2_MAX_SIZE> Bitmap2;

class Bitmap2Helper
{
public:
  static uint64_t value(Bitmap2 bmp, uint32_t size = BITMAP2_MAX_SIZE);
  static std::string to_string(Bitmap2 bmp, uint32_t size = BITMAP2_MAX_SIZE);
  static uint32_t count_bits_set(Bitmap2 bmp, uint32_t size = BITMAP2_MAX_SIZE);
  static uint32_t count_bits_same(Bitmap2 bmp1, Bitmap2 bmp2, uint32_t size = BITMAP2_MAX_SIZE);
  static uint32_t count_bits_diff(Bitmap2 bmp1, Bitmap2 bmp2, uint32_t size = BITMAP2_MAX_SIZE);
  static Bitmap2 rotate_left(Bitmap2 bmp, uint32_t amount, uint32_t size = BITMAP2_MAX_SIZE);
  static Bitmap2 rotate_right(Bitmap2 bmp, uint32_t amount, uint32_t size = BITMAP2_MAX_SIZE);
  static Bitmap2 compress(Bitmap2 bmp, uint32_t granularity, uint32_t size = BITMAP2_MAX_SIZE);
  static Bitmap2 decompress(Bitmap2 bmp, uint32_t granularity, uint32_t size = BITMAP2_MAX_SIZE);
  static Bitmap2 bitwise_or(Bitmap2 bmp1, Bitmap2 bmp2, uint32_t size = BITMAP2_MAX_SIZE);
  static Bitmap2 bitwise_and(Bitmap2 bmp1, Bitmap2 bmp2, uint32_t size = BITMAP2_MAX_SIZE);
};

//=======================================================================================//
// File             : sms/sms_helper.h
// Author           : Rahul Bera, SAFARI Research Group (write2bera@gmail.com)
// Date             : 19/AUG/2025
// Description      : Defines auxiliary structures to implement
//                    Spatial Memory Streaming prefetcher, ISCA'06
//=======================================================================================//




class FTEntry2
{
public:
  uint64_t page;
  uint64_t pc;
  uint32_t trigger_offset;

public:
  void reset()
  {
    page = 0xdeadbeef;
    pc = 0xdeadbeef;
    trigger_offset = 0;
  }
  FTEntry2() { reset(); }
  ~FTEntry2() {}
};

class ATEntry2
{
public:
  uint64_t page;
  uint64_t pc;
  uint32_t trigger_offset;
  Bitmap2 pattern;
  uint32_t age;

public:
  void reset()
  {
    page = pc = 0xdeadbeef;
    trigger_offset = 0;
    pattern.reset();
    age = 0;
  }
  ATEntry2() { reset(); }
  ~ATEntry2() {}
};

class PHTEntry2
{
public:
  uint64_t signature;
  Bitmap2 pattern;
  uint32_t age;

public:
  void reset()
  {
    signature = 0xdeadbeef;
    pattern.reset();
    age = 0;
  }
  PHTEntry2() { reset(); }
  ~PHTEntry2() {}
};

struct Sms2Core {
private:
  // config
  constexpr static uint32_t AT_SIZE = 48;
  constexpr static uint32_t FT_SIZE = 96;
  constexpr static uint32_t PHT_SIZE = 3072;
  constexpr static uint32_t PHT_ASSOC = 16;
  constexpr static uint32_t PHT_SETS = PHT_SIZE / PHT_ASSOC;
  constexpr static uint32_t REGION_SIZE = 2048;
  constexpr static uint32_t REGION_SIZE_LOG = 11;
  constexpr static uint32_t PREF_BUFFER_SIZE = 384;

  // internal data structures
  std::deque<FTEntry2*> filter_table;
  std::deque<ATEntry2*> acc_table;
  std::vector<std::deque<PHTEntry2*>> pht;
  uint32_t pht_sets;
  std::deque<uint64_t> pref_buffer;

  BloomFilterStub* bloom = nullptr;

  // private functions
  std::deque<FTEntry2*>::iterator search_filter_table(uint64_t page);
  std::deque<FTEntry2*>::iterator search_victim_filter_table();
  void evict_filter_table(std::deque<FTEntry2*>::iterator victim);
  void insert_filter_table(uint64_t pc, uint64_t page, uint32_t offset);

  std::deque<ATEntry2*>::iterator search_acc_table(uint64_t page);
  std::deque<ATEntry2*>::iterator search_victim_acc_table();
  void evict_acc_table(std::deque<ATEntry2*>::iterator victim);
  void update_age_acc_table(std::deque<ATEntry2*>::iterator current);
  void insert_acc_table(FTEntry2* ftentry, uint32_t offset);

  std::deque<PHTEntry2*>::iterator search_pht(uint64_t signature, uint32_t& set);
  std::deque<PHTEntry2*>::iterator search_victim_pht(int32_t set);
  void evcit_pht(int32_t set, std::deque<PHTEntry2*>::iterator victim);
  void update_age_pht(int32_t set, std::deque<PHTEntry2*>::iterator current);
  void insert_pht_table(ATEntry2* atentry);

  uint64_t create_signature(uint64_t pc, uint32_t offset);
  std::size_t generate_prefetch(uint64_t pc, uint64_t address, uint64_t page, uint32_t offset, std::vector<uint64_t>& pref_addr);
  void buffer_prefetch(std::vector<uint64_t> pref_addr);
  void issue_prefetch();

public:
 openevolve_prefetcher* host = nullptr;

 explicit Sms2Core(openevolve_prefetcher* pf) : host(pf) {}

  
  uint32_t PREF_DEGREE = 4;

  

  // champsim interface prototypes
  void prefetcher_initialize();
  uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                    uint32_t metadata_in);
  uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);
  void prefetcher_cycle_operate();
};

//=======================================================================================//
// File             : sms/bitmap.cc
// Author           : Rahul Bera, SAFARI Research Group (write2bera@gmail.com)
// Date             : 19/AUG/2025
// Description      : Implements bitmap functionality required for SMS
//=======================================================================================//



std::string Bitmap2Helper::to_string(Bitmap2 bmp, uint32_t size)
{
  // return bmp.to_string<char,std::string::traits_type,std::string::allocator_type>();
  std::stringstream ss;
  for (int32_t bit = size - 1; bit >= 0; --bit) {
    ss << bmp[bit];
  }
  return ss.str();
}

uint32_t Bitmap2Helper::count_bits_set(Bitmap2 bmp, uint32_t size)
{
  // return static_cast<uint32_t>(bmp.count());
  uint32_t count = 0;
  for (uint32_t index = 0; index < size; ++index) {
    if (bmp[index])
      count++;
  }
  return count;
}

uint32_t Bitmap2Helper::count_bits_same(Bitmap2 bmp1, Bitmap2 bmp2, uint32_t size)
{
  uint32_t count_same = 0;
  for (uint32_t index = 0; index < size; ++index) {
    if (bmp1[index] && bmp1[index] == bmp2[index]) {
      count_same++;
    }
  }
  return count_same;
}

uint32_t Bitmap2Helper::count_bits_diff(Bitmap2 bmp1, Bitmap2 bmp2, uint32_t size)
{
  uint32_t count_diff = 0;
  for (uint32_t index = 0; index < size; ++index) {
    if (bmp1[index] && !bmp2[index]) {
      count_diff++;
    }
  }
  return count_diff;
}

uint64_t Bitmap2Helper::value(Bitmap2 bmp, uint32_t size) { return bmp.to_ullong(); }

Bitmap2 Bitmap2Helper::rotate_left(Bitmap2 bmp, uint32_t amount, uint32_t size)
{
  Bitmap2 result;
  for (uint32_t index = 0; index < (size - amount); ++index) {
    result[index + amount] = bmp[index];
  }
  for (uint32_t index = 0; index < amount; ++index) {
    result[index] = bmp[index + size - amount];
  }
  return result;
}

Bitmap2 Bitmap2Helper::rotate_right(Bitmap2 bmp, uint32_t amount, uint32_t size)
{
  Bitmap2 result;
  for (uint32_t index = 0; index < size - amount; ++index) {
    result[index] = bmp[index + amount];
  }
  for (uint32_t index = 0; index < amount; ++index) {
    result[size - amount + index] = bmp[index];
  }
  return result;
}

Bitmap2 Bitmap2Helper::compress(Bitmap2 bmp, uint32_t granularity, uint32_t size)
{
  assert(size % granularity == 0);
  uint32_t index = 0;
  Bitmap2 result;
  uint32_t ptr = 0;

  while (index < size) {
    bool res = false;
    uint32_t gran = 0;
    for (gran = 0; gran < granularity; ++gran) {
      assert(index + gran < size);
      res = res | bmp[index + gran];
    }
    result[ptr] = res;
    ptr++;
    index = index + gran;
  }
  return result;
}

Bitmap2 Bitmap2Helper::decompress(Bitmap2 bmp, uint32_t granularity, uint32_t size)
{
  Bitmap2 result;
  result.reset();
  assert(size * granularity <= BITMAP2_MAX_SIZE);
  for (uint32_t index = 0; index < size; ++index) {
    if (bmp[index]) {
      uint32_t ptr = index * granularity;
      for (uint32_t count = 0; count < granularity; ++count) {
        result[ptr + count] = true;
      }
    }
  }
  return result;
}

Bitmap2 Bitmap2Helper::bitwise_or(Bitmap2 bmp1, Bitmap2 bmp2, uint32_t size)
{
  Bitmap2 result;
  for (uint32_t index = 0; index < size; ++index) {
    if (bmp1[index] || bmp2[index]) {
      result[index] = true;
    }
  }
  return result;
}

Bitmap2 Bitmap2Helper::bitwise_and(Bitmap2 bmp1, Bitmap2 bmp2, uint32_t size)
{
  Bitmap2 result;
  for (uint32_t index = 0; index < size; ++index) {
    if (bmp1[index] && bmp2[index]) {
      result[index] = true;
    }
  }
  return result;
}

//=======================================================================================//
// File             : sms/sms.cc
// Author           : Rahul Bera, SAFARI Research Group (write2bera@gmail.com)
// Date             : 19/AUG/2025
// Description      : Implements Spatial Memory Streaming prefetcher, ISCA'06
//=======================================================================================//


void Sms2Core::prefetcher_initialize()
{
  /* init PHT */
  std::deque<PHTEntry2*> d;
  pht.resize(Sms2Core::PHT_SETS, d);
}

uint32_t Sms2Core::prefetcher_cache_operate(champsim::address address, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                       uint32_t metadata_in)
{
  uint64_t addr = address.to<uint64_t>();
  uint64_t page = addr >> Sms2Core::REGION_SIZE_LOG;
  uint32_t offset = (uint32_t)((addr >> LOG2_BLOCK_SIZE) & ((1ull << (Sms2Core::REGION_SIZE_LOG - LOG2_BLOCK_SIZE)) - 1));
  std::vector<uint64_t> pref_addr;

  // cout << "pc " << hex << setw(16) << pc
  // 	<< " address " << hex << setw(16) << address
  // 	<< " page " << hex << setw(16) << page
  // 	<< " offset " << dec << setw(2) << offset
  // 	<< endl;

  auto at_index = search_acc_table(page);
  //   stats.at.lookup++;
  if (at_index != acc_table.end()) {
    /* accumulation table hit */
    // stats.at.hit++;
    (*at_index)->pattern[offset] = 1;
    update_age_acc_table(at_index);
  } else {
    /* search filter table */
    auto ft_index = search_filter_table(page);
    // stats.ft.lookup++;
    if (ft_index != filter_table.end()) {
      /* filter table hit */
      //   stats.ft.hit++;
      insert_acc_table((*ft_index), offset);
      evict_filter_table(ft_index);
    } else {
      /* filter table miss. Beginning of new generation. Issue prefetch */
      insert_filter_table(ip.to<uint64_t>(), page, offset);
      generate_prefetch(ip.to<uint64_t>(), addr, page, offset, pref_addr);
      buffer_prefetch(pref_addr);
    }
  }
  return 0;
}

void Sms2Core::prefetcher_cycle_operate() { issue_prefetch(); }

uint32_t Sms2Core::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in)
{
  return 0;
}

//=======================================================================================//
// File             : sms/sms_aux.cc
// Author           : Rahul Bera, SAFARI Research Group (write2bera@gmail.com)
// Date             : 19/AUG/2025
// Description      : Implements Spatial Memory Streaming prefetcher, ISCA'06
//=======================================================================================//


/* Functions for Filter table */
std::deque<FTEntry2*>::iterator Sms2Core::search_filter_table(uint64_t page)
{
  return std::find_if(filter_table.begin(), filter_table.end(), [page](FTEntry2* ftentry) { return (ftentry->page == page); });
}

void Sms2Core::insert_filter_table(uint64_t pc, uint64_t page, uint32_t offset)
{
  //   stats.ft.insert++;
  FTEntry2* ftentry = NULL;
  if (filter_table.size() >= Sms2Core::FT_SIZE) {
    auto victim = search_victim_filter_table();
    evict_filter_table(victim);
  }

  ftentry = new FTEntry2();
  ftentry->page = page;
  ftentry->pc = pc;
  ftentry->trigger_offset = offset;
  filter_table.push_back(ftentry);
}

std::deque<FTEntry2*>::iterator Sms2Core::search_victim_filter_table() { return filter_table.begin(); }

void Sms2Core::evict_filter_table(std::deque<FTEntry2*>::iterator victim)
{
  //   stats.ft.evict++;
  FTEntry2* ftentry = (*victim);
  filter_table.erase(victim);
  delete ftentry;
}

/* Functions for Accumulation Table */
std::deque<ATEntry2*>::iterator Sms2Core::search_acc_table(uint64_t page)
{
  return std::find_if(acc_table.begin(), acc_table.end(), [page](ATEntry2* atentry) { return (atentry->page == page); });
}

void Sms2Core::insert_acc_table(FTEntry2* ftentry, uint32_t offset)
{
  //   stats.at.insert++;
  ATEntry2* atentry = NULL;
  if (acc_table.size() >= Sms2Core::AT_SIZE) {
    auto victim = search_victim_acc_table();
    evict_acc_table(victim);
  }

  atentry = new ATEntry2();
  atentry->pc = ftentry->pc;
  atentry->page = ftentry->page;
  atentry->trigger_offset = ftentry->trigger_offset;
  atentry->pattern[ftentry->trigger_offset] = 1;
  atentry->pattern[offset] = 1;
  atentry->age = 0;
  for (uint32_t index = 0; index < acc_table.size(); ++index)
    acc_table[index]->age++;
  acc_table.push_back(atentry);
}

std::deque<ATEntry2*>::iterator Sms2Core::search_victim_acc_table()
{
  uint32_t max_age = 0;
  std::deque<ATEntry2*>::iterator it, victim;
  for (it = acc_table.begin(); it != acc_table.end(); ++it) {
    if ((*it)->age >= max_age) {
      max_age = (*it)->age;
      victim = it;
    }
  }
  return victim;
}

void Sms2Core::evict_acc_table(std::deque<ATEntry2*>::iterator victim)
{
  //   stats.at.evict++;
  ATEntry2* atentry = (*victim);
  insert_pht_table(atentry);
  acc_table.erase(victim);

  // cout << "[PHT_INSERT] pc " << hex << setw(10) << atentry->pc
  // 	<< " page " << hex << setw(10) << atentry->page
  // 	<< " offset " << dec << setw(3) << atentry->trigger_offset
  // 	<< " pattern " << BitmapHelper::to_string(atentry->pattern)
  // 	<< endl;

  delete (atentry);
}

void Sms2Core::update_age_acc_table(std::deque<ATEntry2*>::iterator current)
{
  for (auto it = acc_table.begin(); it != acc_table.end(); ++it) {
    (*it)->age++;
  }
  (*current)->age = 0;
}

/* Functions for Pattern History Table */
void Sms2Core::insert_pht_table(ATEntry2* atentry)
{
  //   stats.pht.lookup++;
  uint64_t signature = create_signature(atentry->pc, atentry->trigger_offset);

  // cout << "signature " << hex << setw(20) << signature << dec
  // 	<< " pattern " << BitmapHelper::to_string(atentry->pattern)
  // 	<< endl;

  uint32_t set = 0;
  auto pht_index = search_pht(signature, set);
  if (pht_index != pht[set].end()) {
    /* PHT hit */
    // stats.pht.hit++;
    (*pht_index)->pattern = atentry->pattern;
    update_age_pht(set, pht_index);
  } else {
    /* PHT miss */
    if (pht[set].size() >= Sms2Core::PHT_ASSOC) {
      auto victim = search_victim_pht(set);
      evcit_pht(set, victim);
    }

    // stats.pht.insert++;
    PHTEntry2* phtentry = new PHTEntry2();
    phtentry->signature = signature;
    phtentry->pattern = atentry->pattern;
    phtentry->age = 0;
    for (uint32_t index = 0; index < pht[set].size(); ++index)
      pht[set][index]->age = 0;
    pht[set].push_back(phtentry);
  }
}

std::deque<PHTEntry2*>::iterator Sms2Core::search_pht(uint64_t signature, uint32_t& set)
{
  set = (uint32_t)(signature % Sms2Core::PHT_SETS);
  return std::find_if(pht[set].begin(), pht[set].end(), [signature](PHTEntry2* phtentry) { return (phtentry->signature == signature); });
}

std::deque<PHTEntry2*>::iterator Sms2Core::search_victim_pht(int32_t set)
{
  uint32_t max_age = 0;
  std::deque<PHTEntry2*>::iterator it, victim;
  for (it = pht[set].begin(); it != pht[set].end(); ++it) {
    if ((*it)->age >= max_age) {
      max_age = (*it)->age;
      victim = it;
    }
  }
  return victim;
}

void Sms2Core::update_age_pht(int32_t set, std::deque<PHTEntry2*>::iterator current)
{
  for (auto it = pht[set].begin(); it != pht[set].end(); ++it) {
    (*it)->age++;
  }
  (*current)->age = 0;
}

void Sms2Core::evcit_pht(int32_t set, std::deque<PHTEntry2*>::iterator victim)
{
  //   stats.pht.evict++;
  PHTEntry2* phtentry = (*victim);
  pht[set].erase(victim);
  delete phtentry;
}

uint64_t Sms2Core::create_signature(uint64_t pc, uint32_t offset)
{
  uint64_t signature = pc;
  signature = (signature << (Sms2Core::REGION_SIZE_LOG - LOG2_BLOCK_SIZE));
  signature += (uint64_t)offset;
  return signature;
}

std::size_t Sms2Core::generate_prefetch(uint64_t pc, uint64_t address, uint64_t page, uint32_t offset, std::vector<uint64_t>& pref_addr)
{
  //   stats.generate_prefetch.called++;
  uint64_t signature = create_signature(pc, offset);
  uint32_t set = 0;
  auto pht_index = search_pht(signature, set);
  if (pht_index == pht[set].end()) {
    // stats.generate_prefetch.pht_miss++;
    return 0;
  }

  PHTEntry2* phtentry = (*pht_index);
  for (uint32_t index = 0; index < BITMAP2_MAX_SIZE; ++index) {
    if (phtentry->pattern[index] && offset != index) {
      uint64_t addr = (page << Sms2Core::REGION_SIZE_LOG) + (index << LOG2_BLOCK_SIZE);
      pref_addr.push_back(addr);
    }
  }
  update_age_pht(set, pht_index);
  //   stats.generate_prefetch.pref_generated += pref_addr.size();
  return pref_addr.size();
}

void Sms2Core::buffer_prefetch(std::vector<uint64_t> pref_addr)
{
  // cout << "buffering " << pref_addr.size() << " already present " << pref_buffer.size() << endl;
  uint32_t count = 0;
  for (uint32_t index = 0; index < pref_addr.size(); ++index) {
    if (pref_buffer.size() >= Sms2Core::PREF_BUFFER_SIZE) {
      break;
    }
    pref_buffer.push_back(pref_addr[index]);
    count++;
  }
  //   stats.pref_buffer.buffered += count;
  //   stats.pref_buffer.spilled += (pref_addr.size() - count);
}

void Sms2Core::issue_prefetch()
{
  // TODO: prefetch degree used to control how many prefetches were
  //       issued per cycle from this prefetcher (which is different
  //       than what I usually think of prefetch degree as). This
  //       means that these arms are less useful than I thought. Since
  //       we only have one tag check per cycle in our LLC, is it even
  //       worth having more than one prefetch issued per cycle? Can we
  //       cut down on the number of arms used since apparently degree
  //       has no bearing on what sms actually prefetches?
  uint32_t count = 0;
  while (!pref_buffer.empty() && count < Sms2Core::PREF_DEGREE) {
    champsim::address pf_addr{pref_buffer.front()};

    if (bloom && bloom->test((uint64_t) pf_addr.to<uint64_t>())) {
      pref_buffer.pop_front();
      continue;
    }

    const bool success = host->prefetch_line(pf_addr, true, 0);
    if (success) {
      pref_buffer.pop_front();
      count++;
    } else {
      break;
    }
  }
  //   stats.pref_buffer.issued += pref_addr.size();
}

namespace {

std::unique_ptr<Sms2Core> g_sms2;

} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{
  g_sms2 = std::make_unique<Sms2Core>(this);
  g_sms2->prefetcher_initialize();
}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit,
                                                         bool useful_prefetch, access_type type, uint32_t metadata_in)
{
  return g_sms2->prefetcher_cache_operate(addr, ip, cache_hit, useful_prefetch, type, metadata_in);
}

uint32_t openevolve_prefetcher::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch,
                                                      champsim::address evicted_addr, uint32_t metadata_in)
{
  return g_sms2->prefetcher_cache_fill(addr, set, way, prefetch, evicted_addr, metadata_in);
}

void openevolve_prefetcher::prefetcher_cycle_operate()
{
  g_sms2->prefetcher_cycle_operate();
}

// EVOLVE-BLOCK-END
