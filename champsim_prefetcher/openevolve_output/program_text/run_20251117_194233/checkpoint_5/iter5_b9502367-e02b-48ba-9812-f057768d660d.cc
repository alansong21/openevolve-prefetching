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
    static std::vector<champsim::block_number> access_history; // Maintain access history for stride inference
    const int MAX_HISTORY_SIZE = 16; // Limit history size for performance
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
        access_history.push_back(current_block); // Add current access to history
        if (access_history.size() > MAX_HISTORY_SIZE) {
            access_history.erase(access_history.begin()); // Maintain fixed size
        }
        static champsim::block_number last_last_access = current_block;
        static int64_t stride = 0;

        if (last_last_access != 0) {
            // Infer longer strides based on access history
            for (size_t i = 1; i < access_history.size(); ++i) {
                strides[i - 1] = access_history[access_history.size() - i] - access_history[access_history.size() - i - 1];
            }
            // Fill remaining strides with zero if less than 3
            for (size_t i = access_history.size() - 1; i < 3; ++i) {
                strides[i] = 0;
            }
        }

        for (int i = 0; i < 3; ++i) { // Prefetch next lines based on inferred strides
            if (strides[i] != 0) {
                for (int j = 1; j <= 5; ++j) { // Increase lookahead to 5
            if (strides[i] != 0) {
                for (int j = 1; j <= 3; ++j) {
                    champsim::block_number next_block = current_block + (j * strides[i]);
                    prefetch_line(champsim::address{next_block}, true, metadata_in);
                }
            }
        }

        last_last_access = last_access; // Update last accesses
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
        if (prefetch) {
            last_cache_hit = true; // Update last cache hit status
            prefetch_degree = std::min(prefetch_degree + (useful_prefetch ? 1 : 0), MAX_PREFETCH_DEGREE); // Increase prefetch degree adaptively
        } else {
            last_cache_hit = false; // Update last cache miss status
            prefetch_degree = std::max(prefetch_degree - 1, MIN_PREFETCH_DEGREE); // Decrease prefetch degree after a miss
        }
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
