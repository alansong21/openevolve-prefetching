#!/usr/bin/env bash

# Run ChampSim with the IPCP prefetchers without invoking OpenEvolve.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CHAMPSIM_DIR="$REPO_ROOT/ChampSim"
PREFETCHER_ROOT="$REPO_ROOT/champsim_prefetcher"
DEFAULT_TRACE="$REPO_ROOT/400.perlbench-41B.champsimtrace.xz"

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

sync_ipcp_prefetchers() {
  for pf in ipcp_l1d ipcp_l2c; do
    local src_cc="$PREFETCHER_ROOT/${pf}.cc"
    local src_h="$PREFETCHER_ROOT/${pf}.h"
    local dest_dir="$CHAMPSIM_DIR/prefetcher/${pf}"

    if [[ ! -f "$src_cc" || ! -f "$src_h" ]]; then
      echo "Missing IPCP source files for $pf in $PREFETCHER_ROOT" >&2
      exit 1
    fi

    mkdir -p "$dest_dir"
    cp "$src_cc" "$dest_dir/"
    cp "$src_h" "$dest_dir/"
  done
}

sync_config() {
  local src_cfg="$PREFETCHER_ROOT/champsim_config.json"
  local dest_cfg="$CHAMPSIM_DIR/champsim_config.json"

  if [[ ! -f "$src_cfg" ]]; then
    echo "Missing ChampSim config at $src_cfg" >&2
    exit 1
  fi

  cp "$src_cfg" "$dest_cfg"
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

sync_ipcp_prefetchers
sync_config
run_champsim
