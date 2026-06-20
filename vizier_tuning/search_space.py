"""Translate identified hyperparameters into a Vizier ``StudyConfig``."""

from __future__ import annotations

import logging
from typing import List

from vizier_tuning.config import VizierTuningConfig
from vizier_tuning.template import HyperParam

logger = logging.getLogger(__name__)


def build_study_config(params: List[HyperParam], cfg: VizierTuningConfig):
    """Build a ``vz.StudyConfig`` from the parameter specs.

    Imported lazily so the rest of the package is usable without Vizier
    installed (e.g. for the identifier dry-run).
    """

    from vizier.service import pyvizier as vz

    study_config = vz.StudyConfig(algorithm=cfg.algorithm)
    root = study_config.search_space.root

    def _scale(p: HyperParam):
        if p.scale == "log":
            return vz.ScaleType.LOG
        return vz.ScaleType.LINEAR

    for p in params:
        if p.type == "int":
            root.add_int_param(
                p.name, int(round(float(p.min))), int(round(float(p.max))), scale_type=_scale(p)
            )
        elif p.type == "float":
            root.add_float_param(
                p.name, float(p.min), float(p.max), scale_type=_scale(p)
            )
        elif p.type == "discrete":
            numeric_values = sorted({float(v) for v in p.values})
            root.add_discrete_param(p.name, numeric_values, scale_type=_scale(p))
        elif p.type == "categorical":
            root.add_categorical_param(p.name, [str(v) for v in p.values])
        else:  # pragma: no cover - validated upstream
            raise ValueError(f"Unsupported parameter type: {p.type}")

    study_config.metric_information.append(
        vz.MetricInformation(cfg.metric_name, goal=vz.ObjectiveMetricGoal.MAXIMIZE)
    )

    logger.info(
        "Built Vizier study: algorithm=%s, metric=%s, params=%d",
        cfg.algorithm,
        cfg.metric_name,
        len(params),
    )
    return study_config
