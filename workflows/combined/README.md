# Combined Prefetcher + Replacement Workflow

Joint OpenEvolve workflow that co-evolves the L2C **prefetcher** and the L2C
**replacement policy** in a single source file. The LLM sees one file with two
labelled sections; the evaluator splits that file back into two ChampSim
modules, rebuilds, and reports IPC.

## Layout

```
workflows/combined/
  initial_program.cc      # Combined seed (PF + RP), split-marker delimited
  champsim_config.json    # ChampSim config that wires both modules at L2C
  config.yaml             # OpenEvolve config + dual-API system message
  evaluator.py            # Splits the candidate, reuses base evaluator pipeline
  README.md
```

The split markers must stay verbatim in the combined file:

```
// === OPENEVOLVE_PREFETCHER_BEGIN ===
... openevolve_prefetcher member definitions ...
// === OPENEVOLVE_PREFETCHER_END ===

// === OPENEVOLVE_REPLACEMENT_BEGIN ===
... openevolve_replacement member definitions ...
// === OPENEVOLVE_REPLACEMENT_END ===
```

Each section is later compiled as an independent translation unit, so they
must NOT share globals, helpers, or `using` directives.

## Pieces installed elsewhere

* `openevolve-components/openevolve_replacement.h` — class declaration for the
  evolved replacement policy.
* `openevolve-components/initial_replacement.cc` — default LRU baseline; the
  evaluator overwrites this file with the LLM's split output each iteration.
* `setup_champsim.sh` wires `ChampSim/replacement/openevolve_replacement/` as
  a thin shim that includes the file above (mirrors the prefetcher shim).

The submodules under `ChampSim/` and `openevolve/` are never edited directly;
all coupling lives in `openevolve-components/` and `setup_champsim.sh`.

## Running

After `./setup_champsim.sh` has built ChampSim once and you have set
`OPENAI_API_KEY`, run:

```bash
python openevolve/openevolve-run.py \
  workflows/combined/initial_program.cc \
  workflows/combined/evaluator.py \
  --config workflows/combined/config.yaml \
  --iterations 5
```

Or via the unified launcher:

```bash
./scripts/run_openevolve_workflow.sh --workflow combined --iterations 5
```

The first iteration will copy
`workflows/combined/champsim_config.json` into `ChampSim/`, re-run
`config.sh` so the `openevolve_replacement` module is registered, invalidate
the prefetcher AND replacement build artifacts, and rebuild ChampSim before
running the configured traces. Logs appear under
`openevolve-components/openevolve_output/`.

## Compatibility with the solo workflow

The solo prefetcher workflow (`openevolve-components/evaluator.py` +
`openevolve-components/champsim_config.json`) keeps using the LRU baseline at
L2C and ignores `openevolve_replacement`. Because the combined evaluator
overrides hooks on its own copy of the base module (`importlib.util`-loaded as
`openevolve_components_evaluator`) without mutating the on-disk file, both
workflows can be alternated freely.

If you switch from `combined` back to `champsim`, the next solo run will
re-copy the solo `champsim_config.json` over `ChampSim/champsim_config.json`,
re-run `config.sh`, and rebuild — the stale `openevolve_replacement` build
artifacts are harmless and will simply not be linked.
