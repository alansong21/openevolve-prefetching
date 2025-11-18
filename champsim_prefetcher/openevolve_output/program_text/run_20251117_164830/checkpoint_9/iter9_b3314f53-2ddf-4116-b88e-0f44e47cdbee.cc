// Baseline ChampSim-compatible prefetcher used by OpenEvolve.
// ChampSim/prefetcher/openevolve_prefetcher simply #include's this file, so all
// edits can live outside the ChampSim submodule. Modify the logic in
// prefetcher_cache_operate/prefetcher_cycle_operate to explore new ideas.

#include "openevolve_prefetcher.h"

// EVOLVE-BLOCK-START

void openevolve_prefetcher::prefetcher_initialize()
{
    last_access_type = access_type::INVALID; // Initialize last access type
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

    if ((type == access_type::LOAD || type == access_type::STORE) && last_cache_hit) { // Prefetch on LOAD or STORE if last access was a hit
        champsim::block_number current_block{addr};
        champsim::block_number last_access = current_block;
        static champsim::block_number last_last_access = current_block;
        static int64_t strides[5] = {0}; // Increase the number of strides to consider

        if (last_last_access != 0) {
            strides[0] = current_block - last_last_access; // First inferred stride
            strides[1] = last_access - last_last_access; // Second inferred stride
            strides[2] = current_block - last_access; // Third inferred stride
        }

        for (int i = 0; i < 5; ++i) { // Prefetch next lines based on multiple inferred strides
            if (strides[i] != 0) {
                for (int j = 1; j <= 5; ++j) { // Increase the number of lookahead prefetches
                    champsim::block_number next_block = current_block + (j * strides[i]);
                    prefetch_line(champsim::address{next_block}, true, metadata_in);
                }
            }
        }

        last_last_access = last_access; // Update last accesses for future stride calculations
        last_access_type = type; // Track the type of last access
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
        last_cache_hit = true; // Update last cache hit status
        prefetch_degree = (last_cache_hit) ? std::min(prefetch_degree + 2, MAX_PREFETCH_DEGREE) : std::max(prefetch_degree - 1, MIN_PREFETCH_DEGREE); // Adjust prefetch degree more dynamically
    } else {
        last_cache_hit = false; // Update last cache miss status
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
