#!/usr/bin/env bash

# Run ChampSim with the MLOP prefetchers without invoking OpenEvolve.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CHAMPSIM_DIR="$REPO_ROOT/ChampSim"
PREFETCHER_ROOT="$REPO_ROOT/prefetchers/mlop"
DEFAULT_TRACE="$REPO_ROOT/400.perlbench-41B.champsimtrace.xz"
CONFIG_SOURCE="$PREFETCHER_ROOT/champsim_config.json"

TRACE_PATH="${1:-$DEFAULT_TRACE}"
WARMUP_INSTR="${CHAMPSIM_WARMUP_INSTR:-10000000}"
SIM_INSTR="${CHAMPSIM_SIM_INSTR:-50000000}"

detect_jobs() {
  if [[ -n "${CHAMPSIM_JOBS:-}" ]]; then
    echo "$CHAMPSIM_JOBS"
  elif command -v nproc >/dev/null 2>&1; then
    nproc
  else
    echo 1
  fi
}

JOBS="$(detect_jobs)"

ensure_openevolve_prefetcher_shim() {
  local components_dir="$REPO_ROOT/openevolve-components"
  local shim_dir="$CHAMPSIM_DIR/prefetcher/openevolve_prefetcher"

  if [[ ! -f "$components_dir/openevolve_prefetcher.h" || ! -f "$components_dir/initial_program.cc" ]]; then
    echo "Missing OpenEvolve shim sources under $components_dir. Re-run setup_champsim.sh." >&2
    exit 1
  fi

  mkdir -p "$shim_dir"

  cat >"$shim_dir/openevolve_prefetcher.h" <<'EOF'
#ifndef PREFETCHER_OPENEVOLVE_PREFETCHER_H
#define PREFETCHER_OPENEVOLVE_PREFETCHER_H

#include "../../../../openevolve-components/openevolve_prefetcher.h"

#endif
EOF

  cat >"$shim_dir/openevolve_prefetcher.cc" <<'EOF'
#include "../../../../openevolve-components/initial_program.cc"
EOF
}

sync_mlop_prefetchers() {
  local src_cc="$PREFETCHER_ROOT/mlop_l1d.cc"
  local src_h="$PREFETCHER_ROOT/mlop_l1d.h"
  local dest_dir="$CHAMPSIM_DIR/prefetcher/mlop_l1d"

  if [[ ! -f "$src_cc" || ! -f "$src_h" ]]; then
    echo "Missing MLOP source files in $PREFETCHER_ROOT" >&2
    exit 1
  fi

  mkdir -p "$dest_dir"
  cp "$src_cc" "$dest_dir/"
  cp "$src_h" "$dest_dir/"
}

sync_config() {
  if [[ ! -f "$CONFIG_SOURCE" ]]; then
    echo "Missing ChampSim config for MLOP at $CONFIG_SOURCE" >&2
    exit 1
  fi

  cp "$CONFIG_SOURCE" "$CHAMPSIM_DIR/champsim_config.json"
}

reset_build_artifacts() {
  rm -rf "$CHAMPSIM_DIR/.csconfig"
}

run_champsim() {
  if [[ ! -f "$TRACE_PATH" ]]; then
    echo "Trace not found: $TRACE_PATH" >&2
    exit 1
  fi

  pushd "$CHAMPSIM_DIR" >/dev/null

  echo "Configuring ChampSim with $CHAMPSIM_DIR/champsim_config.json"
  ./config.sh champsim_config.json

  echo "Building ChampSim (jobs: $JOBS)"
  make -j"$JOBS"

  echo "Running ChampSim with trace $TRACE_PATH"
  ./bin/champsim \
    --warmup-instructions "$WARMUP_INSTR" \
    --simulation-instructions "$SIM_INSTR" \
    "$TRACE_PATH"

  popd >/dev/null
}

ensure_openevolve_prefetcher_shim
sync_mlop_prefetchers
sync_config
reset_build_artifacts
run_champsim
