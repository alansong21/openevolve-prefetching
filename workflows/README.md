# Workflows

This directory hosts simulator-specific OpenEvolve workflows.

## Current workflows

- `workflows/champsim` (legacy files still in `openevolve-components/`)
- `workflows/cbp_ng` (new CBP-NG branch-predictor workflow)

## Convention for new workflows

Each workflow should provide:

- `initial_program.*` - candidate source edited by OpenEvolve
- `evaluator.py` - build + execute + metric extraction + artifacts
- `config.yaml` - OpenEvolve run configuration
- `README.md` - setup/run docs and environment knobs

This keeps simulator-specific logic isolated while allowing shared scripts and
future additions (e.g., gem5, Sniper, custom trace pipelines).
