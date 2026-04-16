#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
CBPNG_ROOT="$REPO_ROOT/cbp-ng"
BRIDGE_HEADER="$CBPNG_ROOT/predictors/openevolve_predictor.hpp"
TRACE_SCRIPT="$REPO_ROOT/scripts/download_cbpng_traces.sh"
TOOLCHAIN_BIN_DIR="$REPO_ROOT/toolchains/cbp_ng/bin"

run_apt() {
  if command -v sudo >/dev/null 2>&1; then
    sudo apt-get "$@"
  else
    apt-get "$@"
  fi
}

ensure_pip() {
  if python3 -m pip --version >/dev/null 2>&1; then
    echo "python3 pip already installed."
    return
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Error: python3 -m pip is unavailable and apt-get was not found." >&2
    exit 1
  fi

  echo "Installing python3-pip..."
  run_apt update
  run_apt install -y python3-pip
}

ensure_compiler() {
  if command -v g++-12 >/dev/null 2>&1; then
    echo "g++-12 already installed."
    return
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Error: g++-12 is unavailable and apt-get was not found." >&2
    exit 1
  fi

  echo "Installing g++-12..."
  run_apt update
  run_apt install -y g++-12
}

ensure_toolchain_wrapper() {
  mkdir -p "$TOOLCHAIN_BIN_DIR"
  chmod +x "$TOOLCHAIN_BIN_DIR/g++"
  echo "Configured toolchain wrapper: $TOOLCHAIN_BIN_DIR/g++"
}

ensure_cbpng_bridge() {
  if [[ ! -d "$CBPNG_ROOT" ]]; then
    echo "Error: CBP-NG root not found at $CBPNG_ROOT" >&2
    exit 1
  fi

  mkdir -p "$(dirname "$BRIDGE_HEADER")"
  cat >"$BRIDGE_HEADER" <<'EOF'
#pragma once

#include "../../workflows/cbp_ng/initial_program.hpp"
EOF

  echo "Wrote CBP-NG bridge header: $BRIDGE_HEADER"
}

main() {
  ensure_pip
  ensure_compiler
  ensure_toolchain_wrapper

  if [[ ! -x "$TRACE_SCRIPT" ]]; then
    echo "Error: trace download helper missing or not executable: $TRACE_SCRIPT" >&2
    exit 1
  fi

  echo "Downloading CBP-NG traces..."
  "$TRACE_SCRIPT"

  echo "Creating OpenEvolve bridge files inside cbp-ng..."
  ensure_cbpng_bridge

  echo "CBP-NG setup complete."
  echo "You can now run: ./scripts/run_openevolve_workflow.sh --workflow cbp-ng --iterations 5"
}

main "$@"
