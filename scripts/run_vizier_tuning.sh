#!/usr/bin/env bash
set -euo pipefail

# Thin launcher for the Vizier hyperparameter tuning stage.
# Runs Stage A (LLM hyperparameter identification) + Stage B (Vizier search)
# on an evolved program, reusing the workflow's own evaluator.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

workflow="champsim"
trials=""
program=""
extra_args=()

usage() {
  cat <<'USAGE'
Usage: scripts/run_vizier_tuning.sh [--workflow champsim|cbp-ng|combined] [--trials N] [--program PATH] [-- <extra args>]

Options:
  -w, --workflow NAME    Workflow whose evaluator/best program to tune (default: champsim)
  -t, --trials N         Number of Vizier trials (default: from vizier_tuning_config.yaml)
  -p, --program PATH     Evolved program path (default: workflow best_program)
  -h, --help             Show this help

Any arguments after `--` are passed through to python -m vizier_tuning.run_vizier_tuning
(e.g. --dry-run, --identifier-model, --output-dir, --run-id, --log-level).
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -w|--workflow) workflow="${2:-}"; shift 2 ;;
    -t|--trials) trials="${2:-}"; shift 2 ;;
    -p|--program) program="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    --) shift; extra_args=("$@"); break ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

cmd=(python3 -m vizier_tuning.run_vizier_tuning --workflow "$workflow")
if [[ -n "$trials" ]]; then
  cmd+=(--trials "$trials")
fi
if [[ -n "$program" ]]; then
  cmd+=(--program "$program")
fi
if [[ ${#extra_args[@]} -gt 0 ]]; then
  cmd+=("${extra_args[@]}")
fi

cd "$REPO_ROOT"
echo "Running: ${cmd[*]}"
exec "${cmd[@]}"
