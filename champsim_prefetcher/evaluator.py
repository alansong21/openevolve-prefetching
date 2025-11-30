"""ChampSim evaluator glue for OpenEvolve."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import concurrent.futures
from pathlib import Path
from typing import Tuple
import select

from openevolve.evaluation_result import EvaluationResult

REPO_ROOT = Path(__file__).resolve().parents[1]
CHAMPSIM_ROOT = REPO_ROOT / "ChampSim"
PREFETCHER_CC = Path(__file__).with_name("initial_program.cc")
PREFETCHER_OBJ_DIR = CHAMPSIM_ROOT / ".csconfig" / "modules" / "prefetcher" / "openevolve_prefetcher"
CONFIG_PATH = Path(__file__).with_name("champsim_config.json").resolve()
# Default trace path (for backward compatibility)
TRACE_PATH = Path(os.environ.get("CHAMPSIM_TRACE", REPO_ROOT / "400.perlbench-41B.champsimtrace.xz")).expanduser()

# Manual definition of traces to evaluate
# You can add your traces here
TRACES = [
    # Example: Add paths to your trace files
    TRACE_PATH,
    TRACE_PATH,
    TRACE_PATH,
    TRACE_PATH
    # REPO_ROOT / "traces" / "trace1.champsimtrace.xz",
    # REPO_ROOT / "traces" / "trace2.champsimtrace.xz",
]

# Environment variable can override the manual list if specified
if "CHAMPSIM_TRACES" in os.environ:
    env_traces = [Path(t).expanduser() for t in os.environ["CHAMPSIM_TRACES"].split(":") if t]
    if env_traces:
        TRACES = env_traces
CHAMPSIM_BIN = CHAMPSIM_ROOT / "bin" / "champsim"

SIM_INSTRUCTIONS = int(os.environ.get("CHAMPSIM_SIM_INSTR", 50_000_000))
WARMUP_INSTRUCTIONS = int(os.environ.get("CHAMPSIM_WARMUP_INSTR", 10_000_000))
SIM_TIMEOUT = int(os.environ.get("CHAMPSIM_TIMEOUT", 1200))
BUILD_TIMEOUT = int(os.environ.get("CHAMPSIM_BUILD_TIMEOUT", 600))
MAKE_JOBS = int(os.environ.get("CHAMPSIM_JOBS", max(1, os.cpu_count() or 1)))
IPC_PATTERN = re.compile(r"cumulative IPC:\s+([0-9.]+)")
STREAM_LOGS = os.environ.get("CHAMPSIM_STREAM_LOGS", "true").lower() in ("1", "true", "yes", "on")
CONSOLE_LOG_LIMIT = int(os.environ.get("CHAMPSIM_CONSOLE_LOG_LIMIT", 4000))


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


def _execute_with_stream(cmd, *, cwd: Path, timeout: int, label: str) -> Tuple[str, float]:
    start = time.time()
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

    try:
        while True:
            if process.poll() is not None:
                break

            remaining = deadline - time.time()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(cmd, timeout)

            ready, _, _ = select.select([process.stdout], [], [], remaining)
            if ready:
                chunk = process.stdout.readline()
                if not chunk:
                    continue
                chunks.append(chunk)
                _print_live_line(label, chunk)
            else:
                continue

        process.wait(timeout=max(0.0, deadline - time.time()))
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        raise exc

    output = "".join(chunks)
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, cmd, output)

    return output, time.time() - start


def _ensure_configuration() -> None:
    config_mk = CHAMPSIM_ROOT / "_configuration.mk"
    if config_mk.exists():
        return
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing ChampSim config file: {CONFIG_PATH}")

    cmd = ["./config.sh", str(CONFIG_PATH)]
    output, _ = _execute_with_stream(cmd, cwd=CHAMPSIM_ROOT, timeout=BUILD_TIMEOUT, label="ChampSim config")
    _print_console_log("ChampSim config", output)


def _ensure_prerequisites() -> None:
    if not CHAMPSIM_ROOT.exists():
        raise FileNotFoundError(f"ChampSim root not found at {CHAMPSIM_ROOT}")
    if not TRACE_PATH.exists():
        raise FileNotFoundError(
            f"Trace file not found at {TRACE_PATH}. Update CHAMPSIM_TRACE or run setup_champsim.sh"
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


def _run_champsim(trace: Path) -> Tuple[str, float, Path]:
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
    label = f"ChampSim run ({trace.name})"
    stdout, exec_time = _execute_with_stream(cmd, cwd=CHAMPSIM_ROOT, timeout=SIM_TIMEOUT, label=label)
    return stdout, exec_time, trace


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

    try:
        _ensure_prerequisites()
        _ensure_configuration()
        _copy_candidate(program_path)
        _invalidate_prefetcher_object()
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_result(f"Setup failed: {exc}")

    try:
        build_stdout, build_time = _build_champsim()
        _print_console_log("ChampSim build", build_stdout)
    except subprocess.CalledProcessError as exc:
        combined_stdout = exc.stdout or ""
        _print_console_log("ChampSim build (failed)", combined_stdout)
        return _failure_result(
            f"ChampSim build failed with exit code {exc.returncode}",
            build_log=combined_stdout,
        )
    except subprocess.TimeoutExpired as exc:
        return _failure_result(f"ChampSim build timed out after {exc.timeout} seconds")

    # Calculate maximum parallel processes to avoid overloading the system
    max_workers = max(1, os.cpu_count() or 1 - 5)
    max_workers = min(max_workers, len(TRACES))
    print(f"Running ChampSim in parallel for {len(TRACES)} traces using {max_workers} workers")
    
    # Results storage
    all_ipcs = []
    all_sim_times = []
    all_stdouts = []
    all_traces = []
    
    try:
        # Run simulations in parallel using a thread pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks to the executor
            future_to_trace = {
                executor.submit(_run_champsim, trace): trace for trace in TRACES
            }
            
            # Process results as they complete
            for future in concurrent.futures.as_completed(future_to_trace):
                trace = future_to_trace[future]
                try:
                    sim_stdout, sim_time, trace_path = future.result()
                    ipc = _parse_ipc(sim_stdout)
                    _print_console_log(f"ChampSim run ({trace_path.name})", sim_stdout)
                    
                    all_ipcs.append(ipc)
                    all_sim_times.append(sim_time)
                    all_stdouts.append(sim_stdout)
                    all_traces.append(trace_path.name)
                except Exception as exc:
                    _print_console_log(f"ChampSim run ({trace.name}) failed", str(exc))
                    # Don't fail the entire evaluation if one trace fails
                    # Just log the error and continue with other traces
                    all_ipcs.append(0.0)  # Zero IPC for failed traces
                    all_sim_times.append(0.0)
                    all_stdouts.append(f"ERROR: {str(exc)}")
                    all_traces.append(trace.name)
        
        # If all traces failed, return failure
        if not all_ipcs or all(ipc == 0.0 for ipc in all_ipcs):
            return _failure_result(
                f"All ChampSim runs failed",
                build_log=_trim_log(build_stdout),
                run_log="\n".join(all_stdouts),
            )
            
    except Exception as exc:  # pylint: disable=broad-except
        return _failure_result(
            f"ChampSim parallel run failed: {exc}",
            build_log=_trim_log(build_stdout),
            run_log="\n".join(all_stdouts) if all_stdouts else "",
        )

    # Identify successful runs
    successful_ipcs = [ipc for ipc in all_ipcs if ipc > 0]
    
    # If any trace run failed, set overall IPC to 0
    if any(ipc == 0.0 for ipc in all_ipcs) or len(all_ipcs) != len(TRACES):
        avg_ipc = 0.0
    else:
        # Calculate average IPC only if all trace runs were successful
        avg_ipc = sum(all_ipcs) / len(all_ipcs) if all_ipcs else 0.0
    
    # Use the maximum simulation time as the overall simulation time
    sim_time = max(all_sim_times) if all_sim_times else 0.0
    
    total_time = time.time() - start
    
    # Create detailed artifacts for each trace
    trace_results = {}
    for i, trace_name in enumerate(all_traces):
        trace_results[f"trace_{i+1}_name"] = trace_name
        trace_results[f"trace_{i+1}_ipc"] = all_ipcs[i]
        trace_results[f"trace_{i+1}_log"] = _trim_log(all_stdouts[i])
    
    artifacts = {
        "build_log": _trim_log(build_stdout),
        "num_traces": len(TRACES),
        "successful_traces": len(successful_ipcs),
        "trace_results": trace_results
    }
    
    metrics = {
        "ipc": avg_ipc,  # Average IPC across all traces, or 0 if any trace fails
        "combined_score": avg_ipc,  # Same as IPC for now
        "build_time_s": build_time,
        "sim_time_s": sim_time,
        "wall_time_s": total_time,
        "traces_evaluated": len(TRACES),
        "successful_traces": len([ipc for ipc in all_ipcs if ipc > 0]),
    }
    
    # Add individual trace IPCs to metrics
    for i, ipc in enumerate(all_ipcs):
        metrics[f"trace_{i+1}_ipc"] = ipc
    
    return EvaluationResult(metrics=metrics, artifacts=artifacts)
