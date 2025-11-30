"""ChampSim evaluator glue for OpenEvolve using CloudLab distributed execution."""

from __future__ import annotations

import os
import re
import time
import sys
import concurrent.futures
from pathlib import Path
from typing import Tuple, Dict, List
from collections import defaultdict

# Add cloudlab-lib to path
CLOUDLAB_LIB_PATH = Path(__file__).resolve().parents[1] / "cloudlab-lib"
if str(CLOUDLAB_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(CLOUDLAB_LIB_PATH))

from cloudlab_lib import CloudLabAgent

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

# CloudLab configuration
CLOUDLAB_CONFIG = Path(__file__).resolve().parents[1] / "cloudlab-lib" / "server-config.json"


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


def _parse_ipc(stdout: str) -> float:
    """Parse IPC from ChampSim output."""
    matches = IPC_PATTERN.findall(stdout)
    if not matches:
        raise ValueError("Could not find cumulative IPC in ChampSim output")
    return float(matches[-1])


def _failure_result(message: str, **artifacts) -> EvaluationResult:
    failure_artifacts = {"error": message}
    failure_artifacts.update({k: _trim_log(v) for k, v in artifacts.items() if v})
    return EvaluationResult(
        metrics={"combined_score": 0.0, "ipc": 0.0},
        artifacts=failure_artifacts,
    )


def _map_traces_to_nodes(traces: List[Path], nodes: List[str]) -> Dict[str, List[Path]]:
    """Map traces to nodes using round-robin scheme.
    
    Args:
        traces: List of trace file paths
        nodes: List of node identifiers
        
    Returns:
        Dictionary mapping node identifiers to lists of trace paths
    """
    if not nodes:
        raise ValueError("No nodes available for trace distribution")
    
    node_to_traces = defaultdict(list)
    for i, trace in enumerate(traces):
        node = nodes[i % len(nodes)]
        node_to_traces[node].append(trace)
    
    return dict(node_to_traces)


def _ensure_prerequisites_remote(agent: CloudLabAgent, node: str, repo_root: Path) -> Tuple[bool, str]:
    """Check prerequisites on remote node.
    
    Returns:
        Tuple of (success: bool, error_message: str)
    """
    prefetcher_dir = PREFETCHER_CC.parent.relative_to(REPO_ROOT)
    cmd = f"""
        cd {repo_root}
        if [ ! -d "ChampSim" ]; then
            echo "ERROR: ChampSim root not found at {repo_root}/ChampSim"
            exit 1
        fi
        if [ ! -d "{prefetcher_dir}" ]; then
            echo "ERROR: Prefetcher directory missing: {repo_root}/{prefetcher_dir}"
            exit 1
        fi
        echo "Prerequisites check passed"
    """
    stdout, stderr, exit_status = agent.run_on_node(node, cmd)
    output = "".join(stdout) + "".join(stderr)
    if exit_status != 0:
        return False, output
    return True, output


def _ensure_configuration_remote(agent: CloudLabAgent, node: str, repo_root: Path, config_path: Path) -> Tuple[bool, str]:
    """Ensure ChampSim configuration on remote node.
    
    Returns:
        Tuple of (success: bool, output: str)
    """
    # Convert config_path to relative path from REPO_ROOT
    config_path_rel = config_path.relative_to(REPO_ROOT)
    cmd = f"""
        cd {repo_root}/ChampSim
        if [ ! -f "_configuration.mk" ]; then
            if [ ! -f "../{config_path_rel}" ]; then
                echo "ERROR: Missing ChampSim config file: {repo_root}/{config_path_rel}"
                exit 1
            fi
            ./config.sh ../{config_path_rel}
        fi
        echo "Configuration check passed"
    """
    stdout, stderr, exit_status = agent.run_on_node(node, cmd)
    output = "".join(stdout) + "".join(stderr)
    if exit_status != 0:
        return False, output
    return True, output


def _copy_candidate_remote(agent: CloudLabAgent, node: str, local_program_path: Path, repo_root: Path) -> None:
    """Copy candidate program to remote node."""
    remote_program_path = repo_root / PREFETCHER_CC.relative_to(REPO_ROOT)
    agent.scp(node, str(local_program_path), str(remote_program_path))


def _invalidate_prefetcher_object_remote(agent: CloudLabAgent, node: str, repo_root: Path) -> None:
    """Force ChampSim to rebuild the OpenEvolve prefetcher module and binary on remote node."""
    prefetcher_obj_dir = repo_root / PREFETCHER_OBJ_DIR.relative_to(REPO_ROOT)
    champsim_bin = repo_root / CHAMPSIM_BIN.relative_to(REPO_ROOT)
    
    cmd = f"""
        cd {repo_root}
        rm -rf {prefetcher_obj_dir.relative_to(repo_root)} || true
        rm -f {champsim_bin.relative_to(repo_root)} || true
        echo "Invalidated prefetcher objects"
    """
    agent.run_on_node(node, cmd)


def _build_champsim_remote(agent: CloudLabAgent, node: str, repo_root: Path) -> Tuple[bool, str, float]:
    """Build ChampSim on remote node.
    
    Returns:
        Tuple of (success: bool, output: str, build_time: float)
    """
    start = time.time()
    cmd = f"""
        cd {repo_root}/ChampSim
        make -j{MAKE_JOBS}
    """
    stdout, stderr, exit_status = agent.run_on_node(node, cmd)
    output = "".join(stdout) + "".join(stderr)
    build_time = time.time() - start
    
    if exit_status != 0:
        return False, output, build_time
    return True, output, build_time


def _run_champsim_remote(agent: CloudLabAgent, node: str, trace: Path, repo_root: Path) -> Tuple[bool, str, float, str]:
    """Run ChampSim with the specified trace file on remote node.
    
    Returns:
        Tuple of (success: bool, stdout: str, exec_time: float, trace_name: str)
    """
    # Convert trace to path relative to REPO_ROOT for remote execution
    # All machines have the same directory structure, so relative paths are the same
    if trace.is_relative_to(REPO_ROOT):
        trace_remote = repo_root / trace.relative_to(REPO_ROOT)
    else:
        # If trace is absolute but not under REPO_ROOT, try to use it as-is
        # (assuming it exists at the same absolute path on remote)
        trace_remote = trace
    
    champsim_bin_rel = CHAMPSIM_BIN.relative_to(REPO_ROOT)
    
    start = time.time()
    cmd = f"""
        cd {repo_root}
        if [ ! -f "{champsim_bin_rel}" ]; then
            echo "ERROR: ChampSim binary missing. Did the build succeed?"
            exit 1
        fi
        if [ ! -f "{trace_remote}" ]; then
            echo "ERROR: Trace file not found at {trace_remote}"
            exit 1
        fi
        timeout {SIM_TIMEOUT} {champsim_bin_rel} \\
            --warmup-instructions {WARMUP_INSTRUCTIONS} \\
            --simulation-instructions {SIM_INSTRUCTIONS} \\
            {trace_remote}
    """
    stdout, stderr, exit_status = agent.run_on_node(node, cmd)
    output = "".join(stdout) + "".join(stderr)
    exec_time = time.time() - start
    
    if exit_status != 0:
        return False, output, exec_time, trace.name
    
    return True, output, exec_time, trace.name


def evaluate(program_path: str) -> EvaluationResult:
    """Entry point used by OpenEvolve with CloudLab distributed execution."""

    start = time.time()
    program_path = Path(program_path)

    print("Starting evaluation...")
    # Initialize CloudLab agent
    if not CLOUDLAB_CONFIG.exists():
        print(f"CloudLab config file not found at {CLOUDLAB_CONFIG}")
        return _failure_result(f"CloudLab config file not found at {CLOUDLAB_CONFIG}")
    
    try:
        agent = CloudLabAgent(str(CLOUDLAB_CONFIG))
    except Exception as exc:
        print(f"Failed to initialize CloudLab agent: {exc}")
        return _failure_result(f"Failed to initialize CloudLab agent: {exc}")
    
    if not agent.nodes_:
        print("No nodes available in CloudLab cluster")
        return _failure_result("No nodes available in CloudLab cluster")
    
    # Get list of available nodes (exclude unconnected nodes)
    available_nodes = [node for node in agent.nodes_ if node not in agent.unconnected_nodes_]
    if not available_nodes:
        print("No connected nodes available in CloudLab cluster")
        return _failure_result("No connected nodes available in CloudLab cluster")
    
    print(f"Using {len(available_nodes)} CloudLab nodes: {available_nodes}")
    print(f"Evaluating {len(TRACES)} traces")
    
    # Map traces to nodes using round-robin
    node_to_traces = _map_traces_to_nodes(TRACES, available_nodes)
    print(f"Trace distribution: {[(node, [t.name for t in traces]) for node, traces in node_to_traces.items()]}")
    
    # Setup phase: Setup all nodes in parallel
    print(f"Setting up {len(node_to_traces)} nodes in parallel...")
    
    def setup_node(node: str):
        """Setup a node: copy candidate, prerequisites, config, invalidate, and build.
        
        Returns:
            Tuple of (node, build_success: bool, build_output: str, build_time: float)
        """
        try:
            print(f"Node {node}: Starting setup...")
            
            # Copy candidate program
            _copy_candidate_remote(agent, node, program_path, REPO_ROOT)
            
            # Ensure prerequisites
            success, output = _ensure_prerequisites_remote(agent, node, REPO_ROOT)
            if not success:
                _print_console_log(f"Node {node} prerequisites check failed", output)
                return node, False, f"Prerequisites check failed: {output}", 0.0
            
            # Ensure configuration
            success, output = _ensure_configuration_remote(agent, node, REPO_ROOT, CONFIG_PATH)
            if not success:
                _print_console_log(f"Node {node} configuration failed", output)
                return node, False, f"Configuration failed: {output}", 0.0
            
            # Invalidate prefetcher object
            _invalidate_prefetcher_object_remote(agent, node, REPO_ROOT)
            
            # Build ChampSim
            success, build_output, build_time = _build_champsim_remote(agent, node, REPO_ROOT)
            if not success:
                _print_console_log(f"Node {node} build failed", build_output)
                return node, False, build_output, build_time
            else:
                _print_console_log(f"Node {node} build succeeded", build_output)
                return node, True, build_output, build_time
                
        except Exception as exc:
            _print_console_log(f"Node {node} setup failed", str(exc))
            return node, False, f"Setup exception: {exc}", 0.0
    
    # Execute setup for all nodes in parallel
    node_build_results = {}
    node_build_times = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(node_to_traces)) as executor:
        future_to_node = {
            executor.submit(setup_node, node): node for node in node_to_traces.keys()
        }
        
        # Collect setup results as they complete
        for future in concurrent.futures.as_completed(future_to_node):
            node = future_to_node[future]
            try:
                node_result, success, output, build_time = future.result()
                node_build_results[node_result] = (success, output)
                node_build_times[node_result] = build_time
            except Exception as exc:
                _print_console_log(f"Node {node} setup future exception", str(exc))
                node_build_results[node] = (False, f"Future exception: {exc}")
                node_build_times[node] = 0.0
    
    # Check if any node build succeeded
    successful_builds = [node for node, (success, _) in node_build_results.items() if success]
    if not successful_builds:
        all_build_logs = "\n".join([f"Node {node}:\n{output}" for node, (_, output) in node_build_results.items()])
        return _failure_result(
            "All node builds failed",
            build_log=all_build_logs,
        )
    
    # Trace execution phase: Run traces in parallel within each node, and nodes run in parallel
    print(f"Running traces on {len(successful_builds)} nodes in parallel...")
    
    def run_traces_on_node(node: str, traces: List[Path]):
        """Run all traces for a node in parallel.
        
        Returns:
            List of trace result dictionaries with ipc, sim_time, stdout, trace_name, success
        """
        trace_results = []
        
        if node not in successful_builds:
            # If build failed, mark all traces as failed
            for trace in traces:
                trace_results.append({
                    "ipc": 0.0,
                    "sim_time": 0.0,
                    "stdout": f"ERROR: Build failed on node {node}",
                    "trace_name": trace.name,
                    "success": False
                })
            return trace_results
        
        print(f"Node {node}: Running {len(traces)} traces in parallel...")
        
        def run_single_trace(trace: Path):
            """Run a single trace and return result."""
            try:
                success, sim_output, sim_time, trace_name = _run_champsim_remote(agent, node, trace, REPO_ROOT)
                
                if success:
                    try:
                        ipc = _parse_ipc(sim_output)
                        _print_console_log(f"Node {node} - Trace {trace_name}", sim_output)
                        return {
                            "ipc": ipc,
                            "sim_time": sim_time,
                            "stdout": sim_output,
                            "trace_name": trace_name,
                            "success": True
                        }
                    except ValueError as exc:
                        _print_console_log(f"Node {node} - Trace {trace_name} (IPC parse failed)", sim_output)
                        return {
                            "ipc": 0.0,
                            "sim_time": sim_time,
                            "stdout": f"ERROR: Could not parse IPC: {exc}\n{sim_output}",
                            "trace_name": trace_name,
                            "success": False
                        }
                else:
                    _print_console_log(f"Node {node} - Trace {trace_name} (failed)", sim_output)
                    return {
                        "ipc": 0.0,
                        "sim_time": sim_time,
                        "stdout": sim_output,
                        "trace_name": trace_name,
                        "success": False
                    }
            except Exception as exc:
                _print_console_log(f"Node {node} - Trace {trace.name} (exception)", str(exc))
                return {
                    "ipc": 0.0,
                    "sim_time": 0.0,
                    "stdout": f"ERROR: {str(exc)}",
                    "trace_name": trace.name,
                    "success": False
                }
        
        # Run all traces for this node in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(traces)) as trace_executor:
            future_to_trace = {
                trace_executor.submit(run_single_trace, trace): trace for trace in traces
            }
            
            # Collect trace results as they complete
            for future in concurrent.futures.as_completed(future_to_trace):
                trace = future_to_trace[future]
                try:
                    result = future.result()
                    trace_results.append(result)
                except Exception as exc:
                    _print_console_log(f"Node {node} - Trace {trace.name} (future exception)", str(exc))
                    trace_results.append({
                        "ipc": 0.0,
                        "sim_time": 0.0,
                        "stdout": f"ERROR: Future exception: {str(exc)}",
                        "trace_name": trace.name,
                        "success": False
                    })
        
        return trace_results
    
    # Execute trace running for all nodes in parallel
    all_ipcs = []
    all_sim_times = []
    all_stdouts = []
    all_traces = []
    all_nodes = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(node_to_traces)) as executor:
        future_to_node = {
            executor.submit(run_traces_on_node, node, traces): node
            for node, traces in node_to_traces.items()
        }
        
        # Collect trace results as they complete
        for future in concurrent.futures.as_completed(future_to_node):
            node = future_to_node[future]
            try:
                trace_results = future.result()
                # Extract trace results
                for trace_result in trace_results:
                    all_ipcs.append(trace_result["ipc"])
                    all_sim_times.append(trace_result["sim_time"])
                    all_stdouts.append(trace_result["stdout"])
                    all_traces.append(trace_result["trace_name"])
                    all_nodes.append(node)
            except Exception as exc:
                _print_console_log(f"Node {node} trace execution future exception", str(exc))
                # Mark all traces for this node as failed
                for trace in node_to_traces[node]:
                    all_ipcs.append(0.0)
                    all_sim_times.append(0.0)
                    all_stdouts.append(f"ERROR: Future exception: {str(exc)}")
                    all_traces.append(trace.name)
                    all_nodes.append(node)
    
    # If all traces failed, return failure
    if not all_ipcs or all(ipc == 0.0 for ipc in all_ipcs):
        all_build_logs = "\n".join([f"Node {node}:\n{output}" for node, (_, output) in node_build_results.items()])
        return _failure_result(
            "All ChampSim runs failed",
            build_log=all_build_logs,
            run_log="\n".join(all_stdouts),
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
    
    # Use the maximum build time as the overall build time
    build_time = max(node_build_times.values()) if node_build_times else 0.0
    
    total_time = time.time() - start
    
    # Create detailed artifacts for each trace
    trace_results = {}
    for i, (trace_name, node) in enumerate(zip(all_traces, all_nodes)):
        trace_results[f"trace_{i+1}_name"] = trace_name
        trace_results[f"trace_{i+1}_node"] = node
        trace_results[f"trace_{i+1}_ipc"] = all_ipcs[i]
        trace_results[f"trace_{i+1}_log"] = _trim_log(all_stdouts[i])
    
    # Combine build logs from all nodes
    all_build_logs = "\n".join([f"Node {node}:\n{_trim_log(output)}" for node, (_, output) in node_build_results.items()])
    
    artifacts = {
        "build_log": all_build_logs,
        "num_traces": len(TRACES),
        "successful_traces": len(successful_ipcs),
        "nodes_used": list(node_to_traces.keys()),
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

