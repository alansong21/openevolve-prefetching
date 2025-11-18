# ChampSim Prefetcher Evolution

This example wires OpenEvolve into the ChampSim simulator so that each evolution
step edits a C++ prefetcher (`initial_program.cc`). The evaluator keeps the source
outside both submodules, rebuilds ChampSim, runs a trace, and ranks the program
by the resulting IPC.

## Prerequisites

1. Build ChampSim and download at least one trace:

   ```bash
   ./setup_champsim.sh
   ```

   The script installs dependencies, downloads `400.perlbench-41B...xz`, and runs
   `./config.sh champsim_prefetcher/champsim_config.json`
   so the new `openevolve_prefetcher` module is enabled at L2.

2. Export your API key for whatever LLM you list in `config.yaml` (defaults to
   OpenAI-style models):

   ```bash
   export OPENAI_API_KEY=sk-...
   ```

3. (Optional) Override defaults via environment variables:

   * `CHAMPSIM_TRACE` – absolute path to a `.champsimtrace*.xz` file.
   * `CHAMPSIM_JOBS` – `make -j` fan-out when rebuilding.
   * `CHAMPSIM_SIM_INSTR` / `CHAMPSIM_WARMUP_INSTR` – instruction counts passed
     to the binary.
   * `CHAMPSIM_TIMEOUT` – seconds allowed for each ChampSim run.
   * `CHAMPSIM_STREAM_LOGS` – set to `false` to suppress build/run logs printed to the console.
   * `CHAMPSIM_CONSOLE_LOG_LIMIT` – max characters per log block echoed to stdout (default 4000).

## Running OpenEvolve

From the repository root:

```bash
python openevolve/openevolve-run.py \
  champsim_prefetcher/initial_program.cc \
  champsim_prefetcher/evaluator.py \
  --config champsim_prefetcher/config.yaml \
  --iterations 5
```

Each iteration performs the following:

1. OpenEvolve emits a new `initial_program.cc` candidate.
2. The evaluator overwrites `champsim_prefetcher/initial_program.cc` (the file
   that ChampSim includes via the thin module shim).
3. `make -C ChampSim` rebuilds the binary (incremental after the first build).
4. ChampSim runs the configured trace and prints per-core IPC.
5. The evaluator extracts the final `cumulative IPC` and returns it as
   `combined_score` for the MAP-Elites loop.

Artifacts (trimmed ChampSim logs, build logs, etc.) are attached to the
`EvaluationResult` so you can debug failed runs inside
`openevolve_output/logs/evaluations/`.

## File Inventory

```
champsim_prefetcher/
├── README.md                ← this guide
├── initial_program.cc       ← seed prefetcher edited by OpenEvolve
├── evaluator.py             ← glue code that rebuilds ChampSim
├── config.yaml              ← small-iteration configuration tuned for long runs
└── champsim_config.json     ← instructs config.sh to use openevolve_prefetcher
```

The ChampSim module under `ChampSim/prefetcher/openevolve_prefetcher/` is now a
two-line shim that simply `#include`s the sources in this folder, so the
submodules remain pristine while OpenEvolve edits the shared files tracked at the
repository root.
