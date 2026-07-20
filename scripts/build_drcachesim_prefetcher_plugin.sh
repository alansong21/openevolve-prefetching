#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SOURCE="${1:-$REPO_ROOT/openevolve-components/drcachesim/example_prefetcher_plugin.cpp}"
OUTPUT="${2:-${SOURCE%.cpp}.so}"
DRCACHESIM="$REPO_ROOT/DynamoRIO/clients/drcachesim"
SIMULATOR_LIB="$REPO_ROOT/DynamoRIO/build/clients/lib64/release/libdrmemtrace_simulator.a"
CXX="${CXX:-g++}"

if [[ ! -f "$SOURCE" ]]; then
  echo "Prefetcher plugin source not found: $SOURCE" >&2
  exit 1
fi

if [[ ! -f "$SIMULATOR_LIB" ]]; then
  echo "drcachesim simulator library not found: $SIMULATOR_LIB" >&2
  echo "Build DynamoRIO's drmemtrace_launcher first." >&2
  exit 1
fi

"$CXX" -std=c++17 -fPIC -shared \
  -I"$DRCACHESIM" \
  -I"$DRCACHESIM/common" \
  -I"$DRCACHESIM/simulator" \
  "$SOURCE" \
  "$SIMULATOR_LIB" \
  -ldl \
  -o "$OUTPUT"

echo "$OUTPUT"
