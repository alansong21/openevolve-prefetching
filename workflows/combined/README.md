# Combined Prefetcher + Replacement Workflow

Joint OpenEvolve workflow that co-evolves the L2C **prefetcher** and the L2C
**replacement policy** in a single source file. The LLM sees one file with two
labelled sections; the evaluator splits that file back into two ChampSim
modules, rebuilds, and reports IPC.

## Layout

```
workflows/combined/
  initial_program.cc      # Combined seed (PF + RP), split-marker delimited
  champsim_config.json    # DPC4 1C.limitBW machine; OpenEvolve PF+RP at L2C
  baseline_champsim_config.json  # Same machine; L2C no+lru for miss-log baseline
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

## Phase 0 instrumentation (multi-agent co-design)

The combined evaluator now emits richer ChampSim feedback for downstream agents.
See `docs/multi_agent_codesign.md` for the full design.

### Per-candidate evaluation artifacts

Each OpenEvolve evaluation run writes under
`openevolve-components/openevolve_output/` (exact subpath depends on run id):

| Artifact | Path pattern | Contents |
|----------|--------------|----------|
| Simulator stdout | `.../champsim/<run_id>/<trace>.log` | Full ChampSim output |
| Miss log | `.../champsim/<run_id>/<trace_stem>.misses.txt` | L2C demand misses per `(cpu, pc, address)` |
| Parsed stats | `metrics` + `artifacts.trace_results.trace_N_stats` | IPC, L2C/LLC MPKI, prefetch usefulness, miss latency |

Miss logging is enabled by default (`CHAMPSIM_MISS_LOG=true`). Disable with
`CHAMPSIM_MISS_LOG=false`. The log records **L2C demand misses only**
(simulation ROI; prefetch misses excluded). Rebuild ChampSim after changing
the miss-log instrumentation.

Metrics added to each evaluation include aggregate and per-trace:

- `l2c_mpki`, `llc_mpki`
- `l2c_pf_useful`, `l2c_pf_useless`, `l2c_pf_issued`, `l2c_prefetch_accuracy`
- `trace_N_l2c_avg_miss_latency`, `trace_N_llc_avg_miss_latency`

When baseline/profile caches exist, artifacts also reference:

- `baseline_miss_log_path` → `workflows/combined/baseline/<trace_stem>/misses.txt`
- `baseline_stats_path` → `workflows/combined/baseline/<trace_stem>/stats.json`
- `workload_profile_path` → `workflows/combined/profiles/<trace_stem>.json`

### One-time offline setup

Generate baseline miss logs (no L2C prefetcher, LRU replacement):

```bash
python scripts/generate_baseline_miss_logs.py \
  --trace-dir /nfs/traces/SPEC17 \
  --warmup 1000000 \
  --simulation 1000000
```

Profile raw traces for workload characterization:

```bash
python scripts/profile_trace.py /nfs/traces/SPEC17/605.mcf_s-472B.champsimtrace.xz
python scripts/profile_trace.py /nfs/traces/SPEC17/602.gcc_s-734B.champsimtrace.xz
```

Baseline config: `workflows/combined/baseline_champsim_config.json`
Baseline outputs: `workflows/combined/baseline/<trace_stem>/`
Profile outputs: `workflows/combined/profiles/<trace_stem>.json`

## Phase 1 advisor insights (multi-agent Mode A)

When `OPENEVOLVE_WORKFLOW=combined` (set automatically by
`scripts/run_openevolve_workflow.sh --workflow combined`), each mutation
prompt is enriched with deterministic advisor output instead of the static
ChampSim header dump:

| Agent | Input | Output |
|-------|-------|--------|
| Workload characterization | `workflows/combined/profiles/*.json` | Taxonomy + PF/RP strategy bias per trace |
| Miss-log analysis | Candidate `*.misses.txt` + baseline `baseline/*/misses.txt` | Ranked hypotheses (coverage/conflict/capacity/compulsory) |

Implementation lives under `workflows/combined/agents/` and is wired through
`openevolve/openevolve/context_agent.py::fetch_context_from_agent`.

Prerequisites:

1. Profile traces: `python scripts/profile_all_spec_traces.py -w 1M -i 1M`
2. (Optional) Baseline miss logs: `python scripts/generate_baseline_miss_logs.py ...`

Insights appear in the prompt under `--- ChampSim context (agent) ---`.
Miss-log hypotheses populate after the first evaluated candidate produces
`trace_N_miss_log_path` artifacts.

## Phase 2 agentic mutation (Mode B)

When `OPENEVOLVE_WORKFLOW=combined` and `OPENEVOLVE_AGENTIC_MUTATION=true`
(default for the combined launcher), OpenEvolve uses one implementation agent
that owns PF+RP behavior in both simulators:

| Step | Agent | Action |
|------|-------|--------|
| 1 | Directive | Selects a joint design play and optional PF/RP focus text |
| 2 | Unified implementer | One SEARCH/REPLACE response across all four policy sections |
| 3 | Critic | Static checks (markers, APIs, no cross-includes, split round-trip) |
| 4 | Evaluator | Builds ChampSim modules and the combined drcachesim plugin |

Implementation: `workflows/combined/agentic_mutation.py`, wired through
`openevolve/openevolve/mutation_agent.py` into `iteration.py` and
`process_parallel.py`. Falls back to the Phase 1 single-LLM path if agentic
mutation fails.

Environment knobs:

- `OPENEVOLVE_AGENTIC_MUTATION=false` — disable, use single LLM mutation
- `OPENEVOLVE_MUTATION_MODE=joint|prefetcher|replacement` — force focus text;
  the unified implementer still owns all four sections
- `OPENEVOLVE_CRITIC_RETRIES=2` — critic reject/revise attempts before fallback

## Hierarchical evaluation and rigor

- `OPENEVOLVE_HIERARCHICAL_EVAL=true` — use drcachesim before periodic
  ChampSim; set false for the no-stage-1 ablation.
- `HIER_STAGE2_EVERY_N=10` — authoritative ChampSim cadence.
- `DRCACHESIM_TRACE_ROOT` — converted L2 access-trace root containing both
  `.trace.gz` payloads and adjacent `.counts.json` files.
- `DRCACHESIM_CHAMPSIM_CONFIG` — ChampSim JSON whose `L2C`/`LLC` geometry
  sizes the L2C→LLC drcachesim hierarchy (default:
  `workflows/combined/champsim_config.json`, DPC4 **1C.limitBW**:
  L2C 2 MiB, LLC 3 MiB, DRAM 800 MT/s).
  Stock drcachesim does not model ChampSim DRAM timing; stage-1 records
  ChampSim `physical_memory` / hit latencies as metadata only.
- `STORAGE_BUDGET_BYTES=262144` — hard PF+RP state budget (DPC4-sized L2C).
- `CHAMPSIM_HELDOUT_PATTERNS=token1,token2` — trace-name tokens reserved for
  held-out regression checks.
- `HIER_CALIBRATION_PATH` — persisted proxy-to-IPC ridge model.

Use `scripts/run_combined_ablation.py` to record and run full,
native-mutator, no-stage-1, and native/no-stage-1 variants.

## Phase 3 orchestrator (Mode C)

When `OPENEVOLVE_ORCHESTRATOR=true` (default for the combined launcher), the
mutation path uses a central orchestrator with bandit strategy selection and a
persistent blackboard:

| Component | Path | Role |
|-----------|------|------|
| Orchestrator | `workflows/combined/orchestrator.py` | Runs analyst → bandit → directive → engineers → critic |
| Blackboard | `workflows/combined/blackboard.py` | Tried-ideas log, bandit state, pending rewards (`state/blackboard_<run_id>.json`) |
| Strategy bandit | `workflows/combined/strategy/bandit.py` | UCB1 over PF/RP knob arms |
| Plays library | `workflows/combined/strategy/plays.py` | Named §5 co-design strategies |
| Metadata contract | `workflows/combined/metadata_contract.py` | PF↔RP encoding enforced by critic on joint rounds |

After each ChampSim evaluation, `record_orchestrator_evaluation_reward` updates
the bandit with IPC (and MPKI) delta from the parent.

Uses LangGraph when installed; otherwise runs the same node sequence sequentially.

Environment knobs:

- `OPENEVOLVE_ORCHESTRATOR=false` — fall back to Phase 2 heuristic directive
- `OPENEVOLVE_BLACKBOARD_DIR` — override blackboard JSON directory
- `OPENEVOLVE_RUN_ID` — scopes blackboard file per OpenEvolve run
