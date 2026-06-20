# OpenEvolve + ChampSim Prefetcher

This repository wires OpenEvolve into the ChampSim simulator so evolution edits a C++ prefetcher (`openevolve-components/initial_program.cc`) and evaluates it on ChampSim traces.

## Setup
- Requires Ubuntu/Debian with `sudo` for packages (`pkg-config`, build tools, CMake, Ninja, git, curl, unzip, tar, zip, xz-utils, python3.10-venv).
- Create and activate a Python virtual environment at the repo root:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```
- Install Python dependencies inside the activated venv:
  ```bash
  pip install -r requirements.txt
  ```
- Point the `openevolve` submodule at the maintained fork (fetch + push):
  ```bash
  git submodule update --init --recursive
  git -C openevolve remote set-url origin https://github.com/alansong21/openevolve.git
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
- Each iteration overwrites `openevolve-components/initial_program.cc`, rebuilds `ChampSim`, runs the configured traces, and reports IPC as the score. Logs and artifacts land under `openevolve-components/openevolve_output/`.

## Joint prefetcher + replacement workflow
- Co-evolve the L2C prefetcher and the L2C replacement policy in a single
  source file (split markers tell the evaluator how to break it back into two
  ChampSim modules). See `workflows/combined/README.md` for the full layout.
- Run it directly:
  ```bash
  python openevolve/openevolve-run.py \
    workflows/combined/initial_program.cc \
    workflows/combined/evaluator.py \
    --config workflows/combined/config.yaml \
    --iterations 5
  ```
- Or via the unified launcher:
  ```bash
  ./scripts/run_openevolve_workflow.sh --workflow combined --iterations 5
  ```
- The combined evaluator reuses the solo evaluator's build/run pipeline (no
  duplicated logic) by importing it under a sandboxed module name and patching
  only the candidate-prep / build-invalidation hooks, so the solo prefetcher
  workflow above keeps working unchanged.

## CBP-NG workflow
- Download CBP-NG traces:
  ```bash
  ./scripts/download_cbpng_traces.sh
  ```
- Run OpenEvolve against CBP-NG:
  ```bash
  python openevolve/openevolve-run.py \
    workflows/cbp_ng/initial_program.hpp \
    workflows/cbp_ng/evaluator.py \
    --config workflows/cbp_ng/config.yaml \
    --iterations 5
  ```
- Optional unified launcher:
  ```bash
  ./scripts/run_openevolve_workflow.sh --workflow cbp-ng --iterations 5
  ```

## Vizier hyperparameter tuning (post-evolution)
- After an evolution run, a second stage can tune the numeric/categorical
  constants *inside* the evolved program with
  [Google Vizier](https://github.com/google/vizier), reusing the **same
  evaluator** (so the objective is identical: ChampSim `combined_score`/IPC).
- The hyperparameters are discovered automatically by a single LLM call (reusing
  the workflow's existing LLM config + `OPENAI_API_KEY`), which rewrites the
  source into a `{{HP_name}}` placeholder template; each Vizier trial renders the
  template, runs the evaluator, and reports the score back.
- Vizier is a normal pip dependency (already pinned in `requirements.txt`,
  along with a coherent JAX stack for its GP-Bandit algorithm):
  ```bash
  pip install -r requirements.txt
  ```
- Run it standalone against a workflow's best evolved program:
  ```bash
  python -m vizier_tuning.run_vizier_tuning --workflow champsim --trials 50
  # or: ./scripts/run_vizier_tuning.sh --workflow champsim --trials 50
  ```
- Or chain it right after evolution:
  ```bash
  ./scripts/run_openevolve_workflow.sh --workflow champsim --iterations 5 \
    --with-vizier --vizier-trials 50
  ```
- Inspect what would be tuned without running a search (Stage A only):
  ```bash
  python -m vizier_tuning.run_vizier_tuning --workflow champsim --dry-run
  ```
- Outputs land under `<workflow openevolve_output>/vizier/<run_id>/`:
  `best_tuned_program.*`, `best_params.json`, `trials.jsonl`, plus the Stage-A
  `templated_source.txt` / `param_spec.json`. The evolved `best_program` is never
  overwritten in place.
- Knobs live in `vizier_tuning/vizier_tuning_config.yaml`; see
  `docs/vizier_integration_design.md` for the full design.

## Useful notes
- The ChampSim module `ChampSim/prefetcher/openevolve_prefetcher` is a two-line shim that includes the shared sources under `openevolve-components/`, keeping the submodule clean.
- Update the trace list by editing `TRACES` in `openevolve-components/evaluator.py`. Other knobs (jobs, timeouts, instruction counts) are documented in `openevolve-components/README.md`.
- Ready-to-run Bingo, IPCP, and MLOP wrappers live under `prefetchers/{bingo,ipcp,mlop}/` with scripts in `scripts/` for copying them into the ChampSim submodule.
- Workflow-specific assets live under `workflows/` to keep simulator integrations extensible.
- Quick API sanity check:
  ```bash
  python smoke.py
  ```
