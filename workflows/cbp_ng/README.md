# CBP-NG Workflow

This workflow wires OpenEvolve to `cbp-ng` so each candidate edits
`workflows/cbp_ng/initial_program.hpp`, rebuilds `cbp-ng/cbp`, and evaluates on
CBP-NG traces in parallel.
The optimization metric (`combined_score`) is VFS.

## One-time setup

1. Download traces:

```bash
./scripts/download_cbpng_traces.sh
```

2. Ensure CBP-NG bridge header exists (already tracked in repo):

- `cbp-ng/predictors/openevolve_predictor.hpp` includes `workflows/cbp_ng/initial_program.hpp`.

## Run OpenEvolve

```bash
python openevolve/openevolve-run.py \
  workflows/cbp_ng/initial_program.hpp \
  workflows/cbp_ng/evaluator.py \
  --config workflows/cbp_ng/config.yaml \
  --iterations 5
```

## Environment knobs

- `CBPNG_TRACE_DIR` (default `traces/cbp-ng`)
- `CBPNG_TRACE_SUFFIX` (default `_trace.gz`)
- `CBPNG_WARMUP_INSTR` (default `1000000`)
- `CBPNG_SIM_INSTR` (default `40000000`)
- `CBPNG_TIMEOUT` per trace run seconds (0 means no timeout)
- `CBPNG_BUILD_TIMEOUT` build timeout seconds
- `CBPNG_TRACE_WORKERS` parallel trace workers (default 16)
- `CBPNG_TRACE_ITERATIONS` repeat each trace N times and average
