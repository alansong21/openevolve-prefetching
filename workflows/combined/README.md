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

## Phase 0 instrumentation (multi-agent co-design)

The combined evaluator now emits richer ChampSim feedback for downstream agents.
See `docs/multi_agent_codesign.md` for the full design.

### Per-candidate evaluation artifacts

Each OpenEvolve evaluation run writes under
`openevolve-components/openevolve_output/` (exact subpath depends on run id):

| Artifact | Path pattern | Contents |
|----------|--------------|----------|
| Simulator stdout | `.../champsim/<run_id>/<trace>.log` | Full ChampSim output |
| Miss log | `.../champsim/<run_id>/<trace_stem>.misses.txt` | Demand misses per `(cpu, pc, address)` |
| Parsed stats | `metrics` + `artifacts.trace_results.trace_N_stats` | IPC, L2C/LLC MPKI, prefetch usefulness, miss latency |

Miss logging is enabled by default (`CHAMPSIM_MISS_LOG=true`). Disable with
`CHAMPSIM_MISS_LOG=false`.

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
(default for the combined launcher), OpenEvolve uses specialist engineers
instead of a single undifferentiated mutation LLM:

| Step | Agent | Action |
|------|-------|--------|
| 1 | Directive | Picks `joint`, `prefetcher_only`, or `replacement_only` from metrics + insights |
| 2 | PF engineer | SEARCH/REPLACE edits scoped to the prefetcher half |
| 3 | RP engineer | SEARCH/REPLACE edits scoped to the replacement half |
| 4 | Critic | Static checks (markers, APIs, no cross-includes, split round-trip) |
| 5 | Merge | Reassembles a valid combined file via `workflows/combined/merge.py` |

Implementation: `workflows/combined/agentic_mutation.py`, wired through
`openevolve/openevolve/mutation_agent.py` into `iteration.py` and
`process_parallel.py`. Falls back to the Phase 1 single-LLM path if agentic
mutation fails.

Environment knobs:

- `OPENEVOLVE_AGENTIC_MUTATION=false` — disable, use single LLM mutation
- `OPENEVOLVE_MUTATION_MODE=joint|prefetcher_only|replacement_only` — force edit scope
- `OPENEVOLVE_CRITIC_RETRIES=2` — critic reject/revise attempts before fallback

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
