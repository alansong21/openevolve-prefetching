"""CBP-NG evaluator glue for OpenEvolve using CloudLab distributed execution."""

from __future__ import annotations

import concurrent.futures
import csv
import math
import os
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from openevolve.evaluation_result import EvaluationResult

CLOUDLAB_LIB_PATH = Path(__file__).resolve().parents[2] / "cloudlab-lib"
if str(CLOUDLAB_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(CLOUDLAB_LIB_PATH))

from cloudlab_lib import CloudLabAgent

REPO_ROOT = Path(__file__).resolve().parents[2]
CBPNG_ROOT = REPO_ROOT / "cbp-ng"
PROGRAM_TARGET = Path(__file__).with_name("initial_program.hpp")
BRIDGE_HEADER = CBPNG_ROOT / "predictors" / "openevolve_predictor.hpp"
CBPNG_BIN = CBPNG_ROOT / "cbp"
CLOUDLAB_CONFIG = REPO_ROOT / "cloudlab-lib" / "server-config.json"

REMOTE_REPO_ROOT = Path(os.environ.get("CLOUDLAB_REPO_ROOT", str(REPO_ROOT)))

TRACE_DIR = Path(os.environ.get("CBPNG_TRACE_DIR", REPO_ROOT / "traces" / "cbp-ng"))
TRACE_SUFFIX = os.environ.get("CBPNG_TRACE_SUFFIX", "_trace.gz")
RUN_TIMEOUT = int(os.environ.get("CBPNG_TIMEOUT", 0))
BUILD_TIMEOUT = int(os.environ.get("CBPNG_BUILD_TIMEOUT", 300))
WARMUP_INSTRUCTIONS = int(os.environ.get("CBPNG_WARMUP_INSTR", 1_000_000))
SIM_INSTRUCTIONS = int(os.environ.get("CBPNG_SIM_INSTR", 40_000_000))
TRACE_ITERATIONS = max(1, int(os.environ.get("CBPNG_TRACE_ITERATIONS", 1)))
STREAM_LOGS = os.environ.get("CBPNG_STREAM_LOGS", "true").lower() in ("1", "true", "yes", "on")
CONSOLE_LOG_LIMIT = int(os.environ.get("CBPNG_CONSOLE_LOG_LIMIT", 4000))
MISPREDICTION_PENALTY = 8.0
IPC_CBP0 = 8.0
CPI_CBP0 = 0.0315
EPI_CBP0 = 1000.0
ALPHA = 1.625
BETA = 4 * ALPHA / (ALPHA - 1) ** 2
GAMMA = 2 / (ALPHA - 1)
CBP_ENERGY_RATIO = 0.05

RUN_ID = os.environ.get("OPENEVOLVE_RUN_ID", "").strip()
if RUN_ID:
    EVAL_LOG_ROOT = Path(__file__).with_name("openevolve_output") / "runs" / RUN_ID / "cbp_ng_cloudlab"
else:
    EVAL_LOG_ROOT = Path(__file__).with_name("openevolve_output") / "logs" / "cbp_ng_cloudlab"


def _discover_traces() -> list[Path]:
    if not TRACE_DIR.exists() or not TRACE_DIR.is_dir():
        return []
    traces = [path for path in TRACE_DIR.rglob("*") if path.is_file() and path.name.endswith(TRACE_SUFFIX)]
    return sorted(traces)


def _trim_log(payload: str, limit: int = 20000) -> str:
    if len(payload) <= limit:
        return payload
    return payload[-limit:]


def _print_console_log(label: str, payload: str) -> None:
    if STREAM_LOGS or not payload:
        return
    banner = f"---- {label} ----"
    print(banner)
    print(_trim_log(payload, CONSOLE_LOG_LIMIT).rstrip())
    print("-" * len(banner))


def _trace_name(trace: Path) -> str:
    name = trace.name
    if name.endswith(".gz"):
        name = name[: -len(".gz")]
    if name.endswith("_trace"):
        name = name[: -len("_trace")]
    return name


def _parse_csv_row(stdout: str) -> dict[str, Any]:
    first_line = ""
    for line in stdout.splitlines():
        if line.strip():
            first_line = line.strip()
            break
    if not first_line:
        raise ValueError("CBP-NG output is empty")

    row = next(csv.reader([first_line]))
    if len(row) != 12:
        raise ValueError(f"Unexpected CBP-NG CSV format: expected 12 columns, got {len(row)}")

    return {
        "name": row[0],
        "instructions": float(row[1]),
        "branches": float(row[2]),
        "cond_branches": float(row[3]),
        "pred_cycles": float(row[4]),
        "extra_cycles": float(row[5]),
        "divergences": float(row[6]),
        "divergences_at_block_end": float(row[7]),
        "p2_mispredictions": float(row[8]),
        "p1_latency": float(row[9]),
        "p2_latency": float(row[10]),
        "epi": float(row[11]),
        "raw_line": first_line,
    }


def _failure_result(message: str, **artifacts: str) -> EvaluationResult:
    payload = {"error": message}
    payload.update({k: _trim_log(v) for k, v in artifacts.items() if v})
    return EvaluationResult(
        metrics={"combined_score": 0.0, "vfs": 0.0, "ipc_cbp": 0.0},
        artifacts=payload,
    )


def _compute_vfs(ipc_cbp: float, cpi_cbp: float, epi_cbp: float) -> float:
    if ipc_cbp <= 0 or cpi_cbp < 0 or epi_cbp <= 0:
        return 0.0

    wpi0 = IPC_CBP0 * CPI_CBP0
    wpi = ipc_cbp * cpi_cbp
    speedup = (ipc_cbp / IPC_CBP0) * (1 + wpi0) / (1 + wpi)

    lambda_term = 1 / (1 + wpi0 / 2) - CBP_ENERGY_RATIO
    normalized_epi = ((epi_cbp / EPI_CBP0) * CBP_ENERGY_RATIO + lambda_term * speedup**GAMMA) * (1 + wpi / 2)

    inner = 1 + BETA / (speedup * normalized_epi)
    vfs = speedup * ALPHA * (1 - 2 / (1 + math.sqrt(inner)))
    return max(vfs, 0.0)


def _compute_aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    if not rows:
        return {
            "avg_IPC": 0.0,
            "avg_CPI": 0.0,
            "avg_EPI": 0.0,
            "avg_MPI": 0.0,
            "avg_DPI": 0.0,
            "avg_PPI": 0.0,
            "p1_latency": 0.0,
            "p2_latency": 0.0,
        }

    p1_latency = max(math.ceil(row["p1_latency"]) for row in rows)
    p2_latency = max(math.ceil(row["p2_latency"]) for row in rows)

    count = 0
    inv_ipc_sum = 0.0
    cpi_sum = 0.0
    epi_sum = 0.0
    mpi_sum = 0.0
    dpi_sum = 0.0
    ppi_sum = 0.0

    for row in rows:
        instructions = row["instructions"]
        pred_cycles = row["pred_cycles"]
        extra_cycles = row["extra_cycles"]
        divergences = row["divergences"]
        divergences_at_end = row["divergences_at_block_end"]
        p2_mispredictions = row["p2_mispredictions"]

        mpi = p2_mispredictions / instructions
        epi = row["epi"]

        if p2_latency <= p1_latency:
            cycles = pred_cycles * max(1, p2_latency)
        else:
            cycles = pred_cycles * max(1, p1_latency) + divergences * p2_latency - divergences_at_end * max(1, p1_latency)
        cycles += extra_cycles

        ipc = instructions / cycles
        cpi = mpi * (MISPREDICTION_PENALTY + p2_latency)

        count += 1
        inv_ipc_sum += 1.0 / ipc
        cpi_sum += cpi
        epi_sum += epi
        mpi_sum += mpi
        dpi_sum += divergences / instructions
        ppi_sum += pred_cycles / instructions

    return {
        "avg_IPC": count / inv_ipc_sum,
        "avg_CPI": cpi_sum / count,
        "avg_EPI": epi_sum / count,
        "avg_MPI": mpi_sum / count,
        "avg_DPI": dpi_sum / count,
        "avg_PPI": ppi_sum / count,
        "p1_latency": float(p1_latency),
        "p2_latency": float(p2_latency),
    }


def _map_traces_to_nodes(traces: list[Path], nodes: list[str]) -> dict[str, list[Path]]:
    if not nodes:
        raise ValueError("No nodes available for trace distribution")

    node_to_traces: dict[str, list[Path]] = defaultdict(list)
    for idx, trace in enumerate(traces):
        node_to_traces[nodes[idx % len(nodes)]].append(trace)
    return dict(node_to_traces)


def _remote_path(local_path: Path) -> Path:
    return REMOTE_REPO_ROOT / local_path.relative_to(REPO_ROOT)


def _run_remote(agent: CloudLabAgent, node: str, cmd: str) -> tuple[bool, str]:
    stdout, stderr, exit_status = agent.run_on_node(node, cmd)
    output = "".join(stdout) + "".join(stderr)
    return exit_status == 0, output


def _ensure_prerequisites_remote(agent: CloudLabAgent, node: str) -> tuple[bool, str]:
    remote_program = _remote_path(PROGRAM_TARGET)
    remote_bridge = _remote_path(BRIDGE_HEADER)
    remote_cbp_root = _remote_path(CBPNG_ROOT)
    remote_trace_dir = _remote_path(TRACE_DIR) if TRACE_DIR.is_relative_to(REPO_ROOT) else TRACE_DIR

    cmd = f"""
        cd {REMOTE_REPO_ROOT}
        if [ ! -d "{remote_cbp_root}" ]; then
            echo "ERROR: CBP-NG root not found at {remote_cbp_root}"
            exit 1
        fi
        if [ ! -f "{remote_program}" ]; then
            echo "ERROR: Initial program missing at {remote_program}"
            exit 1
        fi
        if [ ! -d "{remote_trace_dir}" ]; then
            echo "ERROR: Trace directory missing at {remote_trace_dir}"
            exit 1
        fi
        mkdir -p "$(dirname "{remote_bridge}")"
        cat > "{remote_bridge}" <<'EOF'
#pragma once

#include "../../workflows/cbp_ng/initial_program.hpp"
EOF
        echo "Prerequisites check passed"
    """
    return _run_remote(agent, node, cmd)


def _copy_candidate_remote(agent: CloudLabAgent, node: str, local_program_path: Path) -> None:
    remote_program_path = _remote_path(PROGRAM_TARGET)
    agent.scp(node, str(local_program_path), str(remote_program_path))


def _invalidate_build_remote(agent: CloudLabAgent, node: str) -> None:
    remote_cbp_bin = _remote_path(CBPNG_BIN)
    cmd = f"""
        cd {REMOTE_REPO_ROOT}
        rm -f "{remote_cbp_bin}" || true
        echo "Invalidated CBP-NG binary"
    """
    agent.run_on_node(node, cmd)


def _build_cbpng_remote(agent: CloudLabAgent, node: str) -> tuple[bool, str, float]:
    start = time.time()
    timeout_prefix = f"timeout {BUILD_TIMEOUT} " if BUILD_TIMEOUT > 0 else ""
    remote_cbp_root = _remote_path(CBPNG_ROOT)
    cmd = f"""
        cd {remote_cbp_root}
        {timeout_prefix}./compile cbp -include predictors/openevolve_predictor.hpp -DPREDICTOR=openevolve_predictor
    """
    success, output = _run_remote(agent, node, cmd)
    return success, output, time.time() - start


def _run_trace_remote(agent: CloudLabAgent, node: str, trace: Path, iteration: int) -> tuple[bool, str, float, str]:
    start = time.time()
    remote_cbp_bin = _remote_path(CBPNG_BIN)
    remote_trace = _remote_path(trace) if trace.is_relative_to(REPO_ROOT) else trace
    timeout_prefix = f"timeout {RUN_TIMEOUT} " if RUN_TIMEOUT > 0 else ""
    cmd = f"""
        cd {REMOTE_REPO_ROOT}
        if [ ! -f "{remote_cbp_bin}" ]; then
            echo "ERROR: CBP-NG binary missing at {remote_cbp_bin}"
            exit 1
        fi
        if [ ! -f "{remote_trace}" ]; then
            echo "ERROR: Trace file not found at {remote_trace}"
            exit 1
        fi
        {timeout_prefix}{remote_cbp_bin} "{remote_trace}" "{_trace_name(trace)}" {WARMUP_INSTRUCTIONS} {SIM_INSTRUCTIONS} --format csv
    """
    success, output = _run_remote(agent, node, cmd)
    return success, output, time.time() - start, trace.name


def evaluate(program_path: str) -> EvaluationResult:
    start = time.time()
    traces = _discover_traces()
    run_id = time.strftime("%Y%m%d_%H%M%S") + f"_{uuid.uuid4().hex[:8]}"
    run_log_dir = EVAL_LOG_ROOT / run_id
    run_log_dir.mkdir(parents=True, exist_ok=True)

    if not traces:
        return _failure_result(
            f"No CBP-NG traces found under {TRACE_DIR} with suffix '{TRACE_SUFFIX}'. "
            "Set CBPNG_TRACE_DIR/CBPNG_TRACE_SUFFIX to adjust discovery."
        )

    if not CLOUDLAB_CONFIG.exists():
        return _failure_result(f"CloudLab config file not found at {CLOUDLAB_CONFIG}")

    try:
        agent = CloudLabAgent(str(CLOUDLAB_CONFIG))
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_result(f"Failed to initialize CloudLab agent: {exc}")

    available_nodes = [node for node in agent.nodes_ if node not in agent.unconnected_nodes_]
    if not available_nodes:
        return _failure_result("No connected nodes available in CloudLab cluster")

    try:
        _copy_candidate = Path(program_path)
        if not _copy_candidate.exists():
            return _failure_result(f"Candidate program not found: {_copy_candidate}")
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_result(f"Setup failed: {exc}")

    node_to_traces = _map_traces_to_nodes(traces, available_nodes)
    print(f"Using {len(available_nodes)} CloudLab nodes: {available_nodes}")
    print(f"Trace distribution: {[(node, [trace.name for trace in items]) for node, items in node_to_traces.items()]}")

    def setup_node(node: str) -> tuple[str, bool, str, float]:
        try:
            _copy_candidate_remote(agent, node, Path(program_path))

            success, output = _ensure_prerequisites_remote(agent, node)
            if not success:
                return node, False, output, 0.0

            _invalidate_build_remote(agent, node)
            success, build_output, build_time = _build_cbpng_remote(agent, node)
            return node, success, build_output, build_time
        except Exception as exc:  # pylint: disable=broad-except
            return node, False, f"Setup exception: {exc}", 0.0

    node_build_results: dict[str, tuple[bool, str]] = {}
    node_build_times: dict[str, float] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(node_to_traces)) as executor:
        futures = {executor.submit(setup_node, node): node for node in node_to_traces}
        for future in concurrent.futures.as_completed(futures):
            node, success, output, build_time = future.result()
            node_build_results[node] = (success, output)
            node_build_times[node] = build_time
            _print_console_log(f"Node {node} build", output)

    successful_nodes = [node for node, (success, _) in node_build_results.items() if success]
    if not successful_nodes:
        return _failure_result(
            "All node builds failed",
            build_log="\n".join(f"Node {node}:\n{output}" for node, (_, output) in node_build_results.items()),
        )

    trace_data: dict[str, list[dict[str, Any]]] = {trace.name: [] for trace in traces}
    trace_times: dict[str, list[float]] = {trace.name: [] for trace in traces}
    trace_logs: dict[str, list[str]] = {trace.name: [] for trace in traces}
    trace_nodes: dict[str, str] = {}

    def run_node_traces(node: str, assigned_traces: list[Path]) -> list[tuple[Path, int, dict[str, Any], float, str, str]]:
        results: list[tuple[Path, int, dict[str, Any], float, str, str]] = []
        if node not in successful_nodes:
            return results

        for iteration in range(1, TRACE_ITERATIONS + 1):
            for trace in assigned_traces:
                success, output, elapsed, trace_name = _run_trace_remote(agent, node, trace, iteration)
                if not success:
                    raise RuntimeError(f"Node {node} trace {trace_name} failed: {output}")
                parsed = _parse_csv_row(output)
                results.append((trace, iteration, parsed, elapsed, output, node))
        return results

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(node_to_traces)) as executor:
            futures = {
                executor.submit(run_node_traces, node, assigned_traces): node
                for node, assigned_traces in node_to_traces.items()
            }
            for future in concurrent.futures.as_completed(futures):
                node_results = future.result()
                for trace, _iteration, parsed, elapsed, output, node in node_results:
                    trace_data[trace.name].append(parsed)
                    trace_times[trace.name].append(elapsed)
                    trace_logs[trace.name].append(output)
                    trace_nodes[trace.name] = node
                    _print_console_log(f"Node {node} trace {trace.name}", output)
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_result(
            f"CBP-NG CloudLab run failed: {exc}",
            build_log="\n".join(f"Node {node}:\n{output}" for node, (_, output) in node_build_results.items()),
        )

    per_trace_aggregate: dict[str, dict[str, float]] = {
        trace.name: _compute_aggregate(trace_data[trace.name]) for trace in traces
    }

    all_rows: list[dict[str, Any]] = []
    for rows in trace_data.values():
        all_rows.extend(rows)
    aggregate = _compute_aggregate(all_rows)
    vfs = _compute_vfs(aggregate["avg_IPC"], aggregate["avg_CPI"], aggregate["avg_EPI"])

    wall_time = time.time() - start
    sim_time = max((max(times) for times in trace_times.values() if times), default=0.0)
    build_time = max(node_build_times.values(), default=0.0)

    metrics: dict[str, float] = {
        "vfs": vfs,
        "ipc_cbp": aggregate["avg_IPC"],
        "combined_score": vfs,
        "cpi_cbp": aggregate["avg_CPI"],
        "epi_cbp": aggregate["avg_EPI"],
        "mpi": aggregate["avg_MPI"],
        "dpi": aggregate["avg_DPI"],
        "ppi": aggregate["avg_PPI"],
        "p1_latency": aggregate["p1_latency"],
        "p2_latency": aggregate["p2_latency"],
        "build_time_s": build_time,
        "sim_time_s": sim_time,
        "wall_time_s": wall_time,
        "traces_evaluated": float(len(traces)),
        "trace_iterations": float(TRACE_ITERATIONS),
        "cloudlab_nodes_used": float(len(successful_nodes)),
    }

    trace_results: dict[str, Any] = {}
    for idx, trace in enumerate(traces, start=1):
        trace_metrics = per_trace_aggregate[trace.name]
        metrics[f"trace_{idx}_ipc_cbp"] = trace_metrics["avg_IPC"]
        metrics[f"trace_{idx}_cpi_cbp"] = trace_metrics["avg_CPI"]
        trace_results[f"trace_{idx}_name"] = trace.name
        trace_results[f"trace_{idx}_node"] = trace_nodes.get(trace.name, "")
        trace_results[f"trace_{idx}_ipc_cbp"] = trace_metrics["avg_IPC"]
        trace_results[f"trace_{idx}_cpi_cbp"] = trace_metrics["avg_CPI"]
        trace_results[f"trace_{idx}_epi_cbp"] = trace_metrics["avg_EPI"]
        trace_results[f"trace_{idx}_log"] = _trim_log("\n\n".join(trace_logs[trace.name]))

    artifacts = {
        "build_log": "\n".join(f"Node {node}:\n{_trim_log(output)}" for node, (_, output) in node_build_results.items()),
        "num_traces": len(traces),
        "trace_iterations": TRACE_ITERATIONS,
        "cloudlab_nodes": available_nodes,
        "trace_results": trace_results,
    }

    return EvaluationResult(metrics=metrics, artifacts=artifacts)
