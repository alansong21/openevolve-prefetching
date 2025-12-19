# ChampSim Prefetcher Collection

Each subdirectory now contains exactly one prefetcher implementation: a single `.cc` file and matching header for the L1D module, plus a ChampSim config. The helper scripts in `scripts/` copy that pair into the `ChampSim` submodule, drop the matching `champsim_config.json` into place, and invoke the build/run pipeline. All baseline configs set L2C/LLC to the stock `next_line` module so only L1D differs per prefetcher.

Current entries:

- `bingo/` – Bingo L1D implementation (`scripts/run_bingo_champsim.sh`).
- `ipcp/` – IPCP L1D implementation (`scripts/run_ipcp_champsim.sh`).
- `mlop/` – MLOP L1D implementation (`scripts/run_mlop_champsim.sh`).

To add a new baseline, create `prefetchers/<name>/` with a single `{name}.cc`/`.h` pair (targeting L1D) plus a `champsim_config.json` that enables the module at L1D while pointing L2C/LLC to `"next_line"`. Model your script on the existing ones so the code is copied (not symlinked) into `ChampSim/prefetcher/` before building.
