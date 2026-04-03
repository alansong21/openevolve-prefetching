#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

workflow="champsim"
iterations=5
initial_src=""

usage() {
  cat <<'USAGE'
Usage: scripts/run_openevolve_workflow.sh [--workflow champsim|cbp-ng] [--iterations N] [--initial-program PATH]

Options:
  -w, --workflow NAME      Workflow to run (default: champsim)
  -i, --iterations N       Number of iterations (default: 5)
  -p, --initial-program P  Initial program path override
  -h, --help               Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -w|--workflow)
      workflow="${2:-}"
      shift 2
      ;;
    -i|--iterations)
      iterations="${2:-}"
      shift 2
      ;;
    -p|--initial-program)
      initial_src="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "${iterations}" =~ ^[0-9]+$ ]]; then
  echo "Error: --iterations must be a positive integer" >&2
  exit 2
fi

case "$workflow" in
  champsim)
    workflow_env="champsim"
    default_initial="$REPO_ROOT/openevolve-components/next_line.cc"
    evaluator="$REPO_ROOT/openevolve-components/evaluator.py"
    config="$REPO_ROOT/openevolve-components/concise_config.yaml"
    target_initial="$REPO_ROOT/openevolve-components/initial_program.cc"
    ;;
  cbp-ng)
    workflow_env="cbp_ng"
    default_initial="$REPO_ROOT/workflows/cbp_ng/initial_program.hpp"
    evaluator="$REPO_ROOT/workflows/cbp_ng/evaluator.py"
    config="$REPO_ROOT/workflows/cbp_ng/config.yaml"
    target_initial="$REPO_ROOT/workflows/cbp_ng/initial_program.hpp"
    ;;
  *)
    echo "Error: --workflow must be champsim or cbp-ng" >&2
    exit 2
    ;;
esac

if [[ -z "$initial_src" ]]; then
  initial_src="$default_initial"
fi

if [[ ! -f "$initial_src" ]]; then
  echo "Error: initial program not found: $initial_src" >&2
  exit 1
fi

initial_src_real="$(realpath "$initial_src")"
target_initial_real="$(realpath "$target_initial")"

if [[ "$initial_src_real" != "$target_initial_real" ]]; then
  cp "$initial_src" "$target_initial"
fi

run_id="$(date +%Y%m%d_%H%M%S)_$(python3 - <<'PY'
import uuid
print(uuid.uuid4().hex[:8])
PY
)"
export OPENEVOLVE_RUN_ID="$run_id"
export OPENEVOLVE_WORKFLOW="$workflow_env"

echo "Workflow: $workflow"
echo "Run ID: $OPENEVOLVE_RUN_ID"

python3 "$REPO_ROOT/openevolve/openevolve-run.py" \
  "$target_initial" \
  "$evaluator" \
  --config "$config" \
  --iterations "$iterations"
