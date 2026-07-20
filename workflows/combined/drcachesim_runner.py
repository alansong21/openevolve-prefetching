"""Execute drcachesim stage-1 evaluations on converted ChampSim L2 traces."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from drcachesim_stats import CacheStats, compute_stage1_proxy, parse_drcachesim_stats
from split_source import split_dual_backend_source


REPO_ROOT = Path(__file__).resolve().parents[2]
COMBINED_DIR = Path(__file__).resolve().parent
DEFAULT_LAUNCHER = (
    REPO_ROOT / "DynamoRIO" / "build" / "clients" / "bin64" / "drmemtrace_launcher"
)
DEFAULT_TRACE_ROOT = REPO_ROOT / "l2_drcachesim_out"
DEFAULT_CHAMPSIM_CONFIG = COMBINED_DIR / "champsim_config.json"
DEFAULT_BASELINE_CACHE = COMBINED_DIR / "state" / "drcachesim_baseline.json"
DEFAULT_PLUGIN_CACHE = COMBINED_DIR / "state" / "drcachesim_plugins"
DEFAULT_CONFIG_CACHE = COMBINED_DIR / "state" / "drcachesim_configs"


@dataclass(frozen=True)
class TraceInput:
    name: str
    trace: Path
    warmup_refs: int
    sim_refs: int


@dataclass(frozen=True)
class CacheGeometry:
    name: str
    sets: int
    ways: int
    line_size: int
    latency_cycles: int

    @property
    def size_bytes(self) -> int:
        return self.sets * self.ways * self.line_size

    @property
    def size_label(self) -> str:
        size = self.size_bytes
        if size % (1024 * 1024) == 0:
            return f"{size // (1024 * 1024)}M"
        if size % 1024 == 0:
            return f"{size // 1024}K"
        return str(size)


@dataclass(frozen=True)
class HierarchyGeometry:
    """ChampSim-matched L2C→LLC hierarchy for ChampSim L2 demand traces."""

    line_size: int
    l2c: CacheGeometry
    llc: CacheGeometry
    physical_memory: dict[str, Any]
    # ChampSim DRAM timing cannot be enforced in stock drcachesim; these values
    # are recorded for documentation and ChampSim-side alignment checks only.
    memory_latency_supported: bool = False

    @property
    def size_label(self) -> str:  # compatibility for older callers
        return self.l2c.size_label


# Backward-compatible alias used by older tests/scripts.
L2Geometry = CacheGeometry


def _cache_geometry(name: str, block: dict[str, Any], line_size: int) -> CacheGeometry:
    return CacheGeometry(
        name=name,
        sets=int(block["sets"]),
        ways=int(block["ways"]),
        line_size=line_size,
        latency_cycles=int(block.get("latency", 0)),
    )


def load_hierarchy_geometry(config_path: Path | None = None) -> HierarchyGeometry:
    """Read ChampSim L2C/LLC geometry and DRAM timing metadata."""
    path = Path(
        os.environ.get(
            "DRCACHESIM_CHAMPSIM_CONFIG", config_path or DEFAULT_CHAMPSIM_CONFIG
        )
    )
    config = json.loads(path.read_text(encoding="utf-8"))
    line_size = int(config.get("block_size", 64))
    return HierarchyGeometry(
        line_size=line_size,
        l2c=_cache_geometry("L2C", config["L2C"], line_size),
        llc=_cache_geometry("LLC", config["LLC"], line_size),
        physical_memory=dict(config.get("physical_memory", {})),
        memory_latency_supported=False,
    )


def load_l2_geometry(config_path: Path | None = None) -> CacheGeometry:
    """Compatibility wrapper returning ChampSim L2C geometry."""
    return load_hierarchy_geometry(config_path).l2c


def render_l2_llc_config(
    *,
    hierarchy: HierarchyGeometry,
    replacement: str,
    prefetcher: str,
    warmup_refs: int = 0,
    sim_refs: int = 0,
    llc_replacement: str | None = None,
    llc_prefetcher: str = "none",
) -> str:
    """Render ChampSim-matched L2C→LLC hierarchy for L2 demand traces.

    L2 access traces feed the unified L2C. Evolved policies attach to L2C.
    LLC mirrors ChampSim LLC geometry and uses ChampSim's LLC baseline
    policies (LRU / no prefetch) unless overridden.

    Stock drcachesim does not model ChampSim DRAM timing (tCAS/tRCD/channels).
    Those ChampSim values are emitted as comments for documentation only.
    """
    llc_replacement = llc_replacement or "LRU"
    dram = hierarchy.physical_memory
    dram_comment = (
        f"// ChampSim physical_memory (NOT enforced by drcachesim): "
        f"data_rate={dram.get('data_rate')}, channels={dram.get('channels')}, "
        f"tCAS={dram.get('tCAS')}, tRCD={dram.get('tRCD')}, tRP={dram.get('tRP')}, "
        f"tRAS={dram.get('tRAS')}\n"
        f"// ChampSim cache hit latencies (NOT enforced by drcachesim): "
        f"L2C={hierarchy.l2c.latency_cycles}c, LLC={hierarchy.llc.latency_cycles}c\n"
    )
    return (
        f"// Auto-generated L2C→LLC hierarchy matching ChampSim\n"
        f"// L2C {hierarchy.l2c.sets}x{hierarchy.l2c.ways} ({hierarchy.l2c.size_label}); "
        f"LLC {hierarchy.llc.sets}x{hierarchy.llc.ways} ({hierarchy.llc.size_label})\n"
        f"{dram_comment}"
        "num_cores       1\n"
        f"line_size       {hierarchy.line_size}\n"
        "coherent        false\n"
        f"warmup_refs     {warmup_refs}\n"
        f"sim_refs        {sim_refs}\n"
        "\n"
        "L2C {\n"
        "  type            unified\n"
        "  core            0\n"
        f"  size            {hierarchy.l2c.size_label}\n"
        f"  assoc           {hierarchy.l2c.ways}\n"
        f"  prefetcher      {prefetcher}\n"
        f"  replace_policy  {replacement}\n"
        "  parent          LLC\n"
        "}\n"
        "\n"
        "LLC {\n"
        "  type            unified\n"
        f"  size            {hierarchy.llc.size_label}\n"
        f"  assoc           {hierarchy.llc.ways}\n"
        f"  prefetcher      {llc_prefetcher}\n"
        f"  replace_policy  {llc_replacement}\n"
        "  parent          memory\n"
        "}\n"
    )


# Keep old names as aliases during migration.
def render_l2_only_config(
    *,
    geometry: CacheGeometry | HierarchyGeometry,
    replacement: str,
    prefetcher: str,
    warmup_refs: int = 0,
    sim_refs: int = 0,
) -> str:
    hierarchy = (
        geometry
        if isinstance(geometry, HierarchyGeometry)
        else load_hierarchy_geometry()
    )
    return render_l2_llc_config(
        hierarchy=hierarchy,
        replacement=replacement,
        prefetcher=prefetcher,
        warmup_refs=warmup_refs,
        sim_refs=sim_refs,
    )


def write_l2_only_config(
    destination: Path,
    *,
    geometry: CacheGeometry | HierarchyGeometry | None = None,
    replacement: str,
    prefetcher: str,
    warmup_refs: int = 0,
    sim_refs: int = 0,
) -> Path:
    hierarchy = (
        geometry
        if isinstance(geometry, HierarchyGeometry)
        else load_hierarchy_geometry()
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        render_l2_llc_config(
            hierarchy=hierarchy,
            replacement=replacement,
            prefetcher=prefetcher,
            warmup_refs=warmup_refs,
            sim_refs=sim_refs,
        ),
        encoding="utf-8",
    )
    return destination


def _trace_for_counts(counts_path: Path) -> Path:
    suffix = ".counts.json"
    return Path(str(counts_path)[: -len(suffix)])


def discover_traces(root: Path, limit: int = 0) -> list[TraceInput]:
    traces: list[TraceInput] = []
    for counts_path in sorted(root.rglob("*.counts.json")):
        trace = _trace_for_counts(counts_path)
        if not trace.is_file():
            continue
        counts = json.loads(counts_path.read_text(encoding="utf-8"))
        traces.append(
            TraceInput(
                name=str(trace.parent.relative_to(root)),
                trace=trace,
                warmup_refs=int(counts.get("warmup_refs", 0)),
                sim_refs=int(counts.get("sim_refs", 0)),
            )
        )
        if limit > 0 and len(traces) >= limit:
            break
    return traces


def _aggregate(stats: Iterable[CacheStats], name: str = "aggregate") -> CacheStats:
    items = list(stats)
    hits = sum(item.hits for item in items)
    misses = sum(item.misses for item in items)
    prefetch_hits = sum(item.prefetch_hits for item in items)
    prefetch_misses = sum(item.prefetch_misses for item in items)
    accesses = hits + misses
    return CacheStats(
        name=name,
        hits=hits,
        misses=misses,
        compulsory_misses=sum(item.compulsory_misses for item in items),
        prefetch_hits=prefetch_hits,
        prefetch_misses=prefetch_misses,
        child_hits=sum(item.child_hits for item in items),
        miss_rate=misses / accesses if accesses else 0.0,
    )


def _select_scored_cache(stats: dict[str, CacheStats]) -> CacheStats:
    # Prefer LLC as the memory-facing proxy; fall back to L2C for older fixtures.
    for name in ("LLC", "LL", "L2C", "L2", "L1D"):
        if name in stats:
            return stats[name]
    raise ValueError("drcachesim output contained no modeled data cache")


def run_trace(
    trace: TraceInput,
    *,
    launcher: Path,
    replacement: str,
    prefetcher: str,
    prefetcher_plugin: Path | None = None,
    replacement_plugin: Path | None = None,
    timeout: int = 1800,
    geometry: CacheGeometry | HierarchyGeometry | None = None,
) -> tuple[CacheStats, str]:
    hierarchy = (
        geometry
        if isinstance(geometry, HierarchyGeometry)
        else load_hierarchy_geometry()
    )
    config_dir = Path(os.environ.get("DRCACHESIM_CONFIG_CACHE", DEFAULT_CONFIG_CACHE))
    config_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".config",
        prefix=f"l2_llc_{trace.name.replace('/', '_')}_",
        dir=config_dir,
        delete=False,
        encoding="utf-8",
    ) as handle:
        config_path = Path(handle.name)
        handle.write(
            render_l2_llc_config(
                hierarchy=hierarchy,
                replacement=replacement,
                prefetcher=prefetcher,
                warmup_refs=trace.warmup_refs,
                sim_refs=trace.sim_refs,
            )
        )

    command = [
        str(launcher),
        "-infile",
        str(trace.trace),
        "-tool",
        "cache_simulator",
        "-config_file",
        str(config_path),
    ]
    if prefetcher_plugin is not None:
        command.extend(["-prefetcher_plugin", str(prefetcher_plugin)])
    if replacement_plugin is not None:
        command.extend(["-replacement_policy_plugin", str(replacement_plugin)])
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    finally:
        try:
            config_path.unlink()
        except FileNotFoundError:
            pass
    output = completed.stdout + "\n" + completed.stderr
    if completed.returncode:
        raise RuntimeError(
            f"drcachesim failed for {trace.name} ({completed.returncode})\n"
            f"{output[-4000:]}"
        )
    return _select_scored_cache(parse_drcachesim_stats(output)), output


def _baseline_key(traces: list[TraceInput], hierarchy: HierarchyGeometry) -> str:
    return "|".join(
        [
            (
                f"l2c={hierarchy.l2c.sets}x{hierarchy.l2c.ways}x{hierarchy.line_size}"
                f"|llc={hierarchy.llc.sets}x{hierarchy.llc.ways}"
            ),
            *(
                f"{trace.trace}:{trace.trace.stat().st_size}:{trace.sim_refs}"
                for trace in traces
            ),
        ]
    )


def _load_cached_baseline(path: Path, key: str) -> CacheStats | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("key") == key:
            return CacheStats(**payload["stats"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return None


def _save_cached_baseline(path: Path, key: str, stats: CacheStats) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "key": key, "stats": stats.to_dict()}, indent=2)
        + "\n",
        encoding="utf-8",
    )


def evaluate_stage1_policy(
    *,
    replacement: str = "RRIP",
    prefetcher: str = "nextline",
    prefetcher_plugin: Path | None = None,
    replacement_plugin: Path | None = None,
) -> tuple[dict[str, float], dict[str, str]]:
    launcher = Path(os.environ.get("DRCACHESIM_LAUNCHER", DEFAULT_LAUNCHER))
    trace_root = Path(os.environ.get("DRCACHESIM_TRACE_ROOT", DEFAULT_TRACE_ROOT))
    limit = int(os.environ.get("DRCACHESIM_TRACE_LIMIT", "1"))
    timeout = int(os.environ.get("DRCACHESIM_TIMEOUT", "1800"))
    baseline_path = Path(
        os.environ.get("DRCACHESIM_BASELINE_CACHE", DEFAULT_BASELINE_CACHE)
    )
    hierarchy = load_hierarchy_geometry()
    if not launcher.is_file():
        raise FileNotFoundError(f"drcachesim launcher not found: {launcher}")
    traces = discover_traces(trace_root, limit)
    if not traces:
        raise FileNotFoundError(
            f"no drmemtrace payloads found under {trace_root}; "
            "counts JSON alone is insufficient"
        )

    key = _baseline_key(traces, hierarchy)
    baseline = _load_cached_baseline(baseline_path, key)
    if baseline is None:
        baseline = _aggregate(
            run_trace(
                trace,
                launcher=launcher,
                replacement="LRU",
                prefetcher="none",
                timeout=timeout,
                geometry=hierarchy,
            )[0]
            for trace in traces
        )
        _save_cached_baseline(baseline_path, key, baseline)

    outputs: list[str] = []
    candidate_stats: list[CacheStats] = []
    for trace in traces:
        stats, output = run_trace(
            trace,
            launcher=launcher,
            replacement=replacement,
            prefetcher=prefetcher,
            prefetcher_plugin=prefetcher_plugin,
            replacement_plugin=replacement_plugin,
            timeout=timeout,
            geometry=hierarchy,
        )
        candidate_stats.append(stats)
        outputs.append(f"=== {trace.name} ===\n{output}")
    candidate = _aggregate(candidate_stats)
    proxy = compute_stage1_proxy(candidate, baseline)
    dram = hierarchy.physical_memory
    metrics = {
        **proxy,
        "drcachesim_hits": float(candidate.hits),
        "drcachesim_misses": float(candidate.misses),
        "drcachesim_prefetch_hits": float(candidate.prefetch_hits),
        "drcachesim_prefetch_misses": float(candidate.prefetch_misses),
        "drcachesim_l2_sets": float(hierarchy.l2c.sets),
        "drcachesim_l2_ways": float(hierarchy.l2c.ways),
        "drcachesim_l2_size_bytes": float(hierarchy.l2c.size_bytes),
        "drcachesim_llc_sets": float(hierarchy.llc.sets),
        "drcachesim_llc_ways": float(hierarchy.llc.ways),
        "drcachesim_llc_size_bytes": float(hierarchy.llc.size_bytes),
        "drcachesim_l2_latency_cycles": float(hierarchy.l2c.latency_cycles),
        "drcachesim_llc_latency_cycles": float(hierarchy.llc.latency_cycles),
        "champsim_dram_tCAS": float(dram.get("tCAS", 0) or 0),
        "champsim_dram_tRCD": float(dram.get("tRCD", 0) or 0),
        "champsim_dram_tRP": float(dram.get("tRP", 0) or 0),
        "champsim_dram_tRAS": float(dram.get("tRAS", 0) or 0),
        "champsim_dram_data_rate": float(dram.get("data_rate", 0) or 0),
        "drcachesim_memory_latency_modeled": 0.0,
        "stage1_available": 1.0,
    }
    return metrics, {
        "drcachesim_output": "\n".join(outputs),
        "drcachesim_hierarchy": (
            f"L2C {hierarchy.l2c.sets}x{hierarchy.l2c.ways} "
            f"({hierarchy.l2c.size_label}) → "
            f"LLC {hierarchy.llc.sets}x{hierarchy.llc.ways} "
            f"({hierarchy.llc.size_label})"
        ),
        "champsim_memory_latency_note": (
            "drcachesim does not model ChampSim DRAM timing; "
            "tCAS/tRCD/tRP/tRAS and cache hit latencies are recorded from "
            "ChampSim config for documentation only. Measured latency effects "
            "require ChampSim stage-2."
        ),
    }


def build_candidate_plugin(program_path: Path) -> Path:
    """Compile a candidate's two drcachesim sections into one policy plugin."""
    source = program_path.read_text(encoding="utf-8", errors="replace")
    _, _, dr_prefetcher, dr_replacement = split_dual_backend_source(source)
    plugin_source = dr_prefetcher + "\n" + dr_replacement
    digest = hashlib.sha256(plugin_source.encode("utf-8")).hexdigest()[:20]
    cache_dir = Path(os.environ.get("DRCACHESIM_PLUGIN_CACHE", DEFAULT_PLUGIN_CACHE))
    cache_dir.mkdir(parents=True, exist_ok=True)
    source_path = cache_dir / f"{digest}.cpp"
    output_path = cache_dir / f"{digest}.so"
    if output_path.is_file():
        return output_path
    source_path.write_text(plugin_source, encoding="utf-8")

    drcachesim = REPO_ROOT / "DynamoRIO" / "clients" / "drcachesim"
    simulator_lib = (
        REPO_ROOT
        / "DynamoRIO"
        / "build"
        / "clients"
        / "lib64"
        / "release"
        / "libdrmemtrace_simulator.a"
    )
    if not simulator_lib.is_file():
        raise FileNotFoundError(
            f"drcachesim simulator library not found: {simulator_lib}"
        )
    command = [
        os.environ.get("CXX", "g++"),
        "-std=c++17",
        "-fPIC",
        "-shared",
        f"-I{drcachesim}",
        f"-I{drcachesim / 'common'}",
        f"-I{drcachesim / 'simulator'}",
        str(source_path),
        str(simulator_lib),
        "-ldl",
        "-o",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(
            "drcachesim candidate plugin failed to compile\n"
            + completed.stdout
            + completed.stderr
        )
    return output_path
