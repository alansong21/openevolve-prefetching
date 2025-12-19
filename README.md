# OpenEvolve + ChampSim Prefetcher

This repository wires OpenEvolve into the ChampSim simulator so evolution edits a C++ prefetcher (`openevolve-components/initial_program.cc`) and evaluates it on ChampSim traces.

## Setup
- Requires Ubuntu/Debian with `sudo` for packages (`pkg-config`, build tools, CMake, Ninja, git, curl, unzip, tar, zip, xz-utils).
- Create and activate a Python virtual environment at the repo root:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```
- Install Python dependencies inside the activated venv:
  ```bash
  pip install -r requirements.txt
  ```
- Run both setup scripts in order:
  1) Environment bootstrap (Miniforge/conda + OpenEvolve):
  ```bash
  ./setup_shim
  ```
  This installs Miniforge/conda, creates env `oe311`, installs `openevolve` via pip, initializes submodules, and tries to install ChampSim deps via vcpkg under `~/alphaEvolveProject/ChampSim` if present.
  2) ChampSim build and trace download:
  ```bash
  ./setup_champsim.sh
  ```
  This installs system dependencies, downloads a sample trace (`400.perlbench-41B...xz`), updates the `ChampSim` submodule, wires the thin `openevolve_prefetcher` shim in `ChampSim/prefetcher`, bootstraps vcpkg, runs `config.sh` with `openevolve-components/champsim_config.json`, and builds ChampSim.

## Run the evolutionary loop
- Set your LLM API key (defaults to OpenAI-compatible):
  ```bash
  export OPENAI_API_KEY=<your-api-key>
  ```
- Launch OpenEvolve from the repo root:
  ```bash
  python openevolve/openevolve-run.py \
    openevolve-components/initial_program.cc \
    openevolve-components/evaluator.py \
    --config openevolve-components/config.yaml \
    --iterations 5
  ```
- Each iteration overwrites `openevolve-components/initial_program.cc`, rebuilds `ChampSim`, runs the configured trace, and reports IPC as the score. Logs and artifacts land under `openevolve-components/openevolve_output/`.

## Useful notes
- The ChampSim module `ChampSim/prefetcher/openevolve_prefetcher` is a two-line shim that includes the shared sources under `openevolve-components/`, keeping the submodule clean.
- Swap the trace by setting `CHAMPSIM_TRACE` to an absolute path to a `.champsimtrace*.xz`. Other knobs (jobs, timeouts, instruction counts) are documented in `openevolve-components/README.md`.
- Ready-to-run Bingo, IPCP, and MLOP wrappers live under `prefetchers/{bingo,ipcp,mlop}/` with scripts in `scripts/` for copying them into the ChampSim submodule.
- Quick API sanity check:
  ```bash
  python smoke.py
  ```
