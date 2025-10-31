#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

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
./config.sh

echo "Building ChampSim..."
make

echo "ChampSim setup complete."
