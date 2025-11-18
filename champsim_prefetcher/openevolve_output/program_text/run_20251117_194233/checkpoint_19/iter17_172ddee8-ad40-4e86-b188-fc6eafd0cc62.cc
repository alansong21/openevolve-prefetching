// Baseline ChampSim-compatible prefetcher used by OpenEvolve.
// ChampSim/prefetcher/openevolve_prefetcher simply #include's this file, so all
// edits can live outside the ChampSim submodule. Modify the logic in
// prefetcher_cache_operate/prefetcher_cycle_operate to explore new ideas.

#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

void openevolve_prefetcher::prefetcher_initialize()
{
    last_access_type = access_type::INVALID; // Initialize last access type
    std::vector<champsim::block_number> access_history; // Maintain a history of accesses for stride inference
    std::vector<int64_t> strides; // Store inferred strides
    last_cache_hit = false; // Initialize last cache hit status
{
}

uint32_t openevolve_prefetcher::prefetcher_cache_operate(champsim::address addr, champsim::address ip, uint8_t cache_hit, bool useful_prefetch,
                                                         access_type type, uint32_t metadata_in)
{
    (void)ip;
    (void)cache_hit;
    (void)useful_prefetch;
    (void)type;

    if ((type == access_type::LOAD && last_cache_hit) || (type == access_type::STORE && last_access_type == access_type::LOAD && !last_cache_hit)) { // Refine prefetch conditions
        // Update access history for stride inference
        champsim::block_number current_block{addr};
        champsim::block_number last_access = current_block;
        static champsim::block_number last_last_access = current_block;
        static int64_t stride = 0;

        access_history.push_back(current_block);
        if (access_history.size() >= 5) { // Increase history size for better stride inference
            for (size_t i = 1; i < access_history.size(); ++i) {
                strides.push_back(access_history[i] - access_history[i-1]); // Infer strides from access history
            }
        }
        }

        for (size_t i = 0; i < strides.size(); ++i) { // Prefetch next lines based on multiple inferred strides
            if (strides[i] != 0) {
                for (int j = 1; j <= 5; ++j) { // Issue more lookahead prefetches
                    champsim::block_number next_block = current_block + (j * strides[i]);
                    prefetch_line(champsim::address{next_block}, true, metadata_in);
                }
            }
            if (strides[i] != 0) {
                for (int j = 1; j <= 3; ++j) {
                    champsim::block_number next_block = current_block + (j * strides[i]);
                    prefetch_line(champsim::address{next_block}, true, metadata_in);
                }
            }
        }

        last_access_type = type; // Update last access type
    }
{
  (void)ip;
  (void)cache_hit;
  (void)useful_prefetch;
  (void)type;

  champsim::block_number current_block{addr};
  champsim::block_number next_block{current_block + 1};
  prefetch_line(champsim::address{next_block}, true, metadata_in);
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

    if (prefetch) { 
        prefetch_degree = std::min(prefetch_degree + (last_access_type == access_type::LOAD ? 2 : 1), MAX_PREFETCH_DEGREE); // Increase more aggressively after LOAD hits
        last_cache_hit = true; // Update last cache hit status
        prefetch_degree = std::min(prefetch_degree + 1, MAX_PREFETCH_DEGREE); // Increase prefetch degree after a hit
    } else {
        last_cache_hit = false; // Update last cache miss status
        prefetch_degree = std::max(prefetch_degree - (last_access_type == access_type::STORE ? 2 : 1), MIN_PREFETCH_DEGREE); // Decrease more aggressively after STORE misses
        prefetch_degree = std::max(prefetch_degree - 1, MIN_PREFETCH_DEGREE); // Decrease prefetch degree after a miss
    }

    return metadata_in;
{
  (void)addr;
  (void)set;
  (void)way;
  (void)prefetch;
  (void)evicted_addr;

  return metadata_in;
}

void openevolve_prefetcher::prefetcher_cycle_operate()
{
}

// EVOLVE-BLOCK-END
