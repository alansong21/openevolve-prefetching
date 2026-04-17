#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

iterations=5
initial_src=""
context_agent="programmatic"
context_bundle="champsim"

usage() {
  cat <<'EOF'
Usage: scripts/run_openevolve.sh [--iterations N] [--initial-program PATH|ipcp] [--context-agent programmatic|langchain] [--context-bundle champsim|cbp_ng]

Options:
  -i, --iterations N  Number of iterations to run (default: 5)
  -p, --initial-program PATH|ipcp  Initial prefetcher program to copy in (default: next_line.cc)
  -c, --context-agent programmatic|langchain  Context agent mode (default: programmatic)
  -b, --context-bundle champsim|cbp_ng  Explicit context bundle (default: champsim)
  -h, --help          Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i|--iterations)
      iterations="${2:-}"
      shift 2
      ;;
    -p|--initial-program)
      initial_src="${2:-}"
      shift 2
      ;;
    -c|--context-agent)
      context_agent="${2:-}"
      shift 2
      ;;
    -b|--context-bundle)
      context_bundle="${2:-}"
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

case "${context_agent}" in
  programmatic|langchain)
    ;;
  *)
    echo "Error: --context-agent must be 'programmatic' or 'langchain'." >&2
    exit 2
    ;;
esac

case "${context_bundle}" in
  champsim|cbp_ng)
    ;;
  *)
    echo "Error: --context-bundle must be 'champsim' or 'cbp_ng'." >&2
    exit 2
    ;;
esac

components_dir="${REPO_ROOT}/openevolve-components"
next_line_src="${components_dir}/next_line.cc"
ipcp_src="${components_dir}/ipcp_l2c.cc"
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

if [[ -z "${initial_src}" ]]; then
  initial_src="${next_line_src}"
fi

if [[ "${initial_src}" == "ipcp" ]]; then
  initial_src="${ipcp_src}"
fi

if [[ ! -f "${initial_src}" ]]; then
  echo "Error: initial program not found: ${initial_src}" >&2
  exit 1
fi

cp "${initial_src}" "${initial_program}"

export OPENEVOLVE_RUN_ID="${run_id}"
export OPENEvolve_CONTEXT_AGENT="${context_agent}"
export OPENEvolve_CONTEXT_BUNDLE="${context_bundle}"
export OPENEVOLVE_WORKFLOW="champsim"
echo "Run ID: ${OPENEVOLVE_RUN_ID}"

python "${REPO_ROOT}/openevolve/openevolve-run.py" \
  "${initial_program}" \
  "${components_dir}/evaluator.py" \
  --config "${components_dir}/concise_config.yaml" \
  --iterations "${iterations}"
