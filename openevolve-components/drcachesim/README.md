# drcachesim prefetcher plugins

The pinned `DynamoRIO/` source is locally extended so the normal drmemtrace
launcher can load an external data prefetcher:

```bash
./scripts/build_drcachesim_prefetcher_plugin.sh

DynamoRIO/build/clients/bin64/drmemtrace_launcher \
  -infile /path/to/drmemtrace.trace.gz \
  -simulator_type cache \
  -prefetcher_plugin \
    openevolve-components/drcachesim/example_prefetcher_plugin.so
```

`-prefetcher_plugin` implies `-data_prefetcher custom`.

## Plugin ABI

A plugin subclasses `dynamorio::drmemtrace::prefetcher_t`, provides a
`prefetcher_factory_t`, and exports these C-linkage symbols:

```cpp
extern "C" uint64_t drcachesim_prefetcher_plugin_abi_version();
extern "C" dynamorio::drmemtrace::prefetcher_factory_t*
drcachesim_create_prefetcher_factory();
extern "C" void
drcachesim_destroy_prefetcher_factory(
    dynamorio::drmemtrace::prefetcher_factory_t*);
```

The ABI version must equal
`DRCACHESIM_PREFETCHER_PLUGIN_ABI_VERSION` from
`simulator/prefetcher_plugin.h`.

The plugin is a C++ ABI: compile it against the same pinned DynamoRIO headers
and a compatible C++ runtime. Rebuild plugins whenever the DynamoRIO revision
changes. After a DynamoRIO install, the public headers live under
`include/drmemtrace/` (`prefetcher.h`, `prefetcher_plugin.h`,
`caching_device.h`). The helper script currently builds against the source tree
plus `libdrmemtrace_simulator.a`.

`example_prefetcher_plugin.cpp` issues the next two cache lines after each
demand miss and is intended as a build/load example, not an optimized policy.
