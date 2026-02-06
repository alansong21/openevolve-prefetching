"""ChampSim evaluator glue for OpenEvolve."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import concurrent.futures
import uuid
from pathlib import Path
from typing import Tuple
import select

from openevolve.evaluation_result import EvaluationResult

REPO_ROOT = Path(__file__).resolve().parents[1]
CHAMPSIM_ROOT = REPO_ROOT / "ChampSim"
PREFETCHER_CC = Path(__file__).with_name("initial_program.cc")
PREFETCHER_OBJ_DIR = CHAMPSIM_ROOT / ".csconfig" / "modules" / "prefetcher" / "openevolve_prefetcher"
CONFIG_PATH = Path(__file__).with_name("champsim_config.json").resolve()
# Manual definition of traces to evaluate
# You can add your traces here
TRACES = [
    # Default to the single included trace in the repo
    REPO_ROOT / "400.perlbench-41B.champsimtrace.xz",
    REPO_ROOT / "403.gcc-48B.champsimtrace.xz",
    REPO_ROOT / "429.mcf-51B.champsimtrace.xz",
]

CHAMPSIM_BIN = CHAMPSIM_ROOT / "bin" / "champsim"

SIM_INSTRUCTIONS = int(os.environ.get("CHAMPSIM_SIM_INSTR", 10_000_000))
WARMUP_INSTRUCTIONS = int(os.environ.get("CHAMPSIM_WARMUP_INSTR", 1_000_000))
SIM_TIMEOUT = int(os.environ.get("CHAMPSIM_TIMEOUT", 1200))
BUILD_TIMEOUT = int(os.environ.get("CHAMPSIM_BUILD_TIMEOUT", 600))
MAKE_JOBS = int(os.environ.get("CHAMPSIM_JOBS", max(1, os.cpu_count() or 1)))
TRACE_ITERATIONS = max(1, int(os.environ.get("CHAMPSIM_TRACE_ITERATIONS", 1)))
IPC_PATTERN = re.compile(r"cumulative IPC:\s+([0-9.]+)")
STREAM_LOGS = os.environ.get("CHAMPSIM_STREAM_LOGS", "true").lower() in ("1", "true", "yes", "on")
CONSOLE_LOG_LIMIT = int(os.environ.get("CHAMPSIM_CONSOLE_LOG_LIMIT", 4000))
HEARTBEAT_INTERVAL = int(os.environ.get("CHAMPSIM_HEARTBEAT_INTERVAL", 30))
RUN_ID = os.environ.get("OPENEVOLVE_RUN_ID", "").strip()
if RUN_ID:
    EVAL_LOG_ROOT = Path(__file__).with_name("openevolve_output") / "runs" / RUN_ID / "champsim"
else:
    EVAL_LOG_ROOT = Path(__file__).with_name("openevolve_output") / "logs" / "champsim"


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


def _print_live_line(label: str, line: str) -> None:
    if not STREAM_LOGS:
        return
    print(f"[{label}] {line.rstrip()}")


def _execute_with_stream(
    cmd,
    *,
    cwd: Path,
    timeout: int,
    label: str,
    log_path: Path | None = None,
) -> Tuple[str, float]:
    start = time.time()
    last_output_time = start
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    if process.stdout is None:
        raise RuntimeError("Failed to capture process stdout")

    deadline = start + timeout
    chunks: list[str] = []
    log_file = None

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8")

    try:
        while True:
            if process.poll() is not None:
                break

            remaining = deadline - time.time()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout)

            ready, _, _ = select.select([process.stdout], [], [], min(remaining, 1.0))
            if ready:
                chunk = process.stdout.readline()
                if not chunk:
                    continue
                chunks.append(chunk)
                if log_file is not None:
                    log_file.write(chunk)
                    log_file.flush()
                _print_live_line(label, chunk)
                last_output_time = time.time()
            else:
                if HEARTBEAT_INTERVAL > 0 and log_file is not None:
                    now = time.time()
                    if now - last_output_time >= HEARTBEAT_INTERVAL:
                        elapsed = int(now - start)
                        heartbeat = f"[{label}] heartbeat: {elapsed}s elapsed\n"
                        log_file.write(heartbeat)
                        log_file.flush()
                        last_output_time = now
                continue

        process.wait(timeout=max(0.0, deadline - time.time()))
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        raise exc
    finally:
        if log_file is not None:
            log_file.close()

    output = "".join(chunks)
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, cmd, output)

    return output, time.time() - start


def _append_log(log_path: Path | None, payload: str) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(payload)


def _ensure_configuration() -> None:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing ChampSim config file: {CONFIG_PATH}")

    dest_cfg = CHAMPSIM_ROOT / "champsim_config.json"
    shutil.copy(CONFIG_PATH, dest_cfg)

    cmd = ["./config.sh", dest_cfg.name]
    output, _ = _execute_with_stream(cmd, cwd=CHAMPSIM_ROOT, timeout=BUILD_TIMEOUT, label="ChampSim config")
    _print_console_log("ChampSim config", output)


def _ensure_prerequisites() -> None:
    if not CHAMPSIM_ROOT.exists():
        raise FileNotFoundError(f"ChampSim root not found at {CHAMPSIM_ROOT}")
    missing_traces = [trace for trace in TRACES if not trace.exists()]
    if missing_traces:
        missing_list = ", ".join(str(trace) for trace in missing_traces)
        raise FileNotFoundError(
            f"Trace file(s) not found at {missing_list}. Update TRACES in evaluator.py or run setup_champsim.sh"
        )
    if not PREFETCHER_CC.exists():
        raise FileNotFoundError(f"Prefetcher source missing: {PREFETCHER_CC}")


def _copy_candidate(program_path: Path) -> None:
    shutil.copy(program_path, PREFETCHER_CC)


def _invalidate_prefetcher_object() -> None:
    """Force ChampSim to rebuild the OpenEvolve prefetcher module and binary."""

    try:
        shutil.rmtree(PREFETCHER_OBJ_DIR)
    except FileNotFoundError:
        pass
    except OSError:
        # Fall back to unlinking individual artifacts if the directory cannot be removed
        for artifact in ("openevolve_prefetcher.o", "openevolve_prefetcher.d"):
            path = PREFETCHER_OBJ_DIR / artifact
            try:
                path.unlink()
            except FileNotFoundError:
                continue

    try:
        CHAMPSIM_BIN.unlink()
    except FileNotFoundError:
        pass


def _parse_ipc(stdout: str) -> float:
    matches = IPC_PATTERN.findall(stdout)
    if not matches:
        raise ValueError("Could not find cumulative IPC in ChampSim output")
    return float(matches[-1])


def _build_champsim() -> Tuple[str, float]:
    cmd = ["make", f"-j{MAKE_JOBS}"]
    return _execute_with_stream(cmd, cwd=CHAMPSIM_ROOT, timeout=BUILD_TIMEOUT, label="ChampSim build")


def _run_champsim(
    trace: Path, log_path: Path | None, iteration: int | None = None
) -> Tuple[str, float, Path, Path | None]:
    """Run ChampSim with the specified trace file.
    
    Args:
        trace: Path to the trace file to use for simulation
        
    Returns:
        Tuple of (stdout output, execution time in seconds, trace path)
    """
    if not CHAMPSIM_BIN.exists():
        raise FileNotFoundError("ChampSim binary missing. Did the build succeed?")
    
    if not trace.exists():
        raise FileNotFoundError(f"Trace file not found at {trace}")

    cmd = [
        str(CHAMPSIM_BIN),
        "--warmup-instructions",
        str(WARMUP_INSTRUCTIONS),
        "--simulation-instructions",
        str(SIM_INSTRUCTIONS),
        str(trace),
    ]
    # Use trace name in the label for better identification in logs
    if iteration is None:
        label = f"ChampSim run ({trace.name})"
    else:
        label = f"ChampSim run ({trace.name}, iter {iteration})"
    _append_log(log_path, f"[{label}] starting\n")
    stdout, exec_time = _execute_with_stream(
        cmd,
        cwd=CHAMPSIM_ROOT,
        timeout=SIM_TIMEOUT,
        label=label,
        log_path=log_path,
    )
    return stdout, exec_time, trace, log_path


def _failure_result(message: str, **artifacts) -> EvaluationResult:
    failure_artifacts = {"error": message}
    failure_artifacts.update({k: _trim_log(v) for k, v in artifacts.items() if v})
    return EvaluationResult(
        metrics={"combined_score": 0.0, "ipc": 0.0},
        artifacts=failure_artifacts,
    )


def evaluate(program_path: str) -> EvaluationResult:
    """Entry point used by OpenEvolve."""

    start = time.time()
    program_path = Path(program_path)
    run_id = time.strftime("%Y%m%d_%H%M%S") + f"_{uuid.uuid4().hex[:8]}"
    run_log_dir = EVAL_LOG_ROOT / run_id
    trace_log_paths = [run_log_dir / f"{trace.name}.log" for trace in TRACES]

    try:
        if not TRACES:
            return _failure_result("No traces configured. Update TRACES in evaluator.py")
        _ensure_prerequisites()
        _ensure_configuration()
        _copy_candidate(program_path)
        _invalidate_prefetcher_object()
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_result(f"Setup failed: {exc}")

    candidate_source = ""
    built_source = ""
    try:
        candidate_source = program_path.read_text(encoding="utf-8", errors="replace")
        built_source = PREFETCHER_CC.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        build_stdout, build_time = _build_champsim()
        _print_console_log("ChampSim build", build_stdout)
        for log_path in trace_log_paths:
            _append_log(log_path, "[ChampSim build] success\n")
            _append_log(log_path, _trim_log(build_stdout) + "\n")
            if candidate_source:
                candidate_path = log_path.with_name(f"{log_path.stem}.candidate.cc")
                candidate_path.write_text(candidate_source, encoding="utf-8")
                _append_log(
                    log_path,
                    f"[Program source] candidate={candidate_path.name}\n",
                )
            if built_source:
                built_path = log_path.with_name(f"{log_path.stem}.built.cc")
                built_path.write_text(built_source, encoding="utf-8")
                _append_log(
                    log_path,
                    f"[Program source] built={built_path.name}\n",
                )
    except subprocess.CalledProcessError as exc:
        combined_stdout = exc.stdout or ""
        _print_console_log("ChampSim build (failed)", combined_stdout)
        for log_path in trace_log_paths:
            _append_log(log_path, "[ChampSim build] failed\n")
            _append_log(log_path, _trim_log(combined_stdout) + "\n")
            if candidate_source:
                candidate_path = log_path.with_name(f"{log_path.stem}.candidate.cc")
                candidate_path.write_text(candidate_source, encoding="utf-8")
                _append_log(
                    log_path,
                    f"[Program source] candidate={candidate_path.name}\n",
                )
            if built_source:
                built_path = log_path.with_name(f"{log_path.stem}.built.cc")
                built_path.write_text(built_source, encoding="utf-8")
                _append_log(
                    log_path,
                    f"[Program source] built={built_path.name}\n",
                )
        return _failure_result(
            f"ChampSim build failed with exit code {exc.returncode}",
            build_log=combined_stdout,
        )
    except subprocess.TimeoutExpired as exc:
        for log_path in trace_log_paths:
            _append_log(log_path, f"[ChampSim build] timed out after {exc.timeout} seconds\n")
            if candidate_source:
                candidate_path = log_path.with_name(f"{log_path.stem}.candidate.cc")
                candidate_path.write_text(candidate_source, encoding="utf-8")
                _append_log(
                    log_path,
                    f"[Program source] candidate={candidate_path.name}\n",
                )
            if built_source:
                built_path = log_path.with_name(f"{log_path.stem}.built.cc")
                built_path.write_text(built_source, encoding="utf-8")
                _append_log(
                    log_path,
                    f"[Program source] built={built_path.name}\n",
                )
        return _failure_result(f"ChampSim build timed out after {exc.timeout} seconds")

    # Calculate maximum parallel processes to avoid overloading the system
    max_workers = max(1, os.cpu_count() or 1 - 5)
    max_workers = min(max_workers, len(TRACES))
    print(f"Running ChampSim in parallel for {len(TRACES)} traces using {max_workers} workers")
    
    trace_runs = {
        trace.name: {
            "trace_path": trace,
            "ipcs": [],
            "sim_times": [],
            "stdouts": [],
            "log_path": str(run_log_dir / f"{trace.name}.log"),
            "heartbeat_logs": [],
        }
        for trace in TRACES
    }
    
    try:
        for iteration in range(1, TRACE_ITERATIONS + 1):
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_trace = {
                    executor.submit(
                        _run_champsim,
                        trace,
                        run_log_dir / f"{trace.name}.log",
                        iteration,
                    ): trace
                    for trace in TRACES
                }

                done, pending = concurrent.futures.wait(
                    future_to_trace, return_when=concurrent.futures.FIRST_EXCEPTION
                )
                failure = next((future for future in done if future.exception()), None)
                if failure is not None:
                    for future in pending:
                        future.cancel()
                    return _failure_result(
                        f"ChampSim run failed during iteration {iteration}: {failure.exception()}",
                        build_log=_trim_log(build_stdout),
                    )

                concurrent.futures.wait(future_to_trace)
                for future, trace in future_to_trace.items():
                    sim_stdout, sim_time, trace_path, trace_log_path = future.result()
                    ipc = _parse_ipc(sim_stdout)
                    _print_console_log(f"ChampSim run ({trace_path.name})", sim_stdout)
                    heartbeat_log = ""
                    if trace_log_path and trace_log_path.exists():
                        heartbeat_log = trace_log_path.read_text(encoding="utf-8", errors="replace")

                    run = trace_runs[trace_path.name]
                    run["ipcs"].append(ipc)
                    run["sim_times"].append(sim_time)
                    run["stdouts"].append(sim_stdout)
                    run["heartbeat_logs"].append(heartbeat_log)
            
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_result(
            f"ChampSim parallel run failed: {exc}",
            build_log=_trim_log(build_stdout),
        )

    def _join_iteration_logs(chunks: list[str]) -> str:
        if len(chunks) <= 1:
            return chunks[0] if chunks else ""
        return "\n\n".join(
            f"-- iteration {idx + 1} --\n{chunk}" for idx, chunk in enumerate(chunks)
        )

    trace_ipcs = []
    trace_sim_times = []
    all_traces = []
    all_stdouts = []
    all_log_paths = []
    all_heartbeat_logs = []

    for trace in TRACES:
        run = trace_runs[trace.name]
        avg_ipc = sum(run["ipcs"]) / len(run["ipcs"]) if run["ipcs"] else 0.0
        trace_ipcs.append(avg_ipc)
        trace_sim_times.append(max(run["sim_times"]) if run["sim_times"] else 0.0)
        all_traces.append(trace.name)
        all_stdouts.append(_join_iteration_logs(run["stdouts"]))
        all_log_paths.append(run["log_path"])
        all_heartbeat_logs.append(_join_iteration_logs(run["heartbeat_logs"]))

    successful_ipcs = [ipc for ipc in trace_ipcs if ipc > 0]
    avg_ipc = sum(trace_ipcs) / len(trace_ipcs) if trace_ipcs else 0.0
    sim_time = max(trace_sim_times) if trace_sim_times else 0.0
    
    total_time = time.time() - start
    
    # Create detailed artifacts for each trace
    trace_results = {}
    for i, trace_name in enumerate(all_traces):
        trace_results[f"trace_{i+1}_name"] = trace_name
        trace_results[f"trace_{i+1}_ipc"] = trace_ipcs[i]
        trace_results[f"trace_{i+1}_log"] = _trim_log(all_stdouts[i])
        trace_results[f"trace_{i+1}_log_path"] = all_log_paths[i]
        trace_results[f"trace_{i+1}_heartbeat_log"] = _trim_log(all_heartbeat_logs[i])
    
    artifacts = {
        "build_log": _trim_log(build_stdout),
        "num_traces": len(TRACES),
        "successful_traces": len(successful_ipcs),
        "trace_iterations": TRACE_ITERATIONS,
        "trace_results": trace_results
    }
    
    metrics = {
        "ipc": avg_ipc,  # Average IPC across all traces, or 0 if any trace fails
        "combined_score": avg_ipc,  # Same as IPC for now
        "build_time_s": build_time,
        "sim_time_s": sim_time,
        "wall_time_s": total_time,
        "traces_evaluated": len(TRACES),
        "successful_traces": len(successful_ipcs),
    }
    
    # Add individual trace IPCs to metrics
    for i, ipc in enumerate(trace_ipcs):
        metrics[f"trace_{i+1}_ipc"] = ipc

    summary_lines = [
        "[ChampSim summary]",
        f"ipc={avg_ipc}",
        f"build_time_s={build_time}",
        f"sim_time_s={sim_time}",
        f"wall_time_s={total_time}",
        f"traces_evaluated={len(TRACES)}",
        f"successful_traces={len(successful_ipcs)}",
    ]
    for i, trace_name in enumerate(all_traces):
        summary_lines.append(f"trace_{i+1}_name={trace_name}")
        summary_lines.append(f"trace_{i+1}_ipc={trace_ipcs[i]}")
    summary_payload = "\n".join(summary_lines) + "\n"
    for log_path in trace_log_paths:
        _append_log(log_path, summary_payload)

    return EvaluationResult(metrics=metrics, artifacts=artifacts)
