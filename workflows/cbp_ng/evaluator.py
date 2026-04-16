"""CBP-NG evaluator glue for OpenEvolve."""

from __future__ import annotations

import concurrent.futures
import csv
import math
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from openevolve.evaluation_result import EvaluationResult

REPO_ROOT = Path(__file__).resolve().parents[2]
CBPNG_ROOT = REPO_ROOT / "cbp-ng"
PROGRAM_TARGET = Path(__file__).with_name("initial_program.hpp")
BRIDGE_HEADER = CBPNG_ROOT / "predictors" / "openevolve_predictor.hpp"
CBPNG_BIN = CBPNG_ROOT / "cbp"

TRACE_DIR = Path(os.environ.get("CBPNG_TRACE_DIR", REPO_ROOT / "traces" / "cbp-ng"))
TRACE_SUFFIX = os.environ.get("CBPNG_TRACE_SUFFIX", "_trace.gz")
TOOLCHAIN_BIN = Path(os.environ.get("CBPNG_TOOLCHAIN_BIN", REPO_ROOT / "toolchains" / "cbp_ng" / "bin"))
RUN_TIMEOUT = int(os.environ.get("CBPNG_TIMEOUT", 0))
BUILD_TIMEOUT = int(os.environ.get("CBPNG_BUILD_TIMEOUT", 300))
WARMUP_INSTRUCTIONS = int(os.environ.get("CBPNG_WARMUP_INSTR", 1_000_000))
SIM_INSTRUCTIONS = int(os.environ.get("CBPNG_SIM_INSTR", 40_000_000))
TRACE_ITERATIONS = max(1, int(os.environ.get("CBPNG_TRACE_ITERATIONS", 1)))
MAX_WORKERS = int(os.environ.get("CBPNG_TRACE_WORKERS", 16))
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
    EVAL_LOG_ROOT = Path(__file__).with_name("openevolve_output") / "runs" / RUN_ID / "cbp_ng"
else:
    EVAL_LOG_ROOT = Path(__file__).with_name("openevolve_output") / "logs" / "cbp_ng"


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


def _run_command(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
    label: str,
    log_path: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, float]:
    start = time.time()
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=None if timeout <= 0 else timeout,
        check=False,
        env=env,
    )
    output = (completed.stdout or "") + (completed.stderr or "")

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{label}] cmd={' '.join(cmd)}\n")
            handle.write(output)
            if not output.endswith("\n"):
                handle.write("\n")

    if completed.returncode != 0:
        raise subprocess.CalledProcessError(completed.returncode, cmd, output)
    return output, time.time() - start


def _copy_candidate(program_path: Path) -> None:
    shutil.copy(program_path, PROGRAM_TARGET)


def _ensure_prerequisites(traces: list[Path]) -> None:
    if not CBPNG_ROOT.exists():
        raise FileNotFoundError(f"CBP-NG root not found at {CBPNG_ROOT}")
    if not BRIDGE_HEADER.exists():
        raise FileNotFoundError(f"Missing bridge header: {BRIDGE_HEADER}")
    if not PROGRAM_TARGET.exists():
        raise FileNotFoundError(f"Seed program not found: {PROGRAM_TARGET}")
    missing = [trace for trace in traces if not trace.exists()]
    if missing:
        missing_list = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Trace file(s) missing: {missing_list}")


def _build_cbpng(log_path: Path | None = None) -> tuple[str, float]:
    build_env = os.environ.copy()
    if TOOLCHAIN_BIN.is_dir():
        build_env["PATH"] = f"{TOOLCHAIN_BIN}:{build_env.get('PATH', '')}"
    cmd = [
        "./compile",
        "cbp",
        "-include",
        "predictors/openevolve_predictor.hpp",
        "-DPREDICTOR=openevolve_predictor",
    ]
    return _run_command(cmd, cwd=CBPNG_ROOT, timeout=BUILD_TIMEOUT, label="CBP-NG build", log_path=log_path, env=build_env)


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


def _run_trace(trace: Path, log_path: Path, iteration: int) -> tuple[dict[str, Any], float, str]:
    if not CBPNG_BIN.exists():
        raise FileNotFoundError(f"CBP-NG binary missing: {CBPNG_BIN}")

    cmd = [
        str(CBPNG_BIN),
        str(trace),
        _trace_name(trace),
        str(WARMUP_INSTRUCTIONS),
        str(SIM_INSTRUCTIONS),
        "--format",
        "csv",
    ]
    label = f"CBP-NG run ({trace.name}, iter {iteration})"
    stdout, elapsed = _run_command(cmd, cwd=CBPNG_ROOT, timeout=RUN_TIMEOUT, label=label, log_path=log_path)
    parsed = _parse_csv_row(stdout)
    return parsed, elapsed, stdout


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

    try:
        _ensure_prerequisites(traces)
        _copy_candidate(Path(program_path))
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_result(f"Setup failed: {exc}")

    try:
        build_log_path = run_log_dir / "build.log"
        build_stdout, build_time = _build_cbpng(build_log_path)
        _print_console_log("CBP-NG build", build_stdout)
    except subprocess.CalledProcessError as exc:
        return _failure_result(
            f"CBP-NG build failed with exit code {exc.returncode}",
            build_log=exc.stdout or "",
        )
    except subprocess.TimeoutExpired as exc:
        return _failure_result(f"CBP-NG build timed out after {exc.timeout} seconds")
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_result(f"CBP-NG build failed: {exc}")

    trace_data: dict[str, list[dict[str, Any]]] = {trace.name: [] for trace in traces}
    trace_times: dict[str, list[float]] = {trace.name: [] for trace in traces}
    trace_logs: dict[str, list[str]] = {trace.name: [] for trace in traces}

    workers = max(1, min(len(traces), MAX_WORKERS, os.cpu_count() or 1))
    print(f"Running CBP-NG in parallel for {len(traces)} traces using {workers} workers")

    try:
        for iteration in range(1, TRACE_ITERATIONS + 1):
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(_run_trace, trace, run_log_dir / f"{trace.name}.log", iteration): trace
                    for trace in traces
                }

                done, pending = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_EXCEPTION)
                failed = next((future for future in done if future.exception()), None)
                if failed is not None:
                    for future in pending:
                        future.cancel()
                    raise RuntimeError(f"CBP-NG run failed during iteration {iteration}: {failed.exception()}")

                concurrent.futures.wait(futures)
                for future, trace in futures.items():
                    parsed, elapsed, stdout = future.result()
                    trace_data[trace.name].append(parsed)
                    trace_times[trace.name].append(elapsed)
                    trace_logs[trace.name].append(stdout)
                    _print_console_log(f"CBP-NG run ({trace.name})", stdout)

    except Exception as exc:  # pylint: disable=broad-except
        return _failure_result(
            f"CBP-NG parallel run failed: {exc}",
            build_log=build_stdout,
        )

    per_trace_aggregate: dict[str, dict[str, float]] = {}
    for trace in traces:
        per_trace_aggregate[trace.name] = _compute_aggregate(trace_data[trace.name])

    all_rows: list[dict[str, Any]] = []
    for rows in trace_data.values():
        all_rows.extend(rows)
    aggregate = _compute_aggregate(all_rows)
    vfs = _compute_vfs(
        aggregate["avg_IPC"],
        aggregate["avg_CPI"],
        aggregate["avg_EPI"],
    )

    wall_time = time.time() - start
    sim_time = max((max(times) for times in trace_times.values() if times), default=0.0)

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
    }

    trace_results: dict[str, Any] = {}
    for idx, trace in enumerate(traces, start=1):
        trace_metrics = per_trace_aggregate[trace.name]
        metrics[f"trace_{idx}_ipc_cbp"] = trace_metrics["avg_IPC"]
        metrics[f"trace_{idx}_cpi_cbp"] = trace_metrics["avg_CPI"]
        trace_results[f"trace_{idx}_name"] = trace.name
        trace_results[f"trace_{idx}_ipc_cbp"] = trace_metrics["avg_IPC"]
        trace_results[f"trace_{idx}_cpi_cbp"] = trace_metrics["avg_CPI"]
        trace_results[f"trace_{idx}_epi_cbp"] = trace_metrics["avg_EPI"]
        trace_results[f"trace_{idx}_log"] = _trim_log("\n\n".join(trace_logs[trace.name]))
        trace_results[f"trace_{idx}_log_path"] = str(run_log_dir / f"{trace.name}.log")

    artifacts = {
        "build_log": _trim_log(build_stdout),
        "num_traces": len(traces),
        "trace_iterations": TRACE_ITERATIONS,
        "trace_results": trace_results,
    }

    summary_lines = [
        "[CBP-NG summary]",
        f"vfs={vfs}",
        f"ipc_cbp={aggregate['avg_IPC']}",
        f"cpi_cbp={aggregate['avg_CPI']}",
        f"epi_cbp={aggregate['avg_EPI']}",
        f"build_time_s={build_time}",
        f"sim_time_s={sim_time}",
        f"wall_time_s={wall_time}",
        f"traces_evaluated={len(traces)}",
        f"trace_iterations={TRACE_ITERATIONS}",
    ]
    summary_payload = "\n".join(summary_lines) + "\n"
    for trace in traces:
        log_path = run_log_dir / f"{trace.name}.log"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(summary_payload)

    return EvaluationResult(metrics=metrics, artifacts=artifacts)
