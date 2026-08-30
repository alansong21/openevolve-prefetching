#!/usr/bin/env bash
# Build DynamoRIO drmemtrace launcher + libdrmemtrace_simulator.a for stage-1.
# Required by workflows/combined/drcachesim_runner.py when hierarchical eval is on.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DR_ROOT="$REPO_ROOT/DynamoRIO"
DR_BUILD="$DR_ROOT/build"
ZLIB_SRC="$DR_ROOT/third_party/zlib"
ZLIB_PREFIX="${ZLIB_PREFIX:-/tmp/openevolve-zlib-install}"
JOBS="${JOBS:-$(nproc)}"

if [[ ! -d "$DR_ROOT/clients/drcachesim" ]]; then
  echo "DynamoRIO submodule missing at $DR_ROOT" >&2
  exit 1
fi

if [[ ! -f "$ZLIB_SRC/zlib.h" ]]; then
  echo "Initialize zlib submodule: git -C DynamoRIO submodule update --init third_party/zlib" >&2
  exit 1
fi

# System zlib headers are often missing on CloudLab images; build a local static zlib.
if [[ ! -f "$ZLIB_PREFIX/lib/libz.a" ]]; then
  echo "Building local zlib into $ZLIB_PREFIX ..."
  rm -rf /tmp/openevolve-zlib-build
  mkdir -p /tmp/openevolve-zlib-build
  cmake -S "$ZLIB_SRC" -B /tmp/openevolve-zlib-build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$ZLIB_PREFIX"
  ninja -C /tmp/openevolve-zlib-build -j"$JOBS"
  ninja -C /tmp/openevolve-zlib-build install
fi

echo "Configuring DynamoRIO in $DR_BUILD ..."
mkdir -p "$DR_BUILD"
cmake -S "$DR_ROOT" -B "$DR_BUILD" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_DOCS=OFF \
  -DBUILD_TESTS=OFF \
  -DBUILD_SAMPLES=OFF \
  -DBUILD_EXT=ON \
  -DBUILD_CLIENTS=ON \
  -DZLIB_INCLUDE_DIR="$ZLIB_PREFIX/include" \
  -DZLIB_LIBRARY="$ZLIB_PREFIX/lib/libz.a"

echo "Building drmemtrace_simulator + drmemtrace_launcher ..."
ninja -C "$DR_BUILD" -j"$JOBS" drmemtrace_simulator drmemtrace_launcher

SIM_LIB="$DR_BUILD/clients/lib64/release/libdrmemtrace_simulator.a"
LAUNCHER="$DR_BUILD/clients/bin64/drmemtrace_launcher"
test -f "$SIM_LIB"
test -x "$LAUNCHER"
echo "OK: $SIM_LIB"
echo "OK: $LAUNCHER"
