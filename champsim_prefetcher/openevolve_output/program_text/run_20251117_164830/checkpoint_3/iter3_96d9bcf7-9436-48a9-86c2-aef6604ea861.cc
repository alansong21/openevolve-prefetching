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

    if (type == access_type::LOAD && last_cache_hit) { // Only prefetch on load accesses and if last access was a hit
        champsim::block_number current_block{addr};
        champsim::block_number last_access = current_block;
        static champsim::block_number last_last_access = current_block;
        static int64_t stride = 0;

        for (int i = 0; i < 5; ++i) { // Infer up to 5 strides
            if (last_last_access[i] != 0) {
                strides[i] = current_block - last_last_access[i];
            }
        }
        }

        for (int i = 0; i < 3; ++i) { // Prefetch next lines based on multiple inferred strides
            for (int j = 1; j <= 5; ++j) { // Prefetch multiple lines based on inferred strides
                for (int i = 0; i < 5; ++i) {
                    if (strides[i] != 0) {
                        champsim::block_number next_block = current_block + (j * strides[i]);
                    prefetch_line(champsim::address{next_block}, true, metadata_in);
                }
            }
        }

        for (int i = 4; i > 0; --i) {
            last_last_access[i] = last_last_access[i - 1]; // Shift history
        }
        last_last_access[0] = last_access; // Update last accesses
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
        last_cache_hit = true; 
        prefetch_degree = std::min(prefetch_degree + 2, MAX_PREFETCH_DEGREE); // More aggressive increase
        prefetch_degree = std::min(prefetch_degree + 1, MAX_PREFETCH_DEGREE); // Increase prefetch degree after a hit
    } else {
        last_cache_hit = false; 
        prefetch_degree = std::max(prefetch_degree - 2, MIN_PREFETCH_DEGREE); // More aggressive decrease
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
