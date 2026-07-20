#!/usr/bin/env bash
# Run ChampSim and create a drmemtrace/drcachesim-compatible L2 request trace.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CHAMPSIM_DIR="$REPO_ROOT/ChampSim"

TRACE_PATH="${1:-$REPO_ROOT/400.perlbench-41B.champsimtrace.xz}"
OUT_DIR="${2:-$REPO_ROOT/l2_drcachesim_out}"

CONFIG="${CHAMPSIM_CONFIG:-$REPO_ROOT/openevolve-components/champsim_config_l2_trace.json}"
CHAMPSIM_BIN="${CHAMPSIM_BIN:-$CHAMPSIM_DIR/bin/champsim_l2_trace}"
WARMUP_INSTR="${CHAMPSIM_WARMUP_INSTR:-50000000}"
SIM_INSTR="${CHAMPSIM_SIM_INSTR:-200000000}"
JOBS="${CHAMPSIM_JOBS:-$(nproc)}"

if [[ ! -f "$TRACE_PATH" ]]; then
  echo "Trace not found: $TRACE_PATH" >&2
  exit 1
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "ChampSim config not found: $CONFIG" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
RAW="$OUT_DIR/l2_requests.bin"
TRACE="$OUT_DIR/drmemtrace.champsim_l2.1.trace.gz"
COUNTS="$TRACE.counts.json"
CONFIG_COPY="$OUT_DIR/champsim_config.json"

# Configure and archive from the same immutable copy so the trace can always
# be tied to the exact ChampSim parameters used to produce it.
cp "$CONFIG" "$CONFIG_COPY"

if [[ "${SKIP_REBUILD:-0}" != "1" ]]; then
  echo "==> Configuring DPC4 1C.limitBW L2-trace build"
  (
    cd "$CHAMPSIM_DIR"
    ./config.sh "$CONFIG_COPY"
    make -j"$JOBS"
  )
fi

if [[ ! -x "$CHAMPSIM_BIN" ]]; then
  echo "Missing $CHAMPSIM_BIN; rerun without SKIP_REBUILD=1" >&2
  exit 1
fi

echo "==> ChampSim: warmup=$WARMUP_INSTR simulation=$SIM_INSTR"
rm -f "$RAW" "$TRACE" "$COUNTS"
CHAMPSIM_L2_MEMTRACE="$RAW" "$CHAMPSIM_BIN" \
  --warmup-instructions "$WARMUP_INSTR" \
  --simulation-instructions "$SIM_INSTR" \
  "$TRACE_PATH"

if [[ ! -s "$RAW" ]]; then
  echo "ChampSim did not create $RAW" >&2
  exit 1
fi

echo "==> Converting L2 requests to drmemtrace"
python3 "$SCRIPT_DIR/champsim_l2_to_drcachesim.py" \
  "$RAW" \
  --output "$TRACE" \
  --counts-json "$COUNTS" \
  --indir-root "$OUT_DIR"

echo "==> Complete"
echo "Trace:  $TRACE"
echo "Counts: $COUNTS"
echo "Config: $CONFIG_COPY"
python3 - "$COUNTS" "$TRACE" <<'PY'
import json
import sys

counts = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    "Run:\n"
    f"  drrun -t drmemtrace -infile {sys.argv[2]} -simulator_type cache "
    f"{counts['drrun_args']}"
)
PY
