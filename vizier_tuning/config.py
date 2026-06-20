"""Configuration and workflow wiring for the Vizier tuning stage.

This module knows two things:

1. ``WORKFLOWS`` — how each OpenEvolve workflow maps to its evaluator module,
   its canonical initial/best program path and its file suffix. This is the
   same mapping ``scripts/run_openevolve_workflow.sh`` uses, kept in one place
   so the tuning stage always reuses the *exact* evaluator evolution used.
2. ``VizierTuningConfig`` — the knobs for the tuning run itself (number of
   trials, algorithm, metric, identifier-LLM settings, search-space defaults),
   loaded from ``vizier_tuning/vizier_tuning_config.yaml`` with env overrides.

It also makes the OpenEvolve package importable (it lives in the ``openevolve``
submodule directory) so we can reuse its LLM stack and config loader.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENEVOLVE_PKG_DIR = REPO_ROOT / "openevolve"


def ensure_openevolve_importable() -> None:
    """Make ``import openevolve.config`` work whether or not it is pip-installed.

    The evaluators import ``openevolve.evaluation_result``; if OpenEvolve was
    installed via pip this is a no-op, otherwise we fall back to the inner
    package under the ``openevolve/`` submodule checkout.

    We check for a real submodule (``openevolve.config``) rather than a bare
    ``import openevolve``, because the submodule's top directory is itself named
    ``openevolve`` and resolves as an empty PEP-420 namespace package when the
    repo root is on ``sys.path`` — which would otherwise mask the real package.
    """

    import importlib

    try:
        importlib.import_module("openevolve.config")
        return
    except ModuleNotFoundError:
        pass

    inner_pkg = OPENEVOLVE_PKG_DIR / "openevolve"
    if not inner_pkg.is_dir():
        return

    # Drop any partial/namespace ``openevolve`` already cached so the real
    # package (found via OPENEVOLVE_PKG_DIR) takes precedence on re-import.
    for name in [m for m in list(sys.modules) if m == "openevolve" or m.startswith("openevolve.")]:
        del sys.modules[name]

    if str(OPENEVOLVE_PKG_DIR) not in sys.path:
        sys.path.insert(0, str(OPENEVOLVE_PKG_DIR))
    importlib.invalidate_caches()


@dataclass(frozen=True)
class WorkflowSpec:
    """Static description of an OpenEvolve workflow."""

    name: str
    evaluator_path: Path
    initial_program: Path
    suffix: str
    config_path: Path

    @property
    def output_dir(self) -> Path:
        """Directory where OpenEvolve writes its ``openevolve_output`` tree.

        OpenEvolve defaults this to ``<dir(initial_program)>/openevolve_output``.
        """

        return self.initial_program.parent / "openevolve_output"

    @property
    def default_best_program(self) -> Path:
        """The evolved best program produced by a finished evolution run."""

        return self.output_dir / "best" / f"best_program{self.suffix}"


def _build_workflows() -> Dict[str, WorkflowSpec]:
    comp = REPO_ROOT / "openevolve-components"
    wf = REPO_ROOT / "workflows"
    specs = [
        WorkflowSpec(
            name="champsim",
            evaluator_path=comp / "evaluator.py",
            initial_program=comp / "initial_program.cc",
            suffix=".cc",
            config_path=comp / "concise_config.yaml",
        ),
        WorkflowSpec(
            name="combined",
            evaluator_path=wf / "combined" / "evaluator.py",
            initial_program=wf / "combined" / "initial_program.cc",
            suffix=".cc",
            config_path=wf / "combined" / "config.yaml",
        ),
        WorkflowSpec(
            name="cbp-ng",
            evaluator_path=wf / "cbp_ng" / "evaluator.py",
            initial_program=wf / "cbp_ng" / "initial_program.hpp",
            suffix=".hpp",
            config_path=wf / "cbp_ng" / "config.yaml",
        ),
    ]
    return {spec.name: spec for spec in specs}


WORKFLOWS: Dict[str, WorkflowSpec] = _build_workflows()


@dataclass
class SearchSpaceDefaults:
    """Fallback bounds used when the identifier LLM omits a range.

    Bounds are expressed as multiplicative factors around the parameter's
    current value (so a current value of 8 with ``int_min_factor=0.25`` and
    ``int_max_factor=4.0`` yields ``[2, 32]``).
    """

    int_min_factor: float = 0.25
    int_max_factor: float = 4.0
    float_min_factor: float = 0.25
    float_max_factor: float = 4.0


@dataclass
class VizierTuningConfig:
    """Knobs for a single Vizier tuning run."""

    # Study
    num_trials: int = 50
    batch_size: int = 1  # >1 is unsafe with the in-place ChampSim build dir
    algorithm: str = "DEFAULT"  # Vizier GP-Bandit
    metric_name: str = "combined_score"
    metric_fallback: str = "ipc"
    owner: str = "openevolve"
    seed_current_values: bool = True  # seed trial #1 with the evolved baseline
    failure_score: float = 0.0  # score reported for builds that fail / 0 IPC

    # Stage A — hyperparameter identification
    max_parameters: int = 12
    identifier_model: Optional[str] = None  # None -> workflow primary model
    identifier_temperature: float = 0.0
    identifier_max_tokens: int = 16000
    baseline_sanity_build: bool = True  # render+evaluate current values first

    search_space_defaults: SearchSpaceDefaults = field(default_factory=SearchSpaceDefaults)

    @classmethod
    def from_yaml(cls, path: Optional[Path]) -> "VizierTuningConfig":
        data: Dict[str, Any] = {}
        if path is not None and Path(path).exists():
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}

        defaults_data = data.pop("search_space_defaults", None) or {}
        cfg = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        if defaults_data:
            cfg.search_space_defaults = SearchSpaceDefaults(
                **{
                    k: v
                    for k, v in defaults_data.items()
                    if k in SearchSpaceDefaults.__dataclass_fields__
                }
            )
        cfg._apply_env_overrides()
        return cfg

    def _apply_env_overrides(self) -> None:
        if os.environ.get("VIZIER_TRIALS"):
            self.num_trials = int(os.environ["VIZIER_TRIALS"])
        if os.environ.get("VIZIER_BATCH_SIZE"):
            self.batch_size = int(os.environ["VIZIER_BATCH_SIZE"])
        if os.environ.get("VIZIER_ALGORITHM"):
            self.algorithm = os.environ["VIZIER_ALGORITHM"]
        if os.environ.get("VIZIER_IDENTIFIER_MODEL"):
            self.identifier_model = os.environ["VIZIER_IDENTIFIER_MODEL"]
        if os.environ.get("VIZIER_MAX_PARAMETERS"):
            self.max_parameters = int(os.environ["VIZIER_MAX_PARAMETERS"])


DEFAULT_TUNING_CONFIG_PATH = Path(__file__).with_name("vizier_tuning_config.yaml")


def load_tuning_config(path: Optional[Path] = None) -> VizierTuningConfig:
    """Load tuning config from YAML (defaults file if none given)."""

    if path is None and DEFAULT_TUNING_CONFIG_PATH.exists():
        path = DEFAULT_TUNING_CONFIG_PATH
    return VizierTuningConfig.from_yaml(path)


def build_identifier_llm(workflow_config_path: Path, cfg: VizierTuningConfig):
    """Construct an OpenEvolve ``OpenAILLM`` for the Stage-A identification call.

    Reuses the workflow's ``config.yaml`` ``llm:`` block (primary model, api
    base, etc.) and injects ``OPENAI_API_KEY``/``OPENAI_API_BASE`` from the
    environment the same way OpenEvolve does for evolution.
    """

    ensure_openevolve_importable()
    from openevolve.config import load_config
    from openevolve.llm.openai import OpenAILLM

    config = load_config(str(workflow_config_path))

    # Inject credentials from env without clobbering an explicit config api_base.
    api_key = os.environ.get("OPENAI_API_KEY")
    api_base = os.environ.get("OPENAI_API_BASE")
    overrides: Dict[str, Any] = {}
    if api_key:
        overrides["api_key"] = api_key
    if api_base:
        overrides["api_base"] = api_base
    if overrides:
        config.llm.update_model_params(overrides)

    if not config.llm.models:
        raise ValueError(
            f"No LLM models configured in {workflow_config_path}; cannot run "
            "hyperparameter identification."
        )

    model_cfg = config.llm.models[0]  # workflow primary model
    if cfg.identifier_model:
        import dataclasses

        model_cfg = dataclasses.replace(model_cfg, name=cfg.identifier_model)

    # Tune generation params for a deterministic JSON response.
    model_cfg.temperature = cfg.identifier_temperature
    model_cfg.max_tokens = max(model_cfg.max_tokens or 0, cfg.identifier_max_tokens)
    if not model_cfg.api_key:
        model_cfg.api_key = api_key

    return OpenAILLM(model_cfg=model_cfg)
