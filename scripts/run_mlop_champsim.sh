#!/usr/bin/env bash

# Run ChampSim with the MLOP prefetchers without invoking OpenEvolve.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CHAMPSIM_DIR="$REPO_ROOT/ChampSim"
PREFETCHER_ROOT="$REPO_ROOT/champsim_prefetcher"
DEFAULT_TRACE="$REPO_ROOT/400.perlbench-41B.champsimtrace.xz"
CONFIG_SOURCE="$PREFETCHER_ROOT/champsim_config_mlop.json"

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

sync_mlop_prefetchers() {
  # Remove other custom prefetchers that might conflict with symbol names.
  rm -rf "$CHAMPSIM_DIR/prefetcher/bingo_l1d" "$CHAMPSIM_DIR/prefetcher/bingo_l2c" "$CHAMPSIM_DIR/prefetcher/bingo_llc"

  local src_dir="$PREFETCHER_ROOT/mlop"
  for pf in mlop_l1d mlop_l2c mlop_llc; do
    local src_cc="$src_dir/${pf}.cc"
    local src_h="$src_dir/${pf}.h"
    local dest_dir="$CHAMPSIM_DIR/prefetcher/${pf}"

    if [[ ! -f "$src_cc" || ! -f "$src_h" ]]; then
      echo "Missing MLOP source files for $pf in $src_dir" >&2
      exit 1
    fi

    mkdir -p "$dest_dir"
    cp "$src_cc" "$dest_dir/"
    cp "$src_h" "$dest_dir/"
  done
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

sync_mlop_prefetchers
sync_config
reset_build_artifacts
run_champsim
