#!/usr/bin/env bash

sudo apt-get update
sudo apt-get install -y pkg-config build-essential cmake ninja-build curl git unzip tar zip xz-utils

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

OPENEVOLVE_CONFIG="$SCRIPT_DIR/champsim_prefetcher/champsim_config.json"

TRACE_URL="https://dpc3.compas.cs.stonybrook.edu/champsim-traces/speccpu/400.perlbench-41B.champsimtrace.xz"
TRACE_FILE="400.perlbench-41B.champsimtrace.xz"

echo "Downloading trace: $TRACE_FILE"
if [[ -f "$TRACE_FILE" ]]; then
  echo "Trace already exists at $TRACE_FILE; skipping download."
else
  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --retry 3 --output "$TRACE_FILE" "$TRACE_URL"
  elif command -v wget >/dev/null 2>&1; then
    wget --tries=3 --output-document="$TRACE_FILE" "$TRACE_URL"
  else
    echo "Error: neither curl nor wget is available to download the trace." >&2
    exit 1
  fi
fi

cd ChampSim

echo "Updating git submodules..."
git submodule update --init

echo "Bootstrapping vcpkg..."
./vcpkg/bootstrap-vcpkg.sh

echo "Installing vcpkg dependencies..."
./vcpkg/vcpkg install

echo "Running ChampSim configuration..."
if [[ -f "$OPENEVOLVE_CONFIG" ]]; then
  echo "Using configuration at $OPENEVOLVE_CONFIG"
  ./config.sh "$OPENEVOLVE_CONFIG"
else
  ./config.sh
fi

echo "Building ChampSim..."
make

echo "ChampSim setup complete."
