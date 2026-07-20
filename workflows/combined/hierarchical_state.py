"""Small file-backed state helpers for hierarchical evaluation cadence."""

from __future__ import annotations

import json
import math
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is single-process only.
    fcntl = None  # type: ignore[assignment]


DEFAULT_STATE = Path(__file__).resolve().parent / "state" / "hierarchical_eval.json"


@contextmanager
def _locked_file(path: Path) -> Iterator[TextIO]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        yield handle
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def state_path() -> Path:
    return Path(os.environ.get("HIER_STATE_PATH", DEFAULT_STATE))


def next_evaluation(*, every_n: int = 10, path: Path | None = None) -> dict[str, int | bool]:
    """Atomically increment the evaluation count and return cadence metadata."""
    if every_n < 1:
        raise ValueError("every_n must be at least 1")
    target = path or state_path()
    with _locked_file(target) as handle:
        try:
            state = json.load(handle)
        except (json.JSONDecodeError, ValueError):
            state = {}
        count = int(state.get("evaluation_count", 0)) + 1
        # Establish a measured baseline immediately, then run every N evaluations.
        stage2_due = count == 1 or count % every_n == 0
        state.update(
            {
                "schema_version": 1,
                "evaluation_count": count,
                "stage2_every_n": every_n,
                "last_stage2_count": count
                if stage2_due
                else int(state.get("last_stage2_count", 0)),
            }
        )
        handle.seek(0)
        handle.truncate()
        json.dump(state, handle, indent=2)
        handle.write("\n")
        handle.flush()
    return {"evaluation_count": count, "stage2_due": stage2_due}


def stage1_gate_metrics(
    cadence: dict[str, int | bool],
    *,
    ipc_proxy: float,
    available: bool,
    threshold: float,
) -> dict[str, float]:
    """Return native-cascade gate metrics while preserving proxy-only ranking."""
    qualified = not available or ipc_proxy >= threshold
    stage2_due = bool(cadence["stage2_due"]) and qualified
    # Keep unmeasured candidates below every valid positive measured IPC while
    # retaining a monotonic ranking among proxy-only candidates.
    proxy_selection_score = -0.5 + 0.49 * math.tanh(ipc_proxy)
    return {
        "evaluation_count": float(cadence["evaluation_count"]),
        "stage1_passed": 1.0 if qualified else 0.0,
        "stage2_due": 1.0 if stage2_due else 0.0,
        "combined_score": 1.0 if stage2_due else proxy_selection_score,
        "promotion_eligible": 0.0,
    }
