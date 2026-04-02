#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIR="${CBPNG_TRACE_DIR:-$ROOT_DIR/traces/cbp-ng}"
ARCHIVE_PATH="$DEST_DIR/cbp-ng_training_traces.tar.gz"
TRACE_URL="https://drive.google.com/file/d/1kLKn_iKVBP-YxRpC4WiCy-ca-agU0BFG/view"

ensure_gdown() {
  if ! command -v gdown >/dev/null 2>&1; then
    echo "gdown not found; installing with pip..."
    python3 -m pip install --user gdown
    export PATH="$HOME/.local/bin:$PATH"
  fi
}

main() {
  mkdir -p "$DEST_DIR"
  ensure_gdown

  if [[ ! -f "$ARCHIVE_PATH" ]]; then
    echo "Downloading CBP-NG training traces archive..."
    gdown --fuzzy "$TRACE_URL" -O "$ARCHIVE_PATH"
  else
    echo "Archive already exists: $ARCHIVE_PATH"
  fi

  echo "Extracting traces under $DEST_DIR ..."
  tar xf "$ARCHIVE_PATH" -C "$DEST_DIR"

  echo "Done. Set CBPNG_TRACE_DIR=$DEST_DIR when running evaluator if needed."
}

main "$@"
