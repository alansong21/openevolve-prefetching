# ChampSim Workflow

The active ChampSim workflow remains in `openevolve-components/` for backward
compatibility.

Use:

```bash
python openevolve/openevolve-run.py \
  openevolve-components/initial_program.cc \
  openevolve-components/evaluator.py \
  --config openevolve-components/config.yaml \
  --iterations 5
```

This placeholder directory exists so workflow-specific assets can be migrated
here incrementally without breaking current scripts.
