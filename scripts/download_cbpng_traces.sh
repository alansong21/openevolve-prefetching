#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIR="${CBPNG_TRACE_DIR:-$ROOT_DIR/traces/cbp-ng}"
ARCHIVE_PATH="$DEST_DIR/cbp-ng_training_traces.tar.gz"
EXTRACTED_DIR="$DEST_DIR/cbp-ng_training_traces"
TRACE_URL="https://drive.google.com/file/d/1kLKn_iKVBP-YxRpC4WiCy-ca-agU0BFG/view"
TRACE_ID="1kLKn_iKVBP-YxRpC4WiCy-ca-agU0BFG"

ensure_gdown() {
  if ! command -v gdown >/dev/null 2>&1; then
    if [[ -n "${VIRTUAL_ENV:-}" ]]; then
      echo "gdown not found; installing into active virtualenv..."
      python3 -m pip install gdown
    else
      echo "gdown not found; installing with pip..."
      python3 -m pip install --user gdown
      export PATH="$HOME/.local/bin:$PATH"
    fi
  fi
}

main() {
  mkdir -p "$DEST_DIR"
  ensure_gdown

  if [[ ! -f "$ARCHIVE_PATH" ]]; then
    echo "Downloading CBP-NG training traces archive..."
    gdown "$TRACE_ID" -O "$ARCHIVE_PATH"
  else
    echo "Archive already exists: $ARCHIVE_PATH"
  fi

  if [[ -d "$EXTRACTED_DIR" ]] && find "$EXTRACTED_DIR" -type f -name "*${CBPNG_TRACE_SUFFIX:-_trace.gz}" -print -quit | grep -q .; then
    echo "Traces already extracted under $EXTRACTED_DIR"
  else
    echo "Extracting traces under $DEST_DIR ..."
    tar xf "$ARCHIVE_PATH" -C "$DEST_DIR"
  fi

  echo "Done. Set CBPNG_TRACE_DIR=$DEST_DIR when running evaluator if needed."
}

main "$@"
