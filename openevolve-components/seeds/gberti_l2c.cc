// gBerti (Global Berti) L2C prefetcher seed for OpenEvolve combined workflow.
// Adapted from CMU-SAFARI DPC4 submission:
// https://github.com/CMU-SAFARI/DPC4/tree/main/submissions/gBerti/gberti
//
// Pair with fixed LRU replacement via scripts/seed_combined_checkpoint.py.

#include <algorithm>
#include <cstdint>
#include <memory>
#include <queue>
#include <tuple>
#include <unordered_map>
#include <vector>

#include "cache.h"
#include "champsim.h"
#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

/*
 * Author: Gilead Posluns
 * Date: Dec 20, 2025
 *
 * Implements Global Berti, a version of Berti that also supports spatial patterns.
 * Base berti is implemented as described in the MICRO paper, as opposed to what comes
 * with Champsim
 *
 * Berti: https://doi.org/10.1109/MICRO56248.2022.00072
 * */

#define HISTORY_TABLE_SETS 64
#define HISTORY_TABLE_WAYS 16
#define HISTORY_TABLE_TAG_MASK 0xFE0
#define HISTORY_TABLE_ADDR_MASK 0x3FFFFFC0

#define DELTA_TABLE_SIZE 64
#define NUM_DELTAS 32
#define DELTA_TABLE_TAG_MASK 0xFFF
#define MAX_DELTAS_PER_SEARCH 8
#define DELTA_BITS 13

//+6 because delta tracks lines, not addrs
//-1 bc signed
#define MAX_DELTA (1 << (DELTA_BITS + 6 - 1))

#define NO_PREF 0
#define L1_PREF 1
#define L2_PREF 2
#define REPLACE 3
#define LOW_CONFIDENCE 5
#define MED_CONFIDENCE 8
#define HIGH_CONFIDENCE 11
#define EXTREME_CONFIDENCE 80
#define MAX_PREFS 12

#define MAX_LATENCY ((1 << 12) - 1)
#define LINE_ADDR(addr) (addr >> 6)

struct history_table_entry
{
  uint64_t ip_tag; // 7 bits
  uint64_t addr; // 24 bits
  uint64_t timestamp; // 16 bits
};

struct history_table
{
  history_table_entry entries[HISTORY_TABLE_SETS][HISTORY_TABLE_WAYS];
  uint8_t fifo[HISTORY_TABLE_SETS]; // 0 bits if we use a shifter instead of a ptr
};

struct delta_table_entry
{
  uint64_t ip_tag; // 13 bits
  uint64_t ctr; // 4 bits
  int32_t delta[NUM_DELTAS]; // 13 bits each
  uint64_t coverage[NUM_DELTAS]; // 4 bits each
  uint64_t status[NUM_DELTAS]; // 2 bits each
  uint8_t coverage_increment[NUM_DELTAS]; //1 bit each
  bool has_local_delta; // 1 bit
};

struct delta_table
{
  delta_table_entry entries[DELTA_TABLE_SIZE];
  uint64_t fifo; // 6 bits
};

struct GBertiCore {
private:
  history_table histories;
  delta_table deltas;
  std::unordered_map<uint64_t, uint64_t> fetch_latencies; //12 bits each
  std::unordered_map<uint64_t, uint64_t> miss_timestamps; //16 bits each
  std::unordered_map<uint64_t, uint64_t> miss_ips; //already in MSHR/packet

  void record_access(uint64_t addr, uint64_t ip, uint64_t cycle);
  void search_for_deltas(uint64_t addr, uint64_t, uint64_t max_timely_timestamp);
  void search_for_global_deltas(uint64_t addr, uint64_t ip);
  void send_prefetches(uint64_t addr, uint64_t ip, uint64_t cycle);

  bool valid_delta(int64_t delta);

  void init_delta_table(delta_table& table);
  void init_delta_entry(delta_table_entry& entry);
  void init_history_entry(history_table_entry& entry);
  void init_history_table(history_table& table);

public:
  openevolve_prefetcher* host = nullptr;

  explicit GBertiCore(openevolve_prefetcher* pf) : host(pf) {}

  void prefetcher_initialize();
  uint32_t prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                    uint32_t metadata_in);
  uint32_t prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in);
  void prefetcher_cycle_operate() {}
};

#define DEBUG(args...) //printf(args)
#define LOG(args...) //printf(args)

void GBertiCore::prefetcher_initialize()
{
  init_history_table(histories);
  init_delta_table(deltas);
}

uint32_t GBertiCore::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch, access_type type,
                                             uint32_t metadata_in)
{
  DEBUG("Prefetcher cache operate: addr= %lx ip=%lx\n", addr.to<uint64_t>(), ip.to<uint64_t>());
  LOG("Access %lx by ip %lx: CACHE %s\n", addr.to<uint64_t>(), ip.to<uint64_t>(), (cache_hit) ? "HIT" : "MISS");
  uint64_t cycle = host->intern_->current_time.time_since_epoch() / host->intern_->clock_period;
  if (cache_hit == 0)
  {
    miss_timestamps[LINE_ADDR(addr.to<uint64_t>())] = cycle;
    miss_ips[addr.to<uint64_t>()] = ip.to<uint64_t>();
  }
  if (cache_hit && fetch_latencies[LINE_ADDR(addr.to<uint64_t>())] > 0)
  {
    search_for_deltas(addr.to<uint64_t>(), ip.to<uint64_t>(), cycle-fetch_latencies[LINE_ADDR(addr.to<uint64_t>())]);
    fetch_latencies[LINE_ADDR(addr.to<uint64_t>())] = 0;
  }
  send_prefetches(addr.to<uint64_t>(), ip.to<uint64_t>(), cycle);
  search_for_global_deltas(addr.to<uint64_t>(), ip.to<uint64_t>());
  record_access(addr.to<uint64_t>(), ip.to<uint64_t>(), cycle);
  return 0;
}

uint32_t GBertiCore::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch, champsim::address evicted_addr, uint32_t metadata_in)
{
  DEBUG("Prefetcher cache fill: addr= %lx evicted=%lx\n", addr.to<uint64_t>(), evicted_addr.to<uint64_t>());
  LOG("Fill %lx evicting %lx %s\n", addr.to<uint64_t>(), evicted_addr.to<uint64_t>(), fetch_latencies[LINE_ADDR(evicted_addr.to<uint64_t>())] > 0 ? "USELESS" : "");
  uint64_t cycle = host->intern_->current_time.time_since_epoch() / host->intern_->clock_period;
  uint64_t latency = cycle - miss_timestamps[LINE_ADDR(addr.to<uint64_t>())];
  fetch_latencies.erase(LINE_ADDR(evicted_addr.to<uint64_t>()));
  miss_timestamps.erase(LINE_ADDR(addr.to<uint64_t>()));
  if (latency > MAX_LATENCY) latency = 0;
  if (!prefetch && latency > 0)
  {
    search_for_deltas(addr.to<uint64_t>(), miss_ips[addr.to<uint64_t>()], cycle-latency);
    miss_ips.erase(addr.to<uint64_t>());
  }
  else
  {
    fetch_latencies[LINE_ADDR(addr.to<uint64_t>())] = latency;
  }
  return 0;
}

void GBertiCore::record_access(uint64_t addr, uint64_t ip, uint64_t cycle)
{
  DEBUG("record_access: addr= %lx ip=%lx\n", addr, ip);
  uint32_t set = ip % HISTORY_TABLE_SETS;
  uint64_t tag = ip & HISTORY_TABLE_TAG_MASK;
  uint32_t way = histories.fifo[set];
  uint32_t prev_way = (way + HISTORY_TABLE_WAYS - 1) % HISTORY_TABLE_WAYS;
  history_table_entry& prev_entry = histories.entries[set][prev_way];
  if (prev_entry.ip_tag == tag && prev_entry.addr == (addr & HISTORY_TABLE_ADDR_MASK))
  {
    DEBUG("\t Duplicates last access, dropping\n");
    return;
  }
  histories.fifo[set] = (way + 1) % HISTORY_TABLE_WAYS;
  history_table_entry& entry = histories.entries[set][way];
  entry.addr = addr & HISTORY_TABLE_ADDR_MASK;
  entry.ip_tag = tag;
  entry.timestamp = cycle;
}

size_t insert_delta(int64_t delta, delta_table_entry& entry)
{
  int repl = -1;
  int field = 0;
  for (; field < NUM_DELTAS; field++)
  {
    if (delta == entry.delta[field])
    {
      entry.coverage[field] += entry.coverage_increment[field];
      entry.coverage_increment[field] = 0;
      DEBUG(" Found in delta #%d\n", field);
      break;
    }
    if ((entry.status[field] == NO_PREF || entry.status[field] == REPLACE) &&
        repl == -1 && entry.coverage[field] == 0)
    {
      repl = field;
    }
  }
  if (field == NUM_DELTAS && repl != -1)
  {
    entry.delta[repl] = delta;
    entry.coverage[repl] = 1;
    entry.coverage_increment[repl] = 0;
    entry.status[repl] = NO_PREF;
    DEBUG(" Replacing delta #%d\n", repl);
    return repl;
  }
  return field;
}

size_t find_delta_entry(uint64_t ip_tag, delta_table& deltas)
{
  size_t delta_entry = 0;
  while (delta_entry < DELTA_TABLE_SIZE)
  {
    if ((ip_tag & DELTA_TABLE_TAG_MASK) == deltas.entries[delta_entry].ip_tag) break;
    delta_entry++;
  }
  return delta_entry;
}

void GBertiCore::search_for_deltas(uint64_t addr, uint64_t ip, uint64_t max_timely_timestamp)
{
  DEBUG("Search for deltas: addr= %lx ip=%lx\n", addr, ip);
  if (!ip) return;
  uint32_t set = ip % HISTORY_TABLE_SETS;
  uint64_t tag = ip & HISTORY_TABLE_TAG_MASK;
  std::vector<int64_t> timely_deltas;
  bool has_local_delta = false;
  for (int way = histories.fifo[set] + HISTORY_TABLE_WAYS-1; way >= histories.fifo[set] && timely_deltas.size() <= MAX_DELTAS_PER_SEARCH; way--)
  {
    history_table_entry& entry = histories.entries[set][way % HISTORY_TABLE_WAYS];
    if (entry.timestamp <= max_timely_timestamp)
    {
      int64_t delta = (int64_t)(addr & HISTORY_TABLE_ADDR_MASK) - (int64_t)entry.addr;
      if (valid_delta(delta)) timely_deltas.push_back(delta);
    }
  }
  DEBUG("\tFound deltas:");
  for (int64_t d : timely_deltas)
  {
    DEBUG(" %ld", d);
  }
  size_t delta_entry = find_delta_entry(ip & DELTA_TABLE_TAG_MASK, deltas);
  if (delta_entry == DELTA_TABLE_SIZE)
  { //no entry found for this ip, replace the oldest
    delta_entry = deltas.fifo;
    deltas.fifo = (delta_entry+1) % DELTA_TABLE_SIZE;
    init_delta_entry(deltas.entries[delta_entry]);
    deltas.entries[delta_entry].ip_tag = (ip & DELTA_TABLE_TAG_MASK);
  }
  delta_table_entry& entry = deltas.entries[delta_entry];
  DEBUG("\nDelta entry %d:", delta_entry);
  entry.ctr++;
  for (int64_t delta: timely_deltas)
  {
    size_t field = insert_delta(delta, entry);
    if (field != NUM_DELTAS && (entry.status[field] != NO_PREF || entry.coverage[field] >= LOW_CONFIDENCE))
    {
      has_local_delta = true;
    }
  }
  entry.has_local_delta = has_local_delta;
  for (int i = 0; i < NUM_DELTAS; i++)
  {
    entry.coverage_increment[i] = 1;
    if (entry.coverage[i] > 0)
    {
      DEBUG("(%ld:%d)", entry.delta[i], entry.coverage[i]);
    }
  }
  DEBUG(" /%d\n", entry.ctr);
  if (entry.ctr == 16)
  {
    entry.ctr = 0;
    uint32_t pref_count = 0;
    for (int i = 0; i < NUM_DELTAS; i++)
    {
      if (pref_count < MAX_PREFS){ //Ended up just prefetching everything to L1 to avoid repeat prefetches
        if (entry.coverage[i] >= HIGH_CONFIDENCE)
        {
          DEBUG("\tWill prefetch delta %ld to L1\n", entry.delta[i]);
          entry.status[i] = L1_PREF;
        }
        else if (entry.coverage[i] >= MED_CONFIDENCE)
        {
          DEBUG("\tWill prefetch delta %ld to L2\n", entry.delta[i]);
          entry.status[i] = L2_PREF;
        }
        else if (entry.coverage[i] >= LOW_CONFIDENCE)
        {
          DEBUG("\tWill prefetch delta %ld to L2 or replace\n", entry.delta[i]);
          entry.status[i] = REPLACE;
        }
        else entry.status[i] = NO_PREF;
      }
      else entry.status[i] = NO_PREF;
      entry.coverage[i] = 0;
      if (entry.status[i] != NO_PREF) pref_count++;
    }
  }
}

void GBertiCore::search_for_global_deltas(uint64_t addr, uint64_t ip)
{
  size_t delta_entry = find_delta_entry(ip & DELTA_TABLE_TAG_MASK, deltas);
  if (delta_entry == DELTA_TABLE_SIZE || !deltas.entries[delta_entry].has_local_delta)
  {
    DEBUG("Searching for global deltas\n");
    for (int set = 0; set < HISTORY_TABLE_SETS; set++)
    {
      int way = (histories.fifo[set] + HISTORY_TABLE_WAYS - 1) % HISTORY_TABLE_WAYS;
      if (histories.entries[set][way].ip_tag == (ip & HISTORY_TABLE_TAG_MASK)) continue;
      delta_entry = find_delta_entry(histories.entries[set][way].ip_tag | set, deltas);
      if (delta_entry == DELTA_TABLE_SIZE) continue;
      if (deltas.entries[delta_entry].has_local_delta) continue;
      int64_t d = (int64_t)(addr & HISTORY_TABLE_ADDR_MASK) - (int64_t)histories.entries[set][way].addr;
      if (!valid_delta(d)) continue;
      DEBUG("Inserting delta %ld into delta entry %d\n", d, delta_entry);
      insert_delta(d, deltas.entries[delta_entry]);
    }
  }
}

void GBertiCore::send_prefetches(uint64_t addr, uint64_t ip, uint64_t cycle)
{
  DEBUG("Send Prefetches: addr= %lx ip=%lx\n", addr, ip);
  uint64_t tag = ip & DELTA_TABLE_TAG_MASK;
  std::priority_queue<std::tuple<uint64_t, uint64_t>> prefetches;
  for (int entry = 0; entry < DELTA_TABLE_SIZE; entry++)
  {
    if (tag == deltas.entries[entry].ip_tag)
    {
      for (int delta = 0; delta < NUM_DELTAS; delta++)
      {
        int64_t d = deltas.entries[entry].delta[delta];
        if (d == 0) continue; //sanity check in case of overflow
        if (d > 0 && addr + d <= addr) continue;
        if (d < 0 && addr + d >= addr) continue;
        uint64_t target_addr = addr + d;
        if (miss_timestamps.count(LINE_ADDR(target_addr)) || fetch_latencies.count(LINE_ADDR(target_addr))) continue;
        if (deltas.entries[entry].status[delta] != NO_PREF)
        {
          prefetches.push({deltas.entries[entry].coverage[delta], target_addr});
        }
      }
      std::size_t pq_size = host->intern_->get_pq_size()[0];
      std::size_t pq_occupancy = host->intern_->get_pq_occupancy()[0];
      pq_size = std::min(pq_size, pq_occupancy + MAX_DELTAS_PER_SEARCH);
      while (pq_occupancy < pq_size && prefetches.size() > 0)
      {
        uint64_t target_addr = std::get<1>(prefetches.top());
        prefetches.pop();
        if (miss_timestamps.count(LINE_ADDR(target_addr)) || fetch_latencies.count(LINE_ADDR(target_addr))) continue;
        miss_timestamps[LINE_ADDR(target_addr)] = cycle;
        host->prefetch_line(champsim::address{target_addr}, true, 0);
        LOG("Prefetch %lx to L1\n", target_addr);
        pq_occupancy++;
      }
      return;
    }
  }
}

bool GBertiCore::valid_delta(int64_t delta)
{
  return delta < MAX_DELTA && delta >= -MAX_DELTA;
}

void GBertiCore::init_delta_table(delta_table& table)
{
  table.fifo = 0;
  for (int i = 0; i < DELTA_TABLE_SIZE; i++)
  {
    init_delta_entry(table.entries[i]);
  }
}

void GBertiCore::init_delta_entry(delta_table_entry& entry)
{
  entry.ip_tag = 0;
  entry.ctr = 0;
  entry.has_local_delta = false;
  for (int i = 0; i < NUM_DELTAS; i++)
  {
    entry.delta[i] = 0;
    entry.coverage[i] = 0;
    entry.status[i] = NO_PREF;
    entry.coverage_increment[i] = 1;
  }
}

void GBertiCore::init_history_entry(history_table_entry& entry)
{
  entry.addr = 0;
  entry.timestamp = 0;
}

void GBertiCore::init_history_table(history_table& table)
{
  for (int set = 0; set < HISTORY_TABLE_SETS; set++)
  {
    table.fifo[set] = 0;
    for (int way = 0; way < HISTORY_TABLE_WAYS; way++)
    {
      init_history_entry(table.entries[set][way]);
    }
  }
}

namespace {

std::unique_ptr<GBertiCore> g_gberti;

} // namespace

void openevolve_prefetcher::prefetcher_initialize()
{
  g_gberti = std::make_unique<GBertiCore>(this);
  g_gberti->prefetcher_initialize();
}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit,
                                                         bool useful_prefetch, access_type type, uint32_t metadata_in)
{
  return g_gberti->prefetcher_cache_operate(addr, ip, cache_hit, useful_prefetch, type, metadata_in);
}

uint32_t openevolve_prefetcher::prefetcher_cache_fill(champsim::address addr, long set, long way, uint8_t prefetch,
                                                      champsim::address evicted_addr, uint32_t metadata_in)
{
  return g_gberti->prefetcher_cache_fill(addr, set, way, prefetch, evicted_addr, metadata_in);
}

void openevolve_prefetcher::prefetcher_cycle_operate()
{
  g_gberti->prefetcher_cycle_operate();
}

// EVOLVE-BLOCK-END
