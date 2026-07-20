#include "simulator/prefetcher_plugin.h"

#include "common/trace_entry.h"
#include "simulator/caching_device.h"

namespace dynamorio::drmemtrace {

class example_next_two_line_prefetcher_t final : public prefetcher_t {
public:
    explicit example_next_two_line_prefetcher_t(int block_size)
        : prefetcher_t(block_size)
    {
    }

    void
    prefetch(caching_device_t *cache, const memref_t &demand, bool missed) override
    {
        if (!missed)
            return;

        memref_t request = demand;
        request.data.type = TRACE_TYPE_HARDWARE_PREFETCH;
        request.data.addr += block_size_;
        cache->request(request);
        request.data.addr += block_size_;
        cache->request(request);
    }
};

class example_prefetcher_factory_t final : public prefetcher_factory_t {
public:
    prefetcher_t *
    create_prefetcher(int block_size) override
    {
        return new example_next_two_line_prefetcher_t(block_size);
    }
};

} // namespace dynamorio::drmemtrace

extern "C" uint64_t
drcachesim_prefetcher_plugin_abi_version()
{
    return dynamorio::drmemtrace::DRCACHESIM_PREFETCHER_PLUGIN_ABI_VERSION;
}

extern "C" dynamorio::drmemtrace::prefetcher_factory_t *
drcachesim_create_prefetcher_factory()
{
    return new dynamorio::drmemtrace::example_prefetcher_factory_t();
}

extern "C" void
drcachesim_destroy_prefetcher_factory(
    dynamorio::drmemtrace::prefetcher_factory_t *factory)
{
    delete factory;
}
