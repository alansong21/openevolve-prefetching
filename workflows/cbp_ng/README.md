# CBP-NG Workflow

This workflow wires OpenEvolve to `cbp-ng` so each candidate edits
`workflows/cbp_ng/initial_program.hpp`, rebuilds `cbp-ng/cbp`, and evaluates on
CBP-NG traces in parallel. The durable starting point lives in
`workflows/cbp_ng/seed_program.hpp`; the run wrapper copies that seed into
`initial_program.hpp` at the start of every run unless you override it with
`--initial-program`.
The optimization metric (`combined_score`) is VFS.

## One-time setup

1. Run the setup helper:

```bash
./setup_cbp_ng.sh
```

This installs `pip` if needed, installs `g++-12` for CBP-NG, downloads the
CBP-NG traces, and creates the generated bridge header under `cbp-ng/` that
points at `workflows/cbp_ng/initial_program.hpp`.

2. Or download traces manually:

```bash
./scripts/download_cbpng_traces.sh
```

## Run OpenEvolve

```bash
python openevolve/openevolve-run.py \
  workflows/cbp_ng/initial_program.hpp \
  workflows/cbp_ng/evaluator.py \
  --config workflows/cbp_ng/config.yaml \
  --iterations 5
```

If you want the automatic seed reset behavior, prefer the wrapper:

```bash
./scripts/run_openevolve_workflow.sh --workflow cbp-ng --iterations 5
```

For CloudLab-backed distributed evaluation, point OpenEvolve at
[`evaluator_cloudlab.py`](/users/als2005/openevolve-prefetching/workflows/cbp_ng/evaluator_cloudlab.py)
instead:

```bash
python openevolve/openevolve-run.py \
  workflows/cbp_ng/initial_program.hpp \
  workflows/cbp_ng/evaluator_cloudlab.py \
  --config workflows/cbp_ng/config.yaml \
  --iterations 5
```

CloudLab notes:

- Fill in [`cloudlab-lib/server-config.json`](/users/als2005/openevolve-prefetching/cloudlab-lib/server-config.json) with your node names, username, and SSH key.
- Each CloudLab node should have this repo checked out and prepared already.
- By default the evaluator assumes the repo lives at the same absolute path on each node.
  Set `CLOUDLAB_REPO_ROOT` if the remote checkout path differs.
- The evaluator copies `workflows/cbp_ng/initial_program.hpp` to each node, regenerates
  `cbp-ng/predictors/openevolve_predictor.hpp` remotely, builds `cbp-ng/cbp`,
  and distributes traces round-robin across connected nodes.

## Environment knobs

- `CBPNG_TRACE_DIR` (default `traces/cbp-ng`)
- `CBPNG_TRACE_SUFFIX` (default `_trace.gz`)
- `CBPNG_WARMUP_INSTR` (default `1000000`)
- `CBPNG_SIM_INSTR` (default `40000000`)
- `CBPNG_TIMEOUT` per trace run seconds (0 means no timeout)
- `CBPNG_BUILD_TIMEOUT` build timeout seconds
- `CBPNG_TRACE_WORKERS` parallel trace workers (default 16)
- `CBPNG_TRACE_ITERATIONS` repeat each trace N times and average
- `CBPNG_TOOLCHAIN_BIN` compiler-wrapper directory override for local builds
- `CLOUDLAB_REPO_ROOT` remote repo path override for CloudLab evaluator
