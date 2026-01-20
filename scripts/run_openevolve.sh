#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

iterations=5

usage() {
  cat <<'EOF'
Usage: scripts/run_openevolve.sh [--iterations N]

Options:
  -i, --iterations N  Number of iterations to run (default: 5)
  -h, --help          Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i|--iterations)
      iterations="${2:-}"
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

if [[ -z "${iterations}" || "${iterations}" == -* ]]; then
  echo "Error: --iterations requires a positive integer." >&2
  exit 2
fi

if ! [[ "${iterations}" =~ ^[0-9]+$ ]]; then
  echo "Error: --iterations must be an integer." >&2
  exit 2
fi

components_dir="${REPO_ROOT}/openevolve-components"
next_line_src="${components_dir}/next_line.cc"
initial_program="${components_dir}/initial_program.cc"
run_id="$(date +%Y%m%d_%H%M%S)_$(python - <<'PY'
import uuid
print(uuid.uuid4().hex[:8])
PY
)"

if [[ ! -f "${next_line_src}" ]]; then
  echo "Error: missing ${next_line_src}" >&2
  exit 1
fi

cp "${next_line_src}" "${initial_program}"

export OPENEVOLVE_RUN_ID="${run_id}"
echo "Run ID: ${OPENEVOLVE_RUN_ID}"

python "${REPO_ROOT}/openevolve/openevolve-run.py" \
  "${initial_program}" \
  "${components_dir}/evaluator.py" \
  --config "${components_dir}/concise_config.yaml" \
  --iterations "${iterations}"
