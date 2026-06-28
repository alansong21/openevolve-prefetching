#!/usr/bin/env bash
# Apply local OpenEvolve patches for combined-workflow multi-agent support.
#
# Expects the openevolve submodule at REPO_ROOT/openevolve (fork commit with
# context_agent + internal_agent already present, e.g. 9fbbe0e).
#
# NOTE: openevolve_combined_workflow.patch is generated against the parent repo's
# pinned fork commit (9fbbe0e). If you advance the submodule to a newer commit on
# alansong21/openevolve (e.g. origin/main with cbp-ng workflow changes), the patch
# may fail until it is rebased onto that commit. Reset the submodule to the
# parent-pinned SHA (git submodule update) and re-run this script.
#
# Run setup_shim from the openevolve-prefetching repo root so submodule init and
# this script resolve the correct tree.
#
# Adds:
#   - openevolve/mutation_agent.py
#   - combined insight routing in context_agent.py
#   - agentic mutation + bandit reward hooks in iteration.py / process_parallel.py

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OPENEVOLVE_DIR="$REPO_ROOT/openevolve"
TARGET_DIR="$OPENEVOLVE_DIR/openevolve"
PATCH_FILE="$REPO_ROOT/patches/openevolve_combined_workflow.patch"
OVERLAY_MUTATION="$REPO_ROOT/patches/openevolve/mutation_agent.py"
MARKER_FILE="$TARGET_DIR/mutation_agent.py"

log() { printf "[apply_openevolve_local_patches] %s\n" "$*"; }

if [[ ! -e "$OPENEVOLVE_DIR/.git" ]]; then
  log "openevolve submodule missing at $OPENEVOLVE_DIR — run: git submodule update --init openevolve"
  exit 1
fi

if [[ ! -f "$PATCH_FILE" ]]; then
  log "Patch file not found: $PATCH_FILE"
  exit 1
fi

if [[ ! -f "$OVERLAY_MUTATION" ]]; then
  log "Overlay file not found: $OVERLAY_MUTATION"
  exit 1
fi

if [[ ! -f "$TARGET_DIR/context_agent.py" ]]; then
  log "Fork context_agent.py missing — submodule may be wrong commit or upstream vanilla openevolve."
  log "Expected alansong21/openevolve with agent modifications (d9667a1+)."
  exit 1
fi

is_applied() {
  [[ -f "$MARKER_FILE" ]] && grep -q 'try_agentic_mutation_async' "$TARGET_DIR/iteration.py" 2>/dev/null \
    && grep -q '_fetch_combined_insights' "$TARGET_DIR/context_agent.py" 2>/dev/null
}

if is_applied; then
  log "Combined-workflow patches already applied; skipping."
  exit 0
fi

log "Applying combined-workflow patch to openevolve submodule ..."
cd "$OPENEVOLVE_DIR"
if ! patch -p1 --forward --dry-run --silent < "$PATCH_FILE"; then
  log "ERROR: patch dry-run failed. Submodule may be on an incompatible commit."
  log "       Reset openevolve to the parent-pinned commit and re-run."
  exit 1
fi
patch -p1 --forward --silent < "$PATCH_FILE"

log "Installing mutation_agent.py overlay ..."
install -m 0644 "$OVERLAY_MUTATION" "$MARKER_FILE"

if ! is_applied; then
  log "ERROR: patches applied but verification failed."
  exit 1
fi

log "Done. OpenEvolve combined-workflow hooks are installed."
