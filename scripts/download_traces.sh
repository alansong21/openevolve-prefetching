#!/usr/bin/env bash
set -euo pipefail

# Script to fetch all trace folders into DPC4-Traces with the required layout.
# 0) Installs gdown if missing and ensures ~/.local/bin is on PATH.
# 1) Creates DPC4-Traces at the repo root.
# 2) Downloads the requested trace folders.
# 3) Bundles Ligra, GMS, and GAP under DPC4-Traces/Graph.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_ROOT="$ROOT_DIR/DPC4-Traces"
GRAPH_DIR="$DEST_ROOT/Graph"

AI_URL="https://drive.google.com/drive/folders/1rjNIj7xZDKYcqmsNuUgFQcmUDYfeY1M0?usp=drive_link"
SPEC17_URL="https://drive.google.com/drive/folders/1AQ05VQnoBHlh64q4akaTHfYaACiRxm9c?usp=drive_link"
GOOGLE_URL="https://drive.google.com/drive/folders/1Sh6CE7bfZQgVYZ5lSUwmWggR2wGcFyjJ?usp=drive_link"
LIGRA_URL="https://drive.google.com/drive/folders/1AlXOv-FB66bornbG-DeH8lCYWYV8G8v0?usp=drive_link"
GMS_URL="https://drive.google.com/drive/folders/1W93OQn3ObJDJcnNmQrmL3LrRokYDCTG7?usp=drive_link"
GAP_URL="https://drive.google.com/drive/folders/1pj9Tq-lz3TEbztHUdzUoT33wzk1Yqb_r?usp=drive_link"

ensure_gdown() {
  if ! command -v gdown >/dev/null 2>&1; then
    echo "gdown not found; installing with pip..."
    python3 -m pip install --user gdown
    export PATH="$HOME/.local/bin:$PATH"
  fi
}

download_folder() {
  local url="$1"
  local dest="$2"

  # gdown --folder downloads contents into the provided destination directory.
  mkdir -p "$dest"
  gdown --fuzzy --folder --remaining-ok "$url" -O "$dest"
}

main() {
  ensure_gdown

  mkdir -p "$DEST_ROOT" "$GRAPH_DIR"

  download_folder "$AI_URL" "$DEST_ROOT/AI-ML"
  download_folder "$SPEC17_URL" "$DEST_ROOT/SPEC17"
  download_folder "$GOOGLE_URL" "$DEST_ROOT/Google-Traces-v2"

  download_folder "$LIGRA_URL" "$GRAPH_DIR/Ligra"
  download_folder "$GMS_URL" "$GRAPH_DIR/GMS"
  download_folder "$GAP_URL" "$GRAPH_DIR/GAP"

  echo "All traces downloaded under $DEST_ROOT"
}

main "$@"
