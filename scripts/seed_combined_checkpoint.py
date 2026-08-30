#!/usr/bin/env python3
"""Seed an OpenEvolve checkpoint for the combined workflow.

Prefetcher-only .cc files are auto-wrapped with fixed ChampSim LRU replacement
and the combined workflow's baseline drcachesim PF/RP blocks (required split
markers).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENEVOLVE_ROOT = REPO_ROOT / "openevolve"
COMBINED_EVALUATOR = REPO_ROOT / "workflows" / "combined" / "evaluator.py"
COMBINED_INITIAL = REPO_ROOT / "workflows" / "combined" / "initial_program.cc"

PF_BEGIN = "// === OPENEVOLVE_PREFETCHER_BEGIN ==="
PF_END = "// === OPENEVOLVE_PREFETCHER_END ==="
RP_BEGIN = "// === OPENEVOLVE_REPLACEMENT_BEGIN ==="
RP_END = "// === OPENEVOLVE_REPLACEMENT_END ==="
DR_PF_BEGIN = "// === OPENEVOLVE_DR_PREFETCHER_BEGIN ==="
DR_PF_END = "// === OPENEVOLVE_DR_PREFETCHER_END ==="
DR_RP_BEGIN = "// === OPENEVOLVE_DR_REPLACEMENT_BEGIN ==="
DR_RP_END = "// === OPENEVOLVE_DR_REPLACEMENT_END ==="
REQUIRED_MARKERS = (
    PF_BEGIN,
    PF_END,
    RP_BEGIN,
    RP_END,
    DR_PF_BEGIN,
    DR_PF_END,
    DR_RP_BEGIN,
    DR_RP_END,
)
DR_MARKERS = (DR_PF_BEGIN, DR_PF_END, DR_RP_BEGIN, DR_RP_END)

FIXED_LRU_REPLACEMENT = f"""{RP_BEGIN}
// Fixed baseline: ChampSim stock LRU (replacement/lru), adapted to openevolve_replacement.

#include <algorithm>
#include <cassert>
#include <cstdint>
#include <vector>

#include "openevolve_replacement.h"

// EVOLVE-BLOCK-START
namespace {{

struct openevolve_replacement_state {{
  long num_way = 0;
  uint64_t cycle = 0;
  std::vector<uint64_t> last_used_cycles;
}};

openevolve_replacement_state oer_state{{}};

}} // namespace

openevolve_replacement::openevolve_replacement(CACHE* cache)
    : openevolve_replacement(cache, cache->NUM_SET, cache->NUM_WAY)
{{
}}

openevolve_replacement::openevolve_replacement(CACHE* cache, long sets, long ways) : replacement(cache)
{{
  oer_state.num_way = ways;
  oer_state.cycle = 0;
  oer_state.last_used_cycles.assign(static_cast<std::size_t>(sets * ways), 0);
}}

long openevolve_replacement::find_victim(uint32_t triggering_cpu, uint64_t instr_id, long set,
                                        const champsim::cache_block* current_set, champsim::address ip,
                                        champsim::address full_addr, access_type type)
{{
  (void)triggering_cpu; (void)instr_id; (void)current_set; (void)ip; (void)full_addr; (void)type;
  auto begin = std::next(std::begin(oer_state.last_used_cycles), set * oer_state.num_way);
  auto end = std::next(begin, oer_state.num_way);
  auto victim = std::min_element(begin, end);
  assert(begin <= victim && victim < end);
  return std::distance(begin, victim);
}}

void openevolve_replacement::replacement_cache_fill(uint32_t triggering_cpu, long set, long way,
                                                    champsim::address full_addr, champsim::address ip,
                                                    champsim::address victim_addr, access_type type)
{{
  (void)triggering_cpu; (void)full_addr; (void)ip; (void)victim_addr; (void)type;
  oer_state.last_used_cycles.at(static_cast<std::size_t>(set * oer_state.num_way + way)) = oer_state.cycle++;
}}

void openevolve_replacement::update_replacement_state(uint32_t triggering_cpu, long set, long way,
                                                      champsim::address full_addr, champsim::address ip,
                                                      champsim::address victim_addr, access_type type,
                                                      uint8_t hit)
{{
  (void)triggering_cpu; (void)full_addr; (void)ip; (void)victim_addr;
  if (hit && access_type{{type}} != access_type::WRITE) {{
    oer_state.last_used_cycles.at(static_cast<std::size_t>(set * oer_state.num_way + way)) = oer_state.cycle++;
  }}
}}
// EVOLVE-BLOCK-END
{RP_END}
"""


def _ensure_openevolve_on_path() -> None:
    path = str(OPENEVOLVE_ROOT)
    if path not in sys.path:
        sys.path.insert(0, path)


def _import_program_db():
    _ensure_openevolve_on_path()
    from openevolve.config import DatabaseConfig
    from openevolve.database import Program, ProgramDatabase
    return DatabaseConfig, Program, ProgramDatabase


def _import_combined_evaluator():
    workflow_dir = str(COMBINED_EVALUATOR.parent)
    if workflow_dir not in sys.path:
        sys.path.insert(0, workflow_dir)
    spec = importlib.util.spec_from_file_location("combined_evaluator_seed", COMBINED_EVALUATOR)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load evaluator from {COMBINED_EVALUATOR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _has_marker(code: str, marker: str) -> bool:
    pattern = re.escape(marker).replace(r"\ ", r"[ \t]*")
    return bool(re.search(rf"^[ \t]*{pattern}[ \t]*$", code, flags=re.MULTILINE)) or marker in code


def _missing_markers(code: str) -> List[str]:
    return [m for m in REQUIRED_MARKERS if not _has_marker(code, m)]


def _looks_like_prefetcher(code: str) -> bool:
    return "openevolve_prefetcher" in code or "prefetcher_cache_operate" in code


def _dr_baseline_blocks() -> str:
    """Copy drcachesim PF/RP sections from the combined initial program."""
    text = COMBINED_INITIAL.read_text(encoding="utf-8")
    start = text.find(DR_PF_BEGIN)
    if start < 0 or DR_RP_END not in text[start:]:
        raise RuntimeError(
            f"{COMBINED_INITIAL} is missing drcachesim split markers required by the combined evaluator"
        )
    return text[start:].rstrip() + "\n"


def _append_dr_baseline(code: str, source_name: str) -> str:
    present = [_has_marker(code, m) for m in DR_MARKERS]
    if all(present):
        return code.rstrip() + "\n"
    if any(present):
        raise ValueError(f"{source_name}: incomplete drcachesim split markers")
    return code.rstrip() + "\n\n" + _dr_baseline_blocks()


def _wrap_prefetcher_with_lru(prefetcher_code: str, source_name: str) -> str:
    text = prefetcher_code.strip() + "\n"
    if _has_marker(text, RP_BEGIN) or _has_marker(text, RP_END):
        raise ValueError(f"{source_name}: partial replacement markers found")
    if _has_marker(text, PF_BEGIN) and _has_marker(text, PF_END):
        pf_section = text.rstrip() + "\n"
    elif _has_marker(text, PF_BEGIN) or _has_marker(text, PF_END):
        raise ValueError(f"{source_name}: incomplete prefetcher markers")
    else:
        if not _looks_like_prefetcher(text):
            raise ValueError(f"{source_name}: not an openevolve prefetcher source")
        pf_section = f"{PF_BEGIN}\n// Seeded from: {source_name}\n{text.rstrip()}\n{PF_END}\n"
    header = (
        "// AUTO-WRAPPED by scripts/seed_combined_checkpoint.py\n"
        f"// Prefetcher source: {source_name}\n"
        "// Replacement: fixed ChampSim LRU + baseline drcachesim blocks\n\n"
    )
    combined = header + pf_section.rstrip() + "\n\n" + FIXED_LRU_REPLACEMENT.strip() + "\n"
    return _append_dr_baseline(combined, source_name)


def _prepare_combined_code(path: Path, code: str) -> Tuple[str, str]:
    missing = _missing_markers(code)
    if not missing:
        return code, "combined"
    mode = "combined"
    if RP_BEGIN in missing or RP_END in missing:
        code = _wrap_prefetcher_with_lru(code, path.name)
        mode = "wrapped_lru"
    else:
        code = _append_dr_baseline(code, path.name)
        mode = "wrapped_dr"
    missing = _missing_markers(code)
    if missing:
        raise ValueError(f"{path} missing markers: {missing}")
    return code, mode


def _stable_id(path: Path, code: str) -> str:
    digest = hashlib.sha1(f"{path.name}\n{code}".encode()).hexdigest()[:12]
    return f"seed_{path.stem}_{digest}"


def _as_metrics(raw: Any) -> Dict[str, float]:
    if hasattr(raw, "metrics"):
        raw = raw.metrics
    return {str(k): float(v) for k, v in (raw or {}).items() if isinstance(v, (int, float))}


def _evaluation_error(raw: Any) -> str:
    artifacts = getattr(raw, "artifacts", None)
    if not isinstance(artifacts, dict):
        return ""
    error = artifacts.get("error")
    if not error:
        return ""
    extra = artifacts.get("build_log")
    message = str(error).strip()
    if extra:
        tail = "\n".join(str(extra).strip().splitlines()[-30:])
        if tail:
            message = f"{message}\n{tail}"
    return message


def _collect_program_paths(paths: Iterable[str]) -> List[Path]:
    out: List[Path] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            out.extend(sorted(path.glob("*.cc")))
        elif path.is_file():
            out.append(path)
        else:
            raise SystemExit(f"Not found: {path}")
    seen, unique = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    if not unique:
        raise SystemExit("No .cc programs found")
    return unique


def seed_checkpoint(
    program_paths: List[Path],
    output_dir: Path,
    *,
    evaluate: bool,
    default_score: float,
    score_step: float,
    num_islands: int,
    dump_combined: Optional[Path],
) -> None:
    DatabaseConfig, Program, ProgramDatabase = _import_program_db()
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise SystemExit(f"Output directory not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if dump_combined:
        dump_combined.resolve().mkdir(parents=True, exist_ok=True)
    evaluator = _import_combined_evaluator() if evaluate else None
    db = ProgramDatabase(DatabaseConfig(db_path=str(output_dir), num_islands=num_islands, log_prompts=False))

    for index, path in enumerate(program_paths):
        code, mode = _prepare_combined_code(path, path.read_text(encoding="utf-8", errors="replace"))
        if dump_combined:
            (dump_combined / f"{path.stem}_combined.cc").write_text(code, encoding="utf-8")
        if evaluate:
            with tempfile.TemporaryDirectory(prefix="oe_seed_") as tmp:
                candidate = Path(tmp) / f"{path.stem}_combined.cc"
                candidate.write_text(code, encoding="utf-8")
                result = evaluator.evaluate(str(candidate))
                metrics = _as_metrics(result)
                error = _evaluation_error(result)
        else:
            score = default_score - index * score_step
            metrics = {"combined_score": score, "ipc": score}
            error = ""
        db.add(
            Program(
                id=_stable_id(path, code),
                code=code,
                language="cpp",
                metrics=metrics,
                metadata={
                    "seeded": True,
                    "source_path": str(path),
                    "seed_mode": mode,
                    "fixed_replacement": "lru",
                    **({"eval_error": error} if error else {}),
                },
            ),
            iteration=0,
            target_island=index % num_islands,
        )
        print(f"[{index + 1}/{len(program_paths)}] {path.name} ({mode})")
        if evaluate:
            ipc = metrics.get("ipc", metrics.get("combined_score"))
            print(f"    ipc={ipc}  metrics={metrics}")
            if error:
                print(f"    ERROR: {error}")
                if "No traces found" in error or "Setup failed" in error:
                    raise SystemExit(
                        "Stopping: ChampSim evaluation cannot run until traces/setup are fixed.\n"
                        "Point CHAMPSIM_TRACE_DIR at a directory of *.champsimtrace.xz files "
                        f"(default is {REPO_ROOT / 'traces'})."
                    )
    db.save(str(output_dir), iteration=0)
    print(f"Wrote checkpoint to {output_dir}")


def main() -> None:
    p = argparse.ArgumentParser(description="Seed combined checkpoint; prefetcher-only files get fixed LRU.")
    p.add_argument("-p", "--programs", nargs="+", required=True)
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--evaluate", action="store_true")
    p.add_argument("--dump-combined", type=Path, default=None)
    p.add_argument("--default-score", type=float, default=1.0)
    p.add_argument("--score-step", type=float, default=0.01)
    p.add_argument("--num-islands", type=int, default=1)
    args = p.parse_args()
    seed_checkpoint(
        _collect_program_paths(args.programs),
        Path(args.output),
        evaluate=args.evaluate,
        default_score=args.default_score,
        score_step=args.score_step,
        num_islands=args.num_islands,
        dump_combined=args.dump_combined,
    )


if __name__ == "__main__":
    main()
