"""Parameter Tuning Agent powered by Google Vizier (OSS).

Discovers numeric knobs in the combined source (search-space JSON + optional
``// VIZIER_KNOB[...]`` annotations), asks Vizier for a suggestion, rewrites
only those ``constexpr`` assignments, and later completes the trial with the
evaluated IPC (or proxy) metric.

When ``google-vizier`` is not installed, falls back to a persistent local
random/UCB sampler with the same suggest → complete contract so CI and
offline runs still work.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
COMBINED_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SEARCH_SPACE = COMBINED_DIR / "vizier_search_space.json"
DEFAULT_STATE_DIR = COMBINED_DIR / "state"

KnobType = Literal["int", "discrete", "float"]

CONSTEXPR_RE = re.compile(
    r"\bconstexpr\s+(?:std::size_t|size_t|int|unsigned|uint\d+_t|float|double)\s+"
    r"(?P<name>\w+)\s*=\s*(?P<value>[^;]+);"
)
ANNOTATION_RE = re.compile(
    r"//\s*VIZIER_KNOB\["
    r"(?P<type>int|discrete|float)"
    r"(?:,\s*(?P<a>[^,\]]+))?"
    r"(?:,\s*(?P<b>[^,\]]+))?"
    r"(?:,\s*(?P<c>[^\]]+))?"
    r"\]\s*\n"
    r"[ \t]*constexpr\s+(?:std::size_t|size_t|int|unsigned|uint\d+_t|float|double)\s+"
    r"(?P<name>\w+)\s*=",
    re.MULTILINE,
)


@dataclass(frozen=True)
class KnobSpec:
    name: str
    type: KnobType
    min: float | None = None
    max: float | None = None
    values: tuple[float | int, ...] | None = None
    description: str = ""

    def sample_random(self, rng: random.Random) -> float | int:
        if self.type == "discrete":
            assert self.values
            return rng.choice(list(self.values))
        if self.type == "int":
            assert self.min is not None and self.max is not None
            return rng.randint(int(self.min), int(self.max))
        assert self.min is not None and self.max is not None
        return rng.uniform(float(self.min), float(self.max))

    def clamp(self, value: float | int) -> float | int:
        if self.type == "discrete":
            assert self.values
            return min(self.values, key=lambda v: abs(float(v) - float(value)))
        if self.type == "int":
            assert self.min is not None and self.max is not None
            return int(max(self.min, min(self.max, round(float(value)))))
        assert self.min is not None and self.max is not None
        return float(max(self.min, min(self.max, float(value))))


@dataclass(frozen=True)
class DerivedSpec:
    name: str
    source: str
    op: Literal["log2"]


@dataclass
class SearchSpace:
    knobs: list[KnobSpec]
    derived: list[DerivedSpec] = field(default_factory=list)
    metric_name: str = "ipc"
    metric_goal: str = "MAXIMIZE"
    algorithm: str = "DEFAULT"

    @property
    def fingerprint(self) -> str:
        payload = {
            "knobs": [
                {
                    "name": k.name,
                    "type": k.type,
                    "min": k.min,
                    "max": k.max,
                    "values": list(k.values) if k.values else None,
                }
                for k in sorted(self.knobs, key=lambda x: x.name)
            ],
            "derived": [
                {"name": d.name, "from": d.source, "op": d.op}
                for d in sorted(self.derived, key=lambda x: x.name)
            ],
        }
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:12]


@dataclass
class TuningResult:
    child_code: str
    parameters: dict[str, float | int]
    trial_id: str
    study_id: str
    backend: str
    summary: str
    llm_response: str


def param_tuner_enabled() -> bool:
    return os.environ.get("OPENEVOLVE_VIZIER", "true").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _state_dir() -> Path:
    path = Path(os.environ.get("OPENEVOLVE_BLACKBOARD_DIR", DEFAULT_STATE_DIR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_number(token: str) -> float | int:
    token = token.strip()
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    return float(token)


def _knob_from_dict(data: dict[str, Any]) -> KnobSpec:
    values = data.get("values")
    return KnobSpec(
        name=str(data["name"]),
        type=data["type"],  # type: ignore[arg-type]
        min=float(data["min"]) if data.get("min") is not None else None,
        max=float(data["max"]) if data.get("max") is not None else None,
        values=tuple(_parse_number(str(v)) for v in values) if values else None,
        description=str(data.get("description", "")),
    )


def load_search_space(
    source: str | None = None,
    path: Path | None = None,
) -> SearchSpace:
    """Load knobs from JSON, then merge any ``VIZIER_KNOB`` annotations in source."""

    space_path = path or Path(
        os.environ.get("OPENEVOLVE_VIZIER_SEARCH_SPACE", DEFAULT_SEARCH_SPACE)
    )
    knobs: dict[str, KnobSpec] = {}
    derived: list[DerivedSpec] = []
    metric_name = "ipc"
    metric_goal = "MAXIMIZE"
    algorithm = "DEFAULT"

    if space_path.is_file():
        data = json.loads(space_path.read_text(encoding="utf-8"))
        metric_name = str(data.get("metric_name", metric_name))
        metric_goal = str(data.get("metric_goal", metric_goal))
        algorithm = str(data.get("algorithm", algorithm))
        for item in data.get("knobs", []):
            knobs[item["name"]] = _knob_from_dict(item)
        for item in data.get("derived", []):
            derived.append(
                DerivedSpec(
                    name=str(item["name"]),
                    source=str(item["from"]),
                    op=item["op"],  # type: ignore[arg-type]
                )
            )

    if source:
        for match in ANNOTATION_RE.finditer(source):
            name = match.group("name")
            ktype = match.group("type")
            a, b, c = match.group("a"), match.group("b"), match.group("c")
            if ktype == "discrete":
                values_raw = [x for x in (a, b, c) if x]
                # Allow "64|128|256" in first slot.
                if len(values_raw) == 1 and "|" in values_raw[0]:
                    values = tuple(
                        _parse_number(part) for part in values_raw[0].split("|")
                    )
                else:
                    values = tuple(_parse_number(part) for part in values_raw)
                knobs[name] = KnobSpec(name=name, type="discrete", values=values)
            elif ktype == "int":
                knobs[name] = KnobSpec(
                    name=name,
                    type="int",
                    min=_parse_number(a or "0"),
                    max=_parse_number(b or "1"),
                )
            else:
                knobs[name] = KnobSpec(
                    name=name,
                    type="float",
                    min=float(a or 0.0),
                    max=float(b or 1.0),
                )

    if not knobs:
        raise ValueError("Vizier search space is empty (no knobs found)")

    return SearchSpace(
        knobs=list(knobs.values()),
        derived=derived,
        metric_name=metric_name,
        metric_goal=metric_goal,
        algorithm=algorithm,
    )


def extract_constexpr_values(source: str) -> dict[str, str]:
    return {m.group("name"): m.group("value").strip() for m in CONSTEXPR_RE.finditer(source)}


def _format_value(value: float | int) -> str:
    if isinstance(value, float) and not value.is_integer():
        return repr(value)
    return str(int(value))


def apply_parameters(
    source: str,
    parameters: dict[str, float | int],
    *,
    derived: list[DerivedSpec] | None = None,
) -> str:
    """Rewrite matching ``constexpr`` assignments; leave unknowns unchanged."""

    updates = dict(parameters)
    for spec in derived or []:
        if spec.op == "log2" and spec.source in updates:
            src = float(updates[spec.source])
            if src <= 0:
                continue
            updates[spec.name] = int(round(math.log2(src)))

    def _replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name not in updates:
            return match.group(0)
        return match.group(0).rsplit("=", 1)[0] + "= " + _format_value(updates[name]) + ";"

    return CONSTEXPR_RE.sub(_replace, source)


def compute_derived(parameters: dict[str, float | int], derived: list[DerivedSpec]) -> dict[str, float | int]:
    out = dict(parameters)
    for spec in derived:
        if spec.op == "log2" and spec.source in out:
            src = float(out[spec.source])
            if src > 0:
                out[spec.name] = int(round(math.log2(src)))
    return out


# --- Backends -----------------------------------------------------------------


class LocalVizierBackend:
    """Persistent random/UCB sampler used when OSS Vizier is unavailable."""

    def __init__(self, study_id: str, space: SearchSpace) -> None:
        self.study_id = study_id
        self.space = space
        self.path = _state_dir() / f"vizier_local_{study_id}.json"
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"trials": {}, "next_id": 1}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"trials": {}, "next_id": 1}

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    def suggest(self) -> tuple[str, dict[str, float | int]]:
        rng = random.Random(f"{self.study_id}:{self._state['next_id']}:{time.time_ns()}")
        completed = [
            t for t in self._state["trials"].values() if t.get("status") == "COMPLETED"
        ]
        # After a few random seeds, exploit best-seen ± small jitter.
        if len(completed) >= 3 and rng.random() < 0.6:
            best = max(completed, key=lambda t: float(t.get("metric", float("-inf"))))
            params = {
                knob.name: knob.clamp(
                    float(best["parameters"][knob.name])
                    + (
                        0
                        if knob.type == "discrete"
                        else rng.choice([-1, 0, 1]) * (1 if knob.type == "int" else 0.05)
                    )
                )
                for knob in self.space.knobs
                if knob.name in best["parameters"]
            }
        else:
            params = {knob.name: knob.sample_random(rng) for knob in self.space.knobs}

        trial_id = str(self._state["next_id"])
        self._state["next_id"] = int(self._state["next_id"]) + 1
        self._state["trials"][trial_id] = {
            "parameters": params,
            "status": "ACTIVE",
            "created": time.time(),
        }
        self._save()
        return trial_id, params

    def complete(self, trial_id: str, metric: float) -> None:
        trial = self._state["trials"].get(str(trial_id))
        if not trial:
            logger.warning("Local Vizier: unknown trial %s", trial_id)
            return
        trial["status"] = "COMPLETED"
        trial["metric"] = float(metric)
        trial["completed"] = time.time()
        self._save()


class OssVizierBackend:
    """Thin wrapper around google-vizier client API."""

    def __init__(self, study_id: str, space: SearchSpace, owner: str) -> None:
        from vizier.service import clients
        from vizier.service import pyvizier as vz
        from vizier import service as vizier_service

        # Keep the SQLite DB under the blackboard state directory.
        db_path = _state_dir() / "vizier_oss.db"
        os.environ.setdefault("VIZIER_DB_PATH", str(db_path))
        # Older/newer packages expose different knobs; best-effort.
        if hasattr(vizier_service, "VIZIER_DB_PATH"):
            try:
                vizier_service.VIZIER_DB_PATH = str(db_path)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

        self._vz = vz
        study_config = vz.StudyConfig(algorithm=space.algorithm or "DEFAULT")
        root = study_config.search_space.root
        for knob in space.knobs:
            if knob.type == "float":
                root.add_float_param(knob.name, float(knob.min or 0.0), float(knob.max or 1.0))
            elif knob.type == "int":
                root.add_int_param(knob.name, int(knob.min or 0), int(knob.max or 1))
            else:
                assert knob.values
                # discrete_param wants comparable numeric values.
                root.add_discrete_param(
                    knob.name, [float(v) for v in knob.values]
                )

        goal = (
            vz.ObjectiveMetricGoal.MAXIMIZE
            if space.metric_goal.upper() == "MAXIMIZE"
            else vz.ObjectiveMetricGoal.MINIMIZE
        )
        study_config.metric_information.append(
            vz.MetricInformation(space.metric_name, goal=goal)
        )

        self.space = space
        self.study_id = study_id
        self._study = clients.Study.from_study_config(
            study_config, owner=owner, study_id=study_id
        )
        self._active: dict[str, Any] = {}

    def suggest(self) -> tuple[str, dict[str, float | int]]:
        suggestions = list(self._study.suggest(count=1))
        if not suggestions:
            raise RuntimeError("Vizier returned no suggestions")
        suggestion = suggestions[0]
        raw = dict(suggestion.parameters)
        params: dict[str, float | int] = {}
        for knob in self.space.knobs:
            value = raw[knob.name]
            params[knob.name] = knob.clamp(value)
        trial_id = str(suggestion.uid if hasattr(suggestion, "uid") else suggestion.id)
        self._active[trial_id] = suggestion
        return trial_id, params

    def complete(self, trial_id: str, metric: float) -> None:
        suggestion = self._active.pop(str(trial_id), None)
        if suggestion is None:
            # Evaluation often runs in another process; reload from the study DB.
            try:
                trials = list(self._study.trials())
            except Exception:  # noqa: BLE001
                trials = []
            for trial in trials:
                tid = str(getattr(trial, "uid", getattr(trial, "id", "")))
                if tid == str(trial_id):
                    suggestion = trial
                    break
        if suggestion is None:
            logger.warning("OSS Vizier: trial %s not found; skipping complete", trial_id)
            return
        measurement = self._vz.Measurement({self.space.metric_name: float(metric)})
        suggestion.complete(measurement)


def _make_backend(space: SearchSpace) -> tuple[str, Any]:
    run_id = os.environ.get("OPENEVOLVE_RUN_ID", "default")
    study_id = f"combined_{run_id}_{space.fingerprint}"
    owner = os.environ.get("OPENEVOLVE_VIZIER_OWNER", "openevolve")
    force_local = os.environ.get("OPENEVOLVE_VIZIER_BACKEND", "").lower() == "local"
    if not force_local:
        try:
            backend = OssVizierBackend(study_id, space, owner=owner)
            logger.info("Using OSS Vizier backend study_id=%s", study_id)
            return "oss", backend
        except Exception as exc:  # noqa: BLE001
            logger.warning("OSS Vizier unavailable (%s); using local backend", exc)
    backend = LocalVizierBackend(study_id, space)
    logger.info("Using local Vizier backend study_id=%s", study_id)
    return "local", backend


def suggest_parameter_mutation(parent_code: str) -> TuningResult | None:
    """Suggest a parameter-only mutation of ``parent_code``."""

    if not param_tuner_enabled():
        return None

    try:
        space = load_search_space(parent_code)
    except ValueError as exc:
        logger.error("Param tuner: %s", exc)
        return None

    present = extract_constexpr_values(parent_code)
    missing = [k.name for k in space.knobs if k.name not in present]
    if missing:
        logger.warning(
            "Param tuner: knobs missing from source (will skip): %s", ", ".join(missing)
        )
        space = SearchSpace(
            knobs=[k for k in space.knobs if k.name in present],
            derived=[d for d in space.derived if d.source in present or d.source in {k.name for k in space.knobs}],
            metric_name=space.metric_name,
            metric_goal=space.metric_goal,
            algorithm=space.algorithm,
        )
        if not space.knobs:
            return None

    backend_name, backend = _make_backend(space)
    trial_id, parameters = backend.suggest()
    full_params = compute_derived(parameters, space.derived)
    child_code = apply_parameters(parent_code, parameters, derived=space.derived)

    if child_code == parent_code:
        logger.warning("Param tuner: suggestion left source unchanged")
        return None

    # Persist pending trial metadata for complete_trial().
    pending_path = _state_dir() / f"vizier_pending_{os.environ.get('OPENEVOLVE_RUN_ID', 'default')}.json"
    pending_path.write_text(
        json.dumps(
            {
                "trial_id": trial_id,
                "study_id": backend.study_id,
                "backend": backend_name,
                "parameters": full_params,
                "metric_name": space.metric_name,
                "fingerprint": space.fingerprint,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    summary = (
        f"Vizier param tune ({backend_name}, study={backend.study_id}, "
        f"trial={trial_id}): "
        + ", ".join(f"{k}={v}" for k, v in sorted(full_params.items()))
    )
    # Synthetic SEARCH/REPLACE log for OpenEvolve bookkeeping (no LLM).
    llm_response = (
        f"[Vizier parameter tuning]\n{summary}\n"
        "<<<<<<< SEARCH\n(parameter constexpr block)\n=======\n"
        + "\n".join(f"constexpr ... {k} = {v};" for k, v in sorted(full_params.items()))
        + "\n>>>>>>> REPLACE\n"
    )
    return TuningResult(
        child_code=child_code,
        parameters=full_params,
        trial_id=str(trial_id),
        study_id=backend.study_id,
        backend=backend_name,
        summary=summary,
        llm_response=llm_response,
    )


def complete_pending_trial(metrics: dict[str, Any] | None) -> float | None:
    """Complete the latest pending Vizier trial using evaluation metrics."""

    if not metrics:
        return None
    pending_path = _state_dir() / f"vizier_pending_{os.environ.get('OPENEVOLVE_RUN_ID', 'default')}.json"
    if not pending_path.is_file():
        return None
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    metric_name = str(pending.get("metric_name", "ipc"))
    value = metrics.get(metric_name)
    if value is None:
        value = metrics.get("measured_ipc")
    if value is None:
        value = metrics.get("ipc_proxy")
    if value is None:
        value = metrics.get("combined_score")
    if value is None:
        logger.warning("Param tuner: no metric available to complete trial")
        return None

    metric_value = float(value)
    space = load_search_space()
    run_id = os.environ.get("OPENEVOLVE_RUN_ID", "default")
    study_id = str(pending.get("study_id") or f"combined_{run_id}_{space.fingerprint}")
    backend_name = str(pending.get("backend", "local"))
    trial_id = str(pending["trial_id"])

    # Always update the durable local mirror (survives process boundaries).
    mirror = LocalVizierBackend(study_id, space)
    if trial_id not in mirror._state["trials"]:
        mirror._state["trials"][trial_id] = {
            "parameters": pending.get("parameters", {}),
            "status": "ACTIVE",
        }
    mirror.complete(trial_id, metric_value)

    if backend_name == "oss" and os.environ.get("OPENEVOLVE_VIZIER_BACKEND", "").lower() != "local":
        try:
            oss = OssVizierBackend(
                study_id,
                space,
                owner=os.environ.get("OPENEVOLVE_VIZIER_OWNER", "openevolve"),
            )
            oss.complete(trial_id, metric_value)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OSS Vizier complete failed (%s); local mirror updated", exc)

    try:
        pending_path.unlink(missing_ok=True)
    except OSError:
        pass

    logger.info(
        "Completed Vizier trial %s with %s=%s",
        trial_id,
        metric_name,
        metric_value,
    )
    return metric_value


def should_run_param_tune(iteration: int, *, force_arm: bool = False) -> bool:
    """Decide whether this mutation should be a Vizier parameter step."""

    if not param_tuner_enabled():
        return False
    forced = os.environ.get("OPENEVOLVE_MUTATION_MODE", "").lower()
    if forced in {"param", "params", "vizier", "param_tuning"}:
        return True
    if force_arm:
        return True
    every_n = int(os.environ.get("OPENEVOLVE_VIZIER_EVERY_N", "0") or 0)
    if every_n > 0 and iteration > 0 and iteration % every_n == 0:
        return True
    prob = float(os.environ.get("OPENEVOLVE_VIZIER_PROB", "0.2") or 0.0)
    if prob <= 0:
        return False
    rng = random.Random(f"vizier-gate:{os.environ.get('OPENEVOLVE_RUN_ID', 'default')}:{iteration}")
    return rng.random() < prob
