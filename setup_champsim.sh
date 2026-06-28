#!/usr/bin/env bash

sudo apt-get update
sudo apt-get install -y pkg-config build-essential cmake ninja-build curl git unzip tar zip xz-utils python3.10-venv

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

OPENEVOLVE_CONFIG="$SCRIPT_DIR/openevolve-components/champsim_config.json"
OPENEVOLVE_SOURCE_DIR="$SCRIPT_DIR/openevolve-components"
OPENEVOLVE_PREFETCHER_TARGET_DIR="$SCRIPT_DIR/ChampSim/prefetcher/openevolve_prefetcher"
OPENEVOLVE_REPLACEMENT_TARGET_DIR="$SCRIPT_DIR/ChampSim/replacement/openevolve_replacement"
# Back-compat alias (the variable name was singular before the joint
# prefetcher+replacement workflow was added).
OPENEVOLVE_TARGET_DIR="$OPENEVOLVE_PREFETCHER_TARGET_DIR"

TRACE_BASE_URL="https://dpc3.compas.cs.stonybrook.edu/champsim-traces/speccpu"
TRACE_FILES=(
  "400.perlbench-41B.champsimtrace.xz"
  "403.gcc-48B.champsimtrace.xz"
  "429.mcf-51B.champsimtrace.xz"
)

for TRACE_FILE in "${TRACE_FILES[@]}"; do
  TRACE_URL="$TRACE_BASE_URL/$TRACE_FILE"
  echo "Downloading trace: $TRACE_FILE"
  if [[ -f "$TRACE_FILE" ]]; then
    echo "Trace already exists at $TRACE_FILE; skipping download."
    continue
  fi

  if command -v curl >/dev/null 2>&1; then
    curl --fail --location --retry 3 --output "$TRACE_FILE" "$TRACE_URL"
  elif command -v wget >/dev/null 2>&1; then
    wget --tries=3 --output-document="$TRACE_FILE" "$TRACE_URL"
  else
    echo "Error: neither curl nor wget is available to download the trace." >&2
    exit 1
  fi
done

cd ChampSim

echo "Updating git submodules..."
git submodule update --init

apply_miss_log_patch() {
  local patch_file="$SCRIPT_DIR/patches/champsim_pc_address_miss_log.patch"
  local marker_file="inc/cache_stats.h"

  if [[ ! -f "$patch_file" ]]; then
    echo "Error: miss-log patch not found at $patch_file" >&2
    exit 1
  fi

  if grep -q 'pc_address_miss_logging_enabled' "$marker_file" 2>/dev/null; then
    echo "ChampSim miss-log instrumentation already present; skipping patch."
    return 0
  fi

  echo "Applying ChampSim miss-log instrumentation patch..."
  if ! patch -p1 --forward --dry-run --silent < "$patch_file"; then
    echo "Error: miss-log patch does not apply cleanly to the current ChampSim tree." >&2
    echo "       If you partially applied it, restore ChampSim core files and re-run setup." >&2
    exit 1
  fi
  patch -p1 --forward --silent < "$patch_file"
  echo "Miss-log patch applied (inc/cache_stats.h, src/cache_stats.cc, src/cache.cc, src/main.cc)."
}

apply_miss_log_patch

echo "Syncing OpenEvolve prefetcher sources into ChampSim..."
mkdir -p "$OPENEVOLVE_PREFETCHER_TARGET_DIR"
if [[ ! -f "$OPENEVOLVE_SOURCE_DIR/openevolve_prefetcher.h" || ! -f "$OPENEVOLVE_SOURCE_DIR/initial_program.cc" ]]; then
  echo "Error: expected OpenEvolve prefetcher sources missing in $OPENEVOLVE_SOURCE_DIR" >&2
  exit 1
fi
cat >"$OPENEVOLVE_PREFETCHER_TARGET_DIR/openevolve_prefetcher.h" <<'EOF'
#ifndef PREFETCHER_OPENEVOLVE_PREFETCHER_H
#define PREFETCHER_OPENEVOLVE_PREFETCHER_H

#include "../../../../openevolve-components/openevolve_prefetcher.h"

#endif
EOF

cat >"$OPENEVOLVE_PREFETCHER_TARGET_DIR/openevolve_prefetcher.cc" <<'EOF'
#include "../../../../openevolve-components/initial_program.cc"
EOF

echo "Syncing OpenEvolve replacement sources into ChampSim..."
mkdir -p "$OPENEVOLVE_REPLACEMENT_TARGET_DIR"
if [[ ! -f "$OPENEVOLVE_SOURCE_DIR/openevolve_replacement.h" || ! -f "$OPENEVOLVE_SOURCE_DIR/initial_replacement.cc" ]]; then
  echo "Error: expected OpenEvolve replacement sources missing in $OPENEVOLVE_SOURCE_DIR" >&2
  echo "       (needed by the joint prefetcher+replacement workflow under workflows/combined/)" >&2
  exit 1
fi
cat >"$OPENEVOLVE_REPLACEMENT_TARGET_DIR/openevolve_replacement.h" <<'EOF'
#ifndef REPLACEMENT_OPENEVOLVE_REPLACEMENT_H
#define REPLACEMENT_OPENEVOLVE_REPLACEMENT_H

#include "../../../../openevolve-components/openevolve_replacement.h"

#endif
EOF

cat >"$OPENEVOLVE_REPLACEMENT_TARGET_DIR/openevolve_replacement.cc" <<'EOF'
#include "../../../../openevolve-components/initial_replacement.cc"
EOF

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
