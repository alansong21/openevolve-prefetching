"""Evaluator for joint prefetcher + replacement evolution.

The combined initial program packs two independent C++ translation units in
one file, separated by ``// === OPENEVOLVE_PREFETCHER_BEGIN === / END`` and
``// === OPENEVOLVE_REPLACEMENT_BEGIN === / END`` markers. This evaluator:

  1. Splits the LLM-produced candidate file on those markers.
  2. Writes the prefetcher half to ``openevolve-components/initial_program.cc``
     and the replacement half to ``openevolve-components/initial_replacement.cc``.
  3. Reuses the solo-prefetcher evaluator's build / run / parsing pipeline
     under a different ChampSim config (``workflows/combined/champsim_config.json``)
     and arranges for the replacement-policy build object to be invalidated on
     every iteration so the new source actually takes effect.

The base evaluator is monkey-patched (not modified) so the standalone
prefetcher workflow keeps behaving exactly as before.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path
from typing import Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
COMP_DIR = REPO_ROOT / "openevolve-components"
WORKFLOW_DIR = Path(__file__).resolve().parent

from split_source import (
    PREFETCHER_BEGIN,
    PREFETCHER_END,
    REPLACEMENT_BEGIN,
    REPLACEMENT_END,
    split_combined_source,
)


def _import_base_evaluator():
    """Import ``openevolve-components/evaluator.py`` without mutating sys.modules state."""

    base_path = COMP_DIR / "evaluator.py"
    if not base_path.exists():
        raise FileNotFoundError(f"Base evaluator not found: {base_path}")

    module_name = "openevolve_components_evaluator"
    if module_name in sys.modules:
        return sys.modules[module_name]

    if str(COMP_DIR) not in sys.path:
        sys.path.insert(0, str(COMP_DIR))

    spec = importlib.util.spec_from_file_location(module_name, base_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not build module spec for {base_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_base = _import_base_evaluator()
EvaluationResult = _base.EvaluationResult  # type: ignore[attr-defined]

REPLACEMENT_CC = COMP_DIR / "initial_replacement.cc"
REPLACEMENT_OBJ_DIR = (
    _base.CHAMPSIM_ROOT / ".csconfig" / "modules" / "replacement" / "openevolve_replacement"
)
COMBINED_CONFIG_PATH = (WORKFLOW_DIR / "champsim_config.json").resolve()


# --- Patch base evaluator hooks --------------------------------------------------
# We swap the module-level ``_copy_candidate`` / ``_invalidate_prefetcher_object``
# functions so the base ``evaluate()`` flow performs the combined-mode steps
# (split + dual write, dual invalidation) without otherwise diverging from the
# solo prefetcher logic. Python resolves these names through the base module's
# globals each call, so swapping them at import time is safe.

_base.CONFIG_PATH = COMBINED_CONFIG_PATH

_orig_invalidate = _base._invalidate_prefetcher_object  # type: ignore[attr-defined]


def _patched_copy_candidate(program_path: Path) -> None:
    src_text = Path(program_path).read_text(encoding="utf-8", errors="replace")
    try:
        pf_source, rp_source = split_combined_source(src_text)
    except ValueError as exc:
        raise ValueError(
            f"Combined candidate at {program_path} could not be split: {exc}"
        ) from exc

    _base.PREFETCHER_CC.write_text(pf_source, encoding="utf-8")
    REPLACEMENT_CC.write_text(rp_source, encoding="utf-8")


def _patched_invalidate() -> None:
    _orig_invalidate()
    try:
        shutil.rmtree(REPLACEMENT_OBJ_DIR)
    except FileNotFoundError:
        pass
    except OSError:
        for artifact in ("openevolve_replacement.o", "openevolve_replacement.d"):
            artifact_path = REPLACEMENT_OBJ_DIR / artifact
            try:
                artifact_path.unlink()
            except FileNotFoundError:
                continue


_base._copy_candidate = _patched_copy_candidate  # type: ignore[attr-defined]
_base._invalidate_prefetcher_object = _patched_invalidate  # type: ignore[attr-defined]


def evaluate(program_path: str):
    """Entry point used by OpenEvolve for the combined workflow."""

    return _base.evaluate(program_path)


__all__ = ["evaluate", "split_combined_source"]
