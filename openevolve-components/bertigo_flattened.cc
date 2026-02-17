#include <algorithm>
#include <array>
#include <bitset>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <map>
#include <queue>
#include <unordered_set>
#include <vector>

#include "cache.h"
#include "champsim.h"
#include "modules.h"

// -----------------------------------------------------------------------------
// Flattened BertiGo parameters (from bertigo_parameters.h)
// -----------------------------------------------------------------------------
#define BERTI_TABLE_SIZE (64)
#define BERTI_TABLE_DELTA_SIZE (16)
#define PC_PATH_HISTORY_SIZE (4)

#define HISTORY_TABLE_SETS (48)
#define HISTORY_TABLE_WAYS (32)

#define ENTANGLING_HASH

#define SIZE_IP_MASK (64)
#define IP_MASK (0xFFFF)
#define TIME_MASK (0xFFFF)
#define LAT_MASK (0xFFF)
#define ADDR_MASK (0xFFFFFF)
#define DELTA_MASK (12)
#define TABLE_SET_MASK (0x7)

#define CONFIDENCE_MAX (16)
#define CONFIDENCE_INC (1)
#define CONFIDENCE_INIT (1)

#define CONFIDENCE_L1 (10)
#define CONFIDENCE_L2 (6)
#define CONFIDENCE_L2R (4)

#define CONFIDENCE_MIDDLE_L1 (14)
#define CONFIDENCE_MIDDLE_L2 (12)
#define LAUNCH_MIDDLE_CONF (8)

#define MSHR_LIMIT (70)

#define BERTI_R (0x0)
#define BERTI_L1 (0x1)
#define BERTI_L2 (0x2)
#define BERTI_L2R (0x3)

// -----------------------------------------------------------------------------
// Flattened Filter (from filter.h)
// -----------------------------------------------------------------------------
namespace bertigo {

class Filter
{
private:
  static constexpr int LOG2_BLOCK = 6;
  static constexpr int LOG2_PAGE = 12;
  static constexpr uint64_t BLOCK_MASK = 0x3F;

  static constexpr uint32_t MULTICORE_SIGNAL = 0xcafe;
  bool multicore = false;

  static constexpr int NUM_SETS = 1;
  static constexpr int NUM_WAYS = 1360;
  static constexpr int LOG2_SETS = 0;
  static constexpr int TAG_BITS = 29;

  static constexpr uint64_t SET_MASK = NUM_SETS - 1;
  static constexpr uint64_t TAG_MASK = (1ULL << TAG_BITS) - 1;

  struct Entry {
    uint64_t tag = 0;
    uint64_t bitmap = 0;
  };

  std::array<std::array<Entry, NUM_WAYS>, NUM_SETS> table{};
  std::array<std::bitset<NUM_WAYS>, NUM_SETS> plru{};

  uint64_t stat_filter_hits = 0;
  uint64_t stat_filter_misses = 0;
  uint64_t stat_evictions = 0;

  uint64_t getVPN(uint64_t addr) const { return addr >> LOG2_PAGE; }
  uint32_t getSet(uint64_t vpn) const { return vpn & SET_MASK; }
  uint64_t getTag(uint64_t vpn) const { return (vpn >> LOG2_SETS) & TAG_MASK; }
  uint64_t getBlockOffset(uint64_t addr) const { return (addr >> LOG2_BLOCK) & BLOCK_MASK; }

  int getVictim(uint32_t set)
  {
    for (int way = 0; way < NUM_WAYS; way++) {
      if (!plru[set].test(way))
        return way;
    }
    plru[set].reset();
    return 0;
  }

  void touchPLRU(uint32_t set, int way)
  {
    plru[set].set(way);
    if (plru[set].all()) {
      plru[set].reset();
      plru[set].set(way);
    }
  }

  int lookup(uint32_t set, uint64_t tag)
  {
    for (int way = 0; way < NUM_WAYS; way++) {
      if (table[set][way].bitmap != 0 && table[set][way].tag == tag)
        return way;
    }
    return -1;
  }

public:
  Filter()
  {
    std::cout << "Region-Bitmap Prefetch Filter" << std::endl;
    std::cout << "  Sets: " << NUM_SETS << ", Ways: " << NUM_WAYS << " (" << (NUM_SETS * NUM_WAYS) << " entries)" << std::endl;
    std::cout << "  Tag bits: " << TAG_BITS << ", Bitmap: 64 bits/entry, PLRU: " << NUM_WAYS << " bits/set" << std::endl;
    std::cout << "  Storage: ~" << ((NUM_SETS * NUM_WAYS * 84 + NUM_SETS * NUM_WAYS) / 8) << " bytes" << std::endl;
  }

  bool shouldFilter(uint64_t addr)
  {
    if (multicore)
      return false;

    const uint64_t vpn = getVPN(addr);
    const uint32_t set = getSet(vpn);
    const int way = lookup(set, getTag(vpn));

    if (way < 0) {
      stat_filter_misses++;
      return false;
    }

    const uint64_t offset = getBlockOffset(addr);
    const bool tracked = (table[set][way].bitmap >> offset) & 1ULL;

    tracked ? stat_filter_hits++ : stat_filter_misses++;
    return tracked;
  }

  void markAccessed(uint64_t addr)
  {
    const uint64_t vpn = getVPN(addr);
    const uint32_t set = getSet(vpn);
    const uint64_t tag = getTag(vpn);
    const uint64_t offset = getBlockOffset(addr);

    int way = lookup(set, tag);

    if (way < 0) {
      for (int w = 0; w < NUM_WAYS; w++) {
        if (table[set][w].bitmap == 0) {
          way = w;
          break;
        }
      }
      if (way < 0) {
        way = getVictim(set);
        stat_evictions++;
      }
      table[set][way].tag = tag;
      table[set][way].bitmap = 0;
    }

    table[set][way].bitmap |= (1ULL << offset);
    touchPLRU(set, way);
  }

  void onEviction(uint64_t addr)
  {
    const uint64_t vpn = getVPN(addr);
    const uint32_t set = getSet(vpn);
    const int way = lookup(set, getTag(vpn));

    if (way >= 0) {
      const uint64_t offset = getBlockOffset(addr);
      table[set][way].bitmap &= ~(1ULL << offset);
    }
  }

  void printStats() const
  {
    std::cout << "REGION_BITMAP"
              << " filter_hits=" << stat_filter_hits
              << " filter_misses=" << stat_filter_misses
              << " evictions=" << stat_evictions
              << " multicore=" << (multicore ? "ON" : "OFF")
              << std::endl;
  }

  void onFill(uint32_t metadata_in)
  {
    if (metadata_in == MULTICORE_SIGNAL)
      multicore = true;
  }
};

} // namespace bertigo

// -----------------------------------------------------------------------------
// Flattened BertiGo class (header + implementation)
// -----------------------------------------------------------------------------
class BertiGo : public champsim::modules::prefetcher
{
private:
  typedef struct welford {
    uint64_t num = 0;
    float average = 0.0;
  } welford_t;

  welford_t average_latency;

  uint64_t pf_to_l1;
  uint64_t pf_to_l2;
  uint64_t pf_to_l2_bc_mshr;
  uint64_t cant_track_latency;
  uint64_t cross_page;
  uint64_t no_cross_page;
  uint64_t no_found_berti;
  uint64_t found_berti;
  uint64_t average_issued;
  uint64_t average_num;
  uint64_t filtered = 0;
  uint64_t bloom_issued = 0;

  std::array<uint64_t, PC_PATH_HISTORY_SIZE> last_pcs = {};

  typedef struct Delta {
    uint64_t conf;
    int64_t delta;
    uint8_t rpl;
    Delta() : conf(0), delta(0), rpl(BERTI_R) {};
  } delta_t;

  class LatencyTable
  {
  private:
    struct latency_table {
      uint64_t addr = 0;
      uint64_t tag = 0;
      uint64_t time = 0;
      bool pf = false;
    };

    int size;
    latency_table* latencyt;

  public:
    explicit LatencyTable(const int p_size) : size(p_size) { latencyt = new latency_table[size]; }
    ~LatencyTable() { delete[] latencyt; }

    uint8_t add(uint64_t addr, uint64_t tag, bool pf, uint64_t cycle);
    uint64_t get(uint64_t addr);
    uint64_t del(uint64_t addr);
    uint64_t get_tag(uint64_t addr);
  };

  class ShadowCache
  {
  private:
    struct shadow_cache {
      uint64_t addr = 0;
      uint64_t lat = 0;
      bool pf = false;
    };

    int sets;
    int ways;
    shadow_cache** scache;

  public:
    ShadowCache(const int p_sets, const int p_ways)
    {
      scache = new shadow_cache*[p_sets];
      for (int i = 0; i < p_sets; i++)
        scache[i] = new shadow_cache[p_ways];

      sets = p_sets;
      ways = p_ways;
    }

    ~ShadowCache()
    {
      for (int i = 0; i < sets; i++)
        delete[] scache[i];
      delete[] scache;
    }

    bool add(uint32_t set, uint32_t way, uint64_t addr, bool pf, uint64_t lat);
    bool get(uint64_t addr);
    void set_pf(uint64_t addr, bool pf);
    bool is_pf(uint64_t addr);
    uint64_t get_latency(uint64_t addr);
  };

  class HistoryTable
  {
  private:
    struct history_table {
      uint64_t tag = 0;
      uint64_t addr = 0;
      uint64_t time = 0;
    };

    const int sets = HISTORY_TABLE_SETS;
    const int ways = HISTORY_TABLE_WAYS;

    history_table** historyt;
    history_table** history_pointers;

    uint16_t get_aux(uint32_t latency, uint64_t tag, uint64_t act_addr, uint64_t* tags, uint64_t* addr, uint64_t cycle);

  public:
    HistoryTable()
    {
      history_pointers = new history_table*[sets];
      historyt = new history_table*[sets];

      for (int i = 0; i < sets; i++)
        historyt[i] = new history_table[ways];
      for (int i = 0; i < sets; i++)
        history_pointers[i] = historyt[i];
    }

    ~HistoryTable()
    {
      for (int i = 0; i < sets; i++)
        delete[] historyt[i];
      delete[] historyt;

      delete[] history_pointers;
    }

    int get_ways();
    void add(uint64_t tag, uint64_t addr, uint64_t cycle);
    uint16_t get(uint32_t latency, uint64_t tag, uint64_t act_addr, uint64_t* tags, uint64_t* addr, uint64_t cycle);
  };

  class InnerBerti
  {
  private:
    struct berti {
      std::array<delta_t, BERTI_TABLE_DELTA_SIZE> deltas;
      uint64_t conf = 0;
      uint64_t total_used = 0;
    };

    std::map<uint64_t, berti*> bertit;
    std::queue<uint64_t> bertit_queue;

    uint64_t size = 0;

    void increase_conf_tag(uint64_t tag);
    void conf_tag(uint64_t tag);
    void add(uint64_t tag, int64_t delta);

  public:
    LatencyTable* latencyt;
    ShadowCache* scache;
    HistoryTable* historyt;

    InnerBerti(uint64_t p_size, int latency_table_size, int sets, int ways) : size(p_size)
    {
      latencyt = new LatencyTable(latency_table_size);
      scache = new ShadowCache(sets, ways);
      historyt = new HistoryTable();
    }

    ~InnerBerti()
    {
      for (auto& kv : bertit)
        delete kv.second;
      delete latencyt;
      delete scache;
      delete historyt;
    }

    void find_and_update(uint64_t latency, uint64_t tag, uint64_t cycle, uint64_t line_addr);
    uint8_t get(uint64_t tag, std::vector<delta_t>& res);
    uint64_t ip_hash(uint64_t ip);
    static bool compare_rpl(delta_t a, delta_t b);
    static bool compare_greater_delta(delta_t a, delta_t b);
  };

  InnerBerti* berti;

  friend class LatencyTable;
  friend class ShadowCache;
  friend class HistoryTable;
  friend class InnerBerti;

public:
  using prefetcher::prefetcher;

  void prefetcher_initialize();
  uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                    uint32_t metadata_in);
  uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);
  void prefetcher_cycle_operate();
  void prefetcher_final_stats();

private:
  bertigo::Filter filter;
};

// -----------------------------------------------------------------------------
// LatencyTable
// -----------------------------------------------------------------------------
uint8_t BertiGo::LatencyTable::add(uint64_t addr, uint64_t tag, bool pf, uint64_t cycle)
{
  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_LATENCY_TABLE] " << __func__;
    std::cout << " addr: " << std::hex << addr << " tag: " << tag;
    std::cout << " prefetch: " << std::dec << +pf << " cycle: " << cycle;
  }

  latency_table* free = nullptr;

  for (int i = 0; i < size; i++) {
    if (latencyt[i].addr == addr) {
      if constexpr (champsim::debug_print) {
        std::cout << " line already found; find_tag: " << latencyt[i].tag;
        std::cout << " find_pf: " << +latencyt[i].pf << std::endl;
      }
      latencyt[i].pf = pf;
      latencyt[i].tag = tag;
      return latencyt[i].pf;
    }

    if (latencyt[i].tag == 0)
      free = &latencyt[i];
  }

  if (free == nullptr)
    assert(0 && "No free space latency table");

  free->addr = addr;
  free->time = cycle;
  free->tag = tag;
  free->pf = pf;

  if constexpr (champsim::debug_print)
    std::cout << " new entry" << std::endl;
  return free->pf;
}

uint64_t BertiGo::LatencyTable::del(uint64_t addr)
{
  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_LATENCY_TABLE] " << __func__;
    std::cout << " addr: " << std::hex << addr;
  }

  for (int i = 0; i < size; i++) {
    if (latencyt[i].addr == addr) {
      const uint64_t time = latencyt[i].time;

      if constexpr (champsim::debug_print) {
        std::cout << " tag: " << latencyt[i].tag;
        std::cout << " prefetch: " << std::dec << +latencyt[i].pf;
        std::cout << " cycle: " << latencyt[i].time << std::endl;
      }

      latencyt[i].addr = 0;
      latencyt[i].tag = 0;
      latencyt[i].time = 0;
      latencyt[i].pf = 0;

      return time;
    }
  }

  if constexpr (champsim::debug_print)
    std::cout << " TRANSLATION" << std::endl;
  return 0;
}

uint64_t BertiGo::LatencyTable::get(uint64_t addr)
{
  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_LATENCY_TABLE] " << __func__;
    std::cout << " addr: " << std::hex << addr << std::dec;
  }

  for (int i = 0; i < size; i++) {
    if (latencyt[i].addr == addr) {
      if constexpr (champsim::debug_print) {
        std::cout << " time: " << latencyt[i].time << std::endl;
      }
      return latencyt[i].time;
    }
  }

  if constexpr (champsim::debug_print)
    std::cout << " NOT FOUND" << std::endl;
  return 0;
}

uint64_t BertiGo::LatencyTable::get_tag(uint64_t addr)
{
  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_LATENCY_TABLE] " << __func__;
    std::cout << " addr: " << std::hex << addr;
  }

  for (int i = 0; i < size; i++) {
    if (latencyt[i].addr == addr && latencyt[i].tag) {
      if constexpr (champsim::debug_print) {
        std::cout << " tag: " << latencyt[i].tag << std::endl;
      }
      return latencyt[i].tag;
    }
  }

  if constexpr (champsim::debug_print)
    std::cout << " NOT_FOUND" << std::endl;
  return 0;
}

// -----------------------------------------------------------------------------
// ShadowCache
// -----------------------------------------------------------------------------
bool BertiGo::ShadowCache::add(uint32_t set, uint32_t way, uint64_t addr, bool pf, uint64_t lat)
{
  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_SHADOW_CACHE] " << __func__;
    std::cout << " set: " << set << " way: " << way;
    std::cout << " addr: " << std::hex << addr << std::dec;
    std::cout << " pf: " << +pf;
    std::cout << " latency: " << lat << std::endl;
  }

  scache[set][way].addr = addr;
  scache[set][way].pf = pf;
  scache[set][way].lat = lat;
  return scache[set][way].pf;
}

bool BertiGo::ShadowCache::get(uint64_t addr)
{
  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_SHADOW_CACHE] " << __func__;
    std::cout << " addr: " << std::hex << addr << std::endl;
  }

  for (int i = 0; i < sets; i++) {
    for (int ii = 0; ii < ways; ii++) {
      if (scache[i][ii].addr == addr) {
        if constexpr (champsim::debug_print) {
          std::cout << " set: " << i << " way: " << i << std::endl;
        }
        return true;
      }
    }
  }

  return false;
}

void BertiGo::ShadowCache::set_pf(uint64_t addr, bool pf)
{
  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_SHADOW_CACHE] " << __func__;
    std::cout << " addr: " << std::hex << addr << std::dec;
  }

  for (int i = 0; i < sets; i++) {
    for (int ii = 0; ii < ways; ii++) {
      if (scache[i][ii].addr == addr) {
        if constexpr (champsim::debug_print) {
          std::cout << " set: " << i << " way: " << ii;
          std::cout << " old_pf_value: " << +scache[i][ii].pf;
          std::cout << " new_pf_value: " << +pf << std::endl;
        }
        scache[i][ii].pf = pf;
        return;
      }
    }
  }

  assert((0) && "Address is must be in shadow cache");
}

bool BertiGo::ShadowCache::is_pf(uint64_t addr)
{
  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_SHADOW_CACHE] " << __func__;
    std::cout << " addr: " << std::hex << addr << std::dec;
  }

  for (int i = 0; i < sets; i++) {
    for (int ii = 0; ii < ways; ii++) {
      if (scache[i][ii].addr == addr) {
        if constexpr (champsim::debug_print) {
          std::cout << " set: " << i << " way: " << ii;
          std::cout << " pf: " << +scache[i][ii].pf << std::endl;
        }

        return scache[i][ii].pf;
      }
    }
  }

  assert((0) && "Address is must be in shadow cache");
  return 0;
}

uint64_t BertiGo::ShadowCache::get_latency(uint64_t addr)
{
  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_SHADOW_CACHE] " << __func__;
    std::cout << " addr: " << std::hex << addr << std::dec;
  }

  for (int i = 0; i < sets; i++) {
    for (int ii = 0; ii < ways; ii++) {
      if (scache[i][ii].addr == addr) {
        if constexpr (champsim::debug_print) {
          std::cout << " set: " << i << " way: " << ii;
          std::cout << " latency: " << scache[i][ii].lat << std::endl;
        }

        return scache[i][ii].lat;
      }
    }
  }

  assert((0) && "Address is must be in shadow cache");
  return 0;
}

// -----------------------------------------------------------------------------
// HistoryTable
// -----------------------------------------------------------------------------
int BertiGo::HistoryTable::get_ways() { return ways; }

void BertiGo::HistoryTable::add(uint64_t tag, uint64_t addr, uint64_t cycle)
{
  const uint16_t set = tag % HISTORY_TABLE_SETS;
  if (history_pointers[set] == &historyt[set][ways - 1]) {
    if (historyt[set][0].addr == (addr & ADDR_MASK))
      return;
  } else if ((history_pointers[set] - 1)->addr == (addr & ADDR_MASK))
    return;

  history_pointers[set]->tag = tag;
  history_pointers[set]->time = cycle & TIME_MASK;
  history_pointers[set]->addr = addr & ADDR_MASK;

  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_HISTORY_TABLE] " << __func__;
    std::cout << " tag: " << std::hex << tag << " line_addr: " << addr << std::dec;
    std::cout << " cycle: " << cycle << " set: " << set << std::endl;
  }

  if (history_pointers[set] == &historyt[set][ways - 1]) {
    history_pointers[set] = &historyt[set][0];
  } else {
    history_pointers[set]++;
  }
}

uint16_t BertiGo::HistoryTable::get_aux(uint32_t latency, uint64_t tag, uint64_t act_addr, uint64_t* tags, uint64_t* addr, uint64_t cycle)
{
  uint16_t num_on_time = 0;
  const uint16_t set = tag % HISTORY_TABLE_SETS;

  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_HISTORY_TABLE] " << __func__;
    std::cout << " tag: " << std::hex << tag << " line_addr: " << addr << std::dec;
    std::cout << " cycle: " << cycle << " set: " << set << std::endl;
  }

  if (cycle < latency)
    return num_on_time;

  cycle -= latency;

  history_table* pointer = history_pointers[set];

  do {
    if (pointer->tag == tag && pointer->time <= cycle) {
      if (pointer->addr == act_addr)
        return num_on_time;

      tags[num_on_time] = pointer->tag;
      addr[num_on_time] = pointer->addr;
      num_on_time++;
    }

    if (pointer == historyt[set]) {
      pointer = &historyt[set][ways - 1];
    } else {
      pointer--;
    }
  } while (pointer != history_pointers[set]);

  return num_on_time;
}

uint16_t BertiGo::HistoryTable::get(uint32_t latency, uint64_t tag, uint64_t act_addr, uint64_t* tags, uint64_t* addr, uint64_t cycle)
{
  act_addr &= ADDR_MASK;
  return get_aux(latency, tag, act_addr, tags, addr, cycle & TIME_MASK);
}

// -----------------------------------------------------------------------------
// InnerBerti
// -----------------------------------------------------------------------------
void BertiGo::InnerBerti::increase_conf_tag(uint64_t tag)
{
  if constexpr (champsim::debug_print)
    std::cout << "[BERTI_BERTI] " << __func__ << " tag: " << std::hex << tag << std::dec;

  if (bertit.find(tag) == bertit.end()) {
    if constexpr (champsim::debug_print)
      std::cout << " TAG NOT FOUND" << std::endl;
    return;
  }

  bertit[tag]->conf += CONFIDENCE_INC;

  if constexpr (champsim::debug_print)
    std::cout << " global_conf: " << bertit[tag]->conf;

  if (bertit[tag]->conf == CONFIDENCE_MAX) {
    for (auto& i : bertit[tag]->deltas) {
      if (i.conf > CONFIDENCE_L1)
        i.rpl = BERTI_L1;
      else if (i.conf > CONFIDENCE_L2)
        i.rpl = BERTI_L2;
      else if (i.conf > CONFIDENCE_L2R)
        i.rpl = BERTI_L2R;
      else
        i.rpl = BERTI_R;

      if constexpr (champsim::debug_print) {
        std::cout << "Delta: " << i.delta;
        std::cout << " Conf: " << i.conf << " Level: " << +i.rpl;
        std::cout << "|";
      }

      i.conf = 0;
    }

    bertit[tag]->conf = 0;
  }

  if constexpr (champsim::debug_print)
    std::cout << std::endl;
}

void BertiGo::InnerBerti::conf_tag(uint64_t tag) { increase_conf_tag(tag); }

void BertiGo::InnerBerti::add(uint64_t tag, int64_t delta)
{
  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_BERTI] " << __func__;
    std::cout << " tag: " << std::hex << tag << std::dec;
    std::cout << " delta: " << delta;
  }

  auto add_delta = [](auto p_delta, auto entry) {
    delta_t new_delta;
    new_delta.delta = p_delta;
    new_delta.conf = CONFIDENCE_INIT;
    new_delta.rpl = BERTI_R;
    auto it = std::find_if(std::begin(entry->deltas), std::end(entry->deltas), [](const auto i) { return (i.delta == 0); });
    assert(it != std::end(entry->deltas));
    *it = new_delta;
  };

  if (bertit.find(tag) == bertit.end()) {
    if constexpr (champsim::debug_print)
      std::cout << " allocating a new entry;";

    if (bertit_queue.size() > BERTI_TABLE_SIZE) {
      const uint64_t key = bertit_queue.front();
      berti* entry = bertit[key];

      if constexpr (champsim::debug_print)
        std::cout << " removing tag: " << std::hex << key << std::dec << ";";

      delete entry;

      bertit.erase(bertit_queue.front());
      bertit_queue.pop();
    }

    bertit_queue.push(tag);
    assert((bertit.size() <= BERTI_TABLE_SIZE) && "Tracking too much tags");

    berti* entry = new berti;
    entry->conf = CONFIDENCE_INC;

    add_delta(delta, entry);

    if constexpr (champsim::debug_print)
      std::cout << " confidence: " << CONFIDENCE_INIT << std::endl;

    bertit.insert(std::make_pair(tag, entry));
    return;
  }

  berti* entry = bertit[tag];

  for (auto& i : entry->deltas) {
    if (i.delta == delta) {
      i.conf += CONFIDENCE_INC;

      if (i.conf > CONFIDENCE_MAX)
        i.conf = CONFIDENCE_MAX;

      if constexpr (champsim::debug_print)
        std::cout << " confidence: " << i.conf << std::endl;

      return;
    }
  }

  const auto ssize = std::count_if(std::begin(entry->deltas), std::end(entry->deltas), [](const auto i) { return i.delta != 0; });

  if (ssize < static_cast<long>(size)) {
    add_delta(delta, entry);
    assert((std::size(entry->deltas) <= size) && "I remember too much deltas");
    return;
  }

  std::sort(std::begin(entry->deltas), std::end(entry->deltas), compare_rpl);
  if (entry->deltas.front().rpl == BERTI_R || entry->deltas.front().rpl == BERTI_L2R) {
    if constexpr (champsim::debug_print)
      std::cout << " replaced_delta: " << entry->deltas.front().delta << std::endl;
    entry->deltas.front().delta = delta;
    entry->deltas.front().conf = CONFIDENCE_INIT;
    entry->deltas.front().rpl = BERTI_R;
  }
}

uint8_t BertiGo::InnerBerti::get(uint64_t tag, std::vector<delta_t>& res)
{
  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI_BERTI] " << __func__ << " tag: " << std::hex << tag;
    std::cout << std::dec;
  }

  if (!bertit.count(tag)) {
    if constexpr (champsim::debug_print)
      std::cout << " TAG NOT FOUND" << std::endl;
    return 0;
  }

  if constexpr (champsim::debug_print)
    std::cout << std::endl;

  berti* entry = bertit[tag];

  for (auto& i : entry->deltas)
    if (i.delta != 0 && i.rpl != BERTI_R)
      res.push_back(i);

  if (res.empty() && entry->conf >= LAUNCH_MIDDLE_CONF) {
    for (auto& i : entry->deltas) {
      if (i.delta != 0) {
        delta_t new_delta;
        new_delta.delta = i.delta;
        if (i.conf > CONFIDENCE_MIDDLE_L1)
          new_delta.rpl = BERTI_L1;
        else if (i.conf > CONFIDENCE_MIDDLE_L2)
          new_delta.rpl = BERTI_L2;
        else
          continue;
        res.push_back(new_delta);
      }
    }
  }

  std::sort(std::begin(res), std::end(res), compare_greater_delta);
  return 1;
}

void BertiGo::InnerBerti::find_and_update(uint64_t latency, uint64_t tag, uint64_t cycle, uint64_t line_addr)
{
  uint64_t tags[HISTORY_TABLE_WAYS];
  uint64_t addr[HISTORY_TABLE_WAYS];

  const uint16_t num_on_time = historyt->get(latency, tag, line_addr, tags, addr, cycle);

  for (uint32_t i = 0; i < num_on_time; i++) {
    if (i == 0)
      increase_conf_tag(tag);

    line_addr &= ADDR_MASK;
    const int64_t stride = static_cast<int64_t>(line_addr - addr[i]);

    if ((std::abs(stride) < (1 << DELTA_MASK)))
      add(tags[i], stride);
  }
}

bool BertiGo::InnerBerti::compare_rpl(delta_t a, delta_t b)
{
  if (a.rpl == BERTI_R && b.rpl != BERTI_R)
    return 1;
  else if (b.rpl == BERTI_R && a.rpl != BERTI_R)
    return 0;
  else if (a.rpl == BERTI_L2R && b.rpl != BERTI_L2R)
    return 1;
  else if (b.rpl == BERTI_L2R && a.rpl != BERTI_L2R)
    return 0;
  else {
    if (a.conf < b.conf)
      return 1;
    return 0;
  }
}

bool BertiGo::InnerBerti::compare_greater_delta(delta_t a, delta_t b)
{
  if (a.rpl == BERTI_L1 && b.rpl != BERTI_L1)
    return 1;
  else if (a.rpl != BERTI_L1 && b.rpl == BERTI_L1)
    return 0;
  else {
    if (a.rpl == BERTI_L2 && b.rpl != BERTI_L2)
      return 1;
    else if (a.rpl != BERTI_L2 && b.rpl == BERTI_L2)
      return 0;
    else {
      if (a.rpl == BERTI_L2R && b.rpl != BERTI_L2R)
        return 1;
      if (a.rpl != BERTI_L2R && b.rpl == BERTI_L2R)
        return 0;
      else {
        if (std::abs(a.delta) < std::abs(b.delta))
          return 1;
        return 0;
      }
    }
  }
}

uint64_t BertiGo::InnerBerti::ip_hash(uint64_t ip)
{
#ifdef HASH_ORIGINAL
  ip = ((ip >> 1) ^ (ip >> 4));
#endif
#ifdef THOMAS_WANG_HASH_1
  ip = (ip ^ 61) ^ (ip >> 16);
  ip = ip + (ip << 3);
  ip = ip ^ (ip >> 4);
  ip = ip * 0x27d4eb2d;
  ip = ip ^ (ip >> 15);
#endif
#ifdef THOMAS_WANG_HASH_2
  ip = (ip + 0x7ed55d16) + (ip << 12);
  ip = (ip ^ 0xc761c23c) ^ (ip >> 19);
  ip = (ip + 0x165667b1) + (ip << 5);
  ip = (ip + 0xd3a2646c) ^ (ip << 9);
  ip = (ip + 0xfd7046c5) + (ip << 3);
  ip = (ip ^ 0xb55a4f09) ^ (ip >> 16);
#endif
#ifdef THOMAS_WANG_HASH_3
  ip -= (ip << 6);
  ip ^= (ip >> 17);
  ip -= (ip << 9);
  ip ^= (ip << 4);
  ip -= (ip << 3);
  ip ^= (ip << 10);
  ip ^= (ip >> 15);
#endif
#ifdef THOMAS_WANG_HASH_4
  ip += ~(ip << 15);
  ip ^= (ip >> 10);
  ip += (ip << 3);
  ip ^= (ip >> 6);
  ip += ~(ip << 11);
  ip ^= (ip >> 16);
#endif
#ifdef THOMAS_WANG_HASH_5
  ip = (ip + 0x479ab41d) + (ip << 8);
  ip = (ip ^ 0xe4aa10ce) ^ (ip >> 5);
  ip = (ip + 0x9942f0a6) - (ip << 14);
  ip = (ip ^ 0x5aedd67d) ^ (ip >> 3);
  ip = (ip + 0x17bea992) + (ip << 7);
#endif
#ifdef THOMAS_WANG_HASH_6
  ip = (ip ^ 0xdeadbeef) + (ip << 4);
  ip = ip ^ (ip >> 10);
  ip = ip + (ip << 7);
  ip = ip ^ (ip >> 13);
#endif
#ifdef THOMAS_WANG_HASH_7
  ip = ip ^ (ip >> 4);
  ip = (ip ^ 0xdeadbeef) + (ip << 5);
  ip = ip ^ (ip >> 11);
#endif
#ifdef THOMAS_WANG_NEW_HASH
  ip ^= (ip >> 20) ^ (ip >> 12);
  ip = ip ^ (ip >> 7) ^ (ip >> 4);
#endif
#ifdef THOMAS_WANG_HASH_HALF_AVALANCHE
  ip = (ip + 0x479ab41d) + (ip << 8);
  ip = (ip ^ 0xe4aa10ce) ^ (ip >> 5);
  ip = (ip + 0x9942f0a6) - (ip << 14);
  ip = (ip ^ 0x5aedd67d) ^ (ip >> 3);
  ip = (ip + 0x17bea992) + (ip << 7);
#endif
#ifdef THOMAS_WANG_HASH_FULL_AVALANCHE
  ip = (ip + 0x7ed55d16) + (ip << 12);
  ip = (ip ^ 0xc761c23c) ^ (ip >> 19);
  ip = (ip + 0x165667b1) + (ip << 5);
  ip = (ip + 0xd3a2646c) ^ (ip << 9);
  ip = (ip + 0xfd7046c5) + (ip << 3);
  ip = (ip ^ 0xb55a4f09) ^ (ip >> 16);
#endif
#ifdef THOMAS_WANG_HASH_INT_1
  ip -= (ip << 6);
  ip ^= (ip >> 17);
  ip -= (ip << 9);
  ip ^= (ip << 4);
  ip -= (ip << 3);
  ip ^= (ip << 10);
  ip ^= (ip >> 15);
#endif
#ifdef THOMAS_WANG_HASH_INT_2
  ip += ~(ip << 15);
  ip ^= (ip >> 10);
  ip += (ip << 3);
  ip ^= (ip >> 6);
  ip += ~(ip << 11);
  ip ^= (ip >> 16);
#endif
#ifdef ENTANGLING_HASH
  ip = ip ^ (ip >> 2) ^ (ip >> 5);
#endif
#ifdef FOLD_HASH
  uint64_t hash = 0;
  while (ip) {
    hash ^= (ip & IP_MASK);
    ip >>= SIZE_IP_MASK;
  }
  ip = hash;
#endif
  return ip;
}

// -----------------------------------------------------------------------------
// Cache functions
// -----------------------------------------------------------------------------
void BertiGo::prefetcher_initialize()
{
  pf_to_l1 = 0;
  pf_to_l2 = 0;
  pf_to_l2_bc_mshr = 0;
  cant_track_latency = 0;
  cross_page = 0;
  no_cross_page = 0;
  no_found_berti = 0;
  found_berti = 0;
  average_issued = 0;
  average_num = 0;
  filtered = 0;
  bloom_issued = 0;

  uint64_t latency_table_size = intern_->get_mshr_size();
  for (auto const& i : intern_->get_rq_size())
    latency_table_size += i;
  for (auto const& i : intern_->get_wq_size())
    latency_table_size += i;
  for (auto const& i : intern_->get_pq_size())
    latency_table_size += i;

  berti = new InnerBerti(BERTI_TABLE_DELTA_SIZE, latency_table_size, intern_->NUM_SET, intern_->NUM_WAY);

  std::cout << "BertiGo Prefetcher" << std::endl;
  std::cout << "BertiGo IP MASK " << std::hex << IP_MASK << std::dec << std::endl;
  std::cout << "BertiGo L1D Conf: " << std::dec << CONFIDENCE_L1 << " L2C: " << CONFIDENCE_L2 << " L2R: " << CONFIDENCE_L2R << std::endl;

  std::cout << "BertiGo Parameters" << std::endl;
  std::cout << "  SIZES: "
            << "BERTI_TABLE_SIZE=" << BERTI_TABLE_SIZE
            << " BERTI_TABLE_DELTA_SIZE=" << BERTI_TABLE_DELTA_SIZE
            << " PC_PATH_HISTORY_SIZE=" << PC_PATH_HISTORY_SIZE
            << std::endl;

  std::cout << "  HISTORY: "
            << "SETS=" << HISTORY_TABLE_SETS
            << " WAYS=" << HISTORY_TABLE_WAYS
            << std::endl;

  std::cout << std::hex;
  std::cout << "  MASKS: "
            << "SIZE_IP_MASK=" << std::dec << SIZE_IP_MASK
            << " IP_MASK=0x" << std::hex << IP_MASK
            << " TIME_MASK=0x" << TIME_MASK
            << " LAT_MASK=0x" << LAT_MASK
            << " ADDR_MASK=0x" << ADDR_MASK
            << " TABLE_SET_MASK=0x" << TABLE_SET_MASK
            << std::dec
            << " DELTA_MASK=" << DELTA_MASK
            << std::endl;

  std::cout << "  CONFIDENCE: "
            << "MAX=" << CONFIDENCE_MAX
            << " INC=" << CONFIDENCE_INC
            << " INIT=" << CONFIDENCE_INIT
            << " L1=" << CONFIDENCE_L1
            << " L2=" << CONFIDENCE_L2
            << " L2R=" << CONFIDENCE_L2R
            << " MIDDLE_L1=" << CONFIDENCE_MIDDLE_L1
            << " MIDDLE_L2=" << CONFIDENCE_MIDDLE_L2
            << " LAUNCH_MIDDLE_CONF=" << LAUNCH_MIDDLE_CONF
            << std::endl;

  std::cout << "  LIMITS: "
            << " MSHR_LIMIT=" << MSHR_LIMIT
            << std::endl;
}

uint32_t BertiGo::prefetcher_cache_operate(champsim::address address, champsim::address ip_addr, uint8_t cache_hit, bool useful_prefetch,
                                           access_type type, uint32_t metadata_in)
{
  (void)useful_prefetch;
  (void)type;

  const uint64_t addr = address.to<uint64_t>();
  const uint64_t ip = ip_addr.to<uint64_t>();
  const auto current_cycle = intern_->current_time.time_since_epoch() / intern_->clock_period;

  auto latencyt = berti->latencyt;
  auto scache = berti->scache;
  auto historyt = berti->historyt;

  const uint64_t line_addr = (addr >> LOG2_BLOCK_SIZE);

  if (line_addr == 0)
    return metadata_in;

  filter.markAccessed(addr);

  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI] operate";
    std::cout << " ip: " << std::hex << ip;
    std::cout << " full_address: " << addr;
    std::cout << " line_address: " << line_addr << std::dec << std::endl;
  }

  const uint64_t hashed_ip = berti->ip_hash(ip) & IP_MASK;

  uint64_t pc_path_raw = 0;
  for (int i = PC_PATH_HISTORY_SIZE - 1; i >= 0; i--) {
    pc_path_raw = (pc_path_raw << 3) ^ (last_pcs[i] & 0xFFFF);
  }
  pc_path_raw = (pc_path_raw << 3) ^ (ip & 0xFFFF);
  const uint64_t pc_path_hash = berti->ip_hash(pc_path_raw) & IP_MASK;

  if (!cache_hit) {
    if constexpr (champsim::debug_print)
      std::cout << "[BERTI] operate cache miss" << std::endl;

    latencyt->add(line_addr, hashed_ip, false, current_cycle);
    historyt->add(hashed_ip, line_addr, current_cycle);
    historyt->add(pc_path_hash, line_addr, current_cycle);
  } else if (cache_hit && scache->is_pf(line_addr)) {
    if constexpr (champsim::debug_print)
      std::cout << "[BERTI] operate cache hit because of pf" << std::endl;

    scache->set_pf(line_addr, false);

    uint64_t latency = scache->get_latency(line_addr);

    if (latency > LAT_MASK)
      latency = 0;

    berti->find_and_update(latency, hashed_ip, current_cycle & TIME_MASK, line_addr);
    historyt->add(hashed_ip, line_addr, current_cycle & TIME_MASK);
    berti->find_and_update(latency, pc_path_hash, current_cycle & TIME_MASK, line_addr);
    historyt->add(pc_path_hash, line_addr, current_cycle & TIME_MASK);
  } else {
    if constexpr (champsim::debug_print)
      std::cout << "[BERTI] operate cache hit" << std::endl;
  }

  std::vector<delta_t> deltas;
  std::unordered_set<int64_t> seen;

  auto add_deltas = [&](uint64_t hash) {
    std::vector<delta_t> tmp(BERTI_TABLE_DELTA_SIZE);
    berti->get(hash, tmp);
    for (auto& d : tmp) {
      if (d.delta != 0 && d.rpl != BERTI_R && seen.find(d.delta) == seen.end()) {
        deltas.push_back(d);
        seen.insert(d.delta);
      }
    }
  };

  add_deltas(hashed_ip);
  add_deltas(pc_path_hash);

  bool first_issue = true;
  for (auto i : deltas) {
    const uint64_t p_addr = (line_addr + i.delta) << LOG2_BLOCK_SIZE;
    const uint64_t p_b_addr = (p_addr >> LOG2_BLOCK_SIZE);

    if (latencyt->get(p_b_addr))
      continue;
    if (i.rpl == BERTI_R)
      continue;
    if (p_addr == 0)
      continue;

    if ((p_addr >> LOG2_PAGE_SIZE) != (addr >> LOG2_PAGE_SIZE)) {
      cross_page++;
#ifdef NO_CROSS_PAGE
      continue;
#endif
    } else
      no_cross_page++;

    if (filter.shouldFilter(p_addr)) {
      ++filtered;
      continue;
    }

    const float mshr_load = intern_->get_mshr_occupancy_ratio() * 100;

    const bool fill_this_level = (i.rpl == BERTI_L1) && (mshr_load < MSHR_LIMIT);

    if (i.rpl == BERTI_L1 && mshr_load >= MSHR_LIMIT)
      pf_to_l2_bc_mshr++;
    if (fill_this_level)
      pf_to_l1++;
    else
      pf_to_l2++;

    if (prefetch_line(champsim::address{p_addr}, fill_this_level, 0)) {
      ++bloom_issued;
      filter.markAccessed(p_addr);
      ++average_issued;
      if (first_issue) {
        first_issue = false;
        ++average_num;
      }

      if constexpr (champsim::debug_print) {
        std::cout << "[BERTI] operate prefetch delta: " << i.delta;
        std::cout << " p_addr: " << std::hex << p_addr << std::dec;
        std::cout << " this_level: " << +fill_this_level << std::endl;
      }

      if (fill_this_level) {
        if (!scache->get(p_b_addr)) {
          latencyt->add(p_b_addr, hashed_ip, true, current_cycle);
        }
      }
    }
  }

  for (int i = PC_PATH_HISTORY_SIZE - 1; i > 0; i--) {
    last_pcs[i] = last_pcs[i - 1];
  }
  last_pcs[0] = ip;

  return metadata_in;
}

uint32_t BertiGo::prefetcher_cache_fill(champsim::address address, long set, long way, uint8_t prefetch, champsim::address evicted_address,
                                        uint32_t metadata_in)
{
  filter.onFill(metadata_in);
  const uint64_t addr = address.to<uint64_t>();
  const uint64_t evicted_addr = evicted_address.to<uint64_t>();
  const auto current_cycle = intern_->current_time.time_since_epoch() / intern_->clock_period;

  auto latencyt = berti->latencyt;
  auto scache = berti->scache;

  const uint64_t line_addr = (addr >> LOG2_BLOCK_SIZE);
  const uint64_t tag = latencyt->get_tag(line_addr);
  const uint64_t cycle = latencyt->del(line_addr) & TIME_MASK;
  uint64_t latency = 0;

  if constexpr (champsim::debug_print) {
    std::cout << "[BERTI] fill addr: " << std::hex << line_addr;
    std::cout << " event_cycle: " << cycle;
    std::cout << " prefetch: " << +prefetch << std::endl;
    std::cout << " latency: " << latency << std::endl;
  }

  if (cycle != 0 && ((current_cycle & TIME_MASK) > cycle))
    latency = (current_cycle & TIME_MASK) - cycle;

  if (latency > LAT_MASK) {
    latency = 0;
    cant_track_latency++;
  } else {
    if (latency != 0) {
      if (average_latency.num == 0)
        average_latency.average = static_cast<float>(latency);
      else
        average_latency.average = average_latency.average + ((static_cast<float>(latency) - average_latency.average) / average_latency.num);
      average_latency.num++;
    }
  }

  if (evicted_addr != 0) {
    filter.onEviction(evicted_addr);
  }

  scache->add(static_cast<uint32_t>(set), static_cast<uint32_t>(way), line_addr, prefetch, latency);

  if (latency != 0 && !prefetch) {
    berti->find_and_update(latency, tag, cycle, line_addr);
  }
  return metadata_in;
}

void BertiGo::prefetcher_cycle_operate() {}

void BertiGo::prefetcher_final_stats()
{
  std::cout << "BERTI "
            << "TO_L1: " << pf_to_l1 << " TO_L2: " << pf_to_l2;
  std::cout << " TO_L2_BC_MSHR: " << pf_to_l2_bc_mshr << std::endl;

  std::cout << "BERTI AVG_LAT: ";
  std::cout << average_latency.average << " NUM_TRACK_LATENCY: ";
  std::cout << average_latency.num << " NUM_CANT_TRACK_LATENCY: ";
  std::cout << cant_track_latency << std::endl;

  std::cout << "BERTI CROSS_PAGE " << cross_page;
  std::cout << " NO_CROSS_PAGE: " << no_cross_page << std::endl;

  std::cout << "BERTI";
  std::cout << " FOUND_BERTI: " << found_berti;
  std::cout << " NO_FOUND_BERTI: " << no_found_berti << std::endl;

  std::cout << "BERTI";
  std::cout << " AVERAGE_ISSUED: " << ((1.0 * average_issued) / average_num);
  std::cout << std::endl;

  std::cout << "BLOOM FILTER";
  std::cout << " FILTERED: " << filtered;
  std::cout << " PREFETCHED: " << bloom_issued << std::endl;
}
