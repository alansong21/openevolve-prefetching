"""Placeholder-template rendering for hyperparameter trials.

Stage A produces a *templated source* in which every tunable literal is
replaced by a unique ``{{HP_<name>}}`` placeholder, plus a list of parameter
specs. Rendering a concrete candidate for a Vizier trial is then a pure,
deterministic string substitution (no LLM, no fuzzy matching) handled here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

PLACEHOLDER_RE = re.compile(r"\{\{HP_[A-Za-z_][A-Za-z0-9_]*\}\}")

VALID_TYPES = {"int", "float", "discrete", "categorical"}
VALID_SCALES = {"linear", "log"}


@dataclass
class HyperParam:
    """One tunable hyperparameter discovered in the evolved program."""

    name: str
    placeholder: str
    type: str
    current: Any
    min: Optional[float] = None
    max: Optional[float] = None
    values: Optional[List[Any]] = None
    scale: str = "linear"
    rationale: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HyperParam":
        name = data.get("name")
        if not name:
            raise ValueError(f"Parameter missing 'name': {data!r}")
        placeholder = data.get("placeholder") or f"{{{{HP_{name}}}}}"
        ptype = (data.get("type") or "").strip().lower()
        if ptype not in VALID_TYPES:
            raise ValueError(
                f"Parameter '{name}' has invalid type {ptype!r}; "
                f"expected one of {sorted(VALID_TYPES)}"
            )
        scale = (data.get("scale") or "linear").strip().lower()
        if scale not in VALID_SCALES:
            scale = "linear"
        return cls(
            name=name,
            placeholder=placeholder,
            type=ptype,
            current=data.get("current"),
            min=data.get("min"),
            max=data.get("max"),
            values=data.get("values"),
            scale=scale,
            rationale=data.get("rationale", ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "placeholder": self.placeholder,
            "type": self.type,
            "current": self.current,
            "min": self.min,
            "max": self.max,
            "values": self.values,
            "scale": self.scale,
            "rationale": self.rationale,
        }


def parse_param_specs(raw: List[Dict[str, Any]]) -> List[HyperParam]:
    return [HyperParam.from_dict(item) for item in raw]


def format_literal(value: Any, ptype: str) -> str:
    """Render a Python value as a C/C++ source literal."""

    if ptype == "int":
        return str(int(round(float(value))))
    if ptype == "float":
        text = repr(float(value))
        # Ensure a decimal point so the literal is unambiguously floating point.
        if "e" not in text and "E" not in text and "." not in text and "inf" not in text:
            text += ".0"
        return text
    if ptype == "discrete":
        # Discrete params are numeric in Vizier; emit ints cleanly.
        fval = float(value)
        if fval.is_integer():
            return str(int(fval))
        return repr(fval)
    if ptype == "categorical":
        # Categorical values are substituted verbatim (e.g. a token like
        # "true", "false", or an enum name the LLM chose).
        return str(value)
    raise ValueError(f"Unknown parameter type: {ptype}")


def find_placeholders(text: str) -> List[str]:
    return PLACEHOLDER_RE.findall(text)


def validate_template_and_spec(templated_source: str, params: List[HyperParam]) -> None:
    """Ensure placeholders and specs are in 1:1 correspondence and bounds sane.

    Raises ``ValueError`` describing the first problem found.
    """

    if not params:
        raise ValueError("No hyperparameters were identified.")

    spec_placeholders = [p.placeholder for p in params]
    dupes = {ph for ph in spec_placeholders if spec_placeholders.count(ph) > 1}
    if dupes:
        raise ValueError(f"Duplicate placeholders in spec: {sorted(dupes)}")

    source_placeholders = set(find_placeholders(templated_source))
    spec_set = set(spec_placeholders)

    missing_in_source = spec_set - source_placeholders
    if missing_in_source:
        raise ValueError(
            "Spec placeholders not present in templated source: "
            f"{sorted(missing_in_source)}"
        )

    missing_in_spec = source_placeholders - spec_set
    if missing_in_spec:
        raise ValueError(
            "Templated source contains placeholders with no spec entry: "
            f"{sorted(missing_in_spec)}"
        )

    for p in params:
        if p.type in ("int", "float"):
            if p.min is None or p.max is None:
                raise ValueError(f"Parameter '{p.name}' ({p.type}) needs min and max.")
            if float(p.min) >= float(p.max):
                raise ValueError(
                    f"Parameter '{p.name}' has min >= max ({p.min} >= {p.max})."
                )
            if p.scale == "log" and float(p.min) <= 0:
                raise ValueError(
                    f"Parameter '{p.name}' uses log scale but min <= 0 ({p.min})."
                )
        elif p.type in ("discrete", "categorical"):
            if not p.values or len(p.values) < 1:
                raise ValueError(
                    f"Parameter '{p.name}' ({p.type}) needs a non-empty 'values' list."
                )


def clamp_current(p: HyperParam) -> Any:
    """Return ``p.current`` clamped into the parameter's feasible region.

    Used to seed Vizier's first trial with the known-good evolved baseline.
    """

    if p.current is None:
        if p.type in ("int", "float"):
            return type_cast(p, (float(p.min) + float(p.max)) / 2.0)
        return p.values[0]

    if p.type == "int":
        return int(round(max(float(p.min), min(float(p.max), float(p.current)))))
    if p.type == "float":
        return max(float(p.min), min(float(p.max), float(p.current)))
    if p.type in ("discrete", "categorical"):
        if p.current in p.values:
            return p.current
        # Fall back to the nearest discrete value (numeric) or first value.
        if p.type == "discrete":
            try:
                return min(p.values, key=lambda v: abs(float(v) - float(p.current)))
            except (TypeError, ValueError):
                return p.values[0]
        return p.values[0]
    return p.current


def type_cast(p: HyperParam, value: Any) -> Any:
    if p.type == "int":
        return int(round(float(value)))
    if p.type == "float":
        return float(value)
    return value


def render_template(
    templated_source: str, params: List[HyperParam], values: Dict[str, Any]
) -> str:
    """Substitute ``values`` (keyed by param name) into the template.

    Raises ``ValueError`` if any placeholder is left unrendered.
    """

    src = templated_source
    for p in params:
        if p.name not in values:
            raise ValueError(f"No value provided for parameter '{p.name}'.")
        literal = format_literal(values[p.name], p.type)
        src = src.replace(p.placeholder, literal)

    leftover = find_placeholders(src)
    if leftover:
        raise ValueError(f"Unrendered placeholders remain after render: {leftover}")
    return src
