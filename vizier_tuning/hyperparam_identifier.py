"""Stage A: identify tunable hyperparameters in an evolved program via an LLM.

A single LLM call reads the evolved source and returns strict JSON containing:

* ``templated_source`` — the full source, byte-for-byte, except each tunable
  literal is replaced by a unique ``{{HP_<name>}}`` placeholder.
* ``parameters`` — a spec for each placeholder (type, current value, bounds,
  scale, rationale).

The result is validated and normalized into ``HyperParam`` objects. Default
bounds are filled in (around the current value) for any numeric parameter whose
range the LLM omitted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from vizier_tuning.config import VizierTuningConfig, build_identifier_llm
from vizier_tuning.template import (
    HyperParam,
    parse_param_specs,
    validate_template_and_spec,
)

logger = logging.getLogger(__name__)

_SYSTEM_MESSAGE = """\
You are a performance-engineering assistant. You are given the FULL source of a
program (a ChampSim cache prefetcher or replacement policy, C/C++) that was
produced by an evolutionary search. Your job is to identify the numeric and
categorical HYPERPARAMETERS hard-coded inside it that a black-box optimizer
(Google Vizier) should tune to maximize performance (IPC), and to return a
"templated" version of the source where each of those constants is replaced by
a placeholder.

Respond with STRICT JSON ONLY (no markdown, no prose, no code fences) of the
exact shape:

{
  "templated_source": "<the entire source, unchanged EXCEPT tunable literals replaced by {{HP_<name>}} placeholders>",
  "parameters": [
    {
      "name": "<unique snake_case identifier>",
      "placeholder": "{{HP_<name>}}",
      "type": "int" | "float" | "discrete" | "categorical",
      "current": <the literal's current value>,
      "min": <number, for int/float>,
      "max": <number, for int/float>,
      "values": [<list>, ...]  // for discrete (numbers) or categorical (tokens)
      "scale": "linear" | "log",
      "rationale": "<one short sentence: what this knob controls>"
    }
  ]
}

HARD RULES:
- "templated_source" MUST be the complete program. Do NOT drop, reorder, or
  reformat code. Keep ALL comments and ALL markers verbatim, especially
  `// EVOLVE-BLOCK-START` / `// EVOLVE-BLOCK-END` and any
  `// === OPENEVOLVE_*_BEGIN/END ===` split markers.
- Replace ONLY the chosen literal tokens with `{{HP_<name>}}`. Each placeholder
  name MUST be unique and appear EXACTLY where its literal was.
- Choose constants that are genuinely tunable and impactful: table sizes,
  prefetch degree/distance, confidence thresholds, history depths, RRPV / aging
  values, gating thresholds, etc. Do NOT parameterize values whose change would
  break compilation or correctness (e.g. struct field counts that other code
  indexes by name, bit widths that must match masks unless you also tune the
  mask, magic values required by an API).
- Prefer `discrete` with power-of-two `values` for memory-table sizes, and mark
  size-like parameters with "scale": "log".
- Give every int/float a sensible `min` and `max` bracketing the current value.
- Identify AT MOST {max_parameters} parameters; if there are more, keep only the
  most impactful ones.
- Output JSON only.
"""

_USER_TEMPLATE = """\
Identify the tunable hyperparameters in the following program and return the
templated source + parameter spec as STRICT JSON.

----- BEGIN SOURCE -----
{source}
----- END SOURCE -----
"""


@dataclass
class IdentificationResult:
    templated_source: str
    parameters: List[HyperParam]
    raw_response: str


def _extract_json(text: str) -> Dict[str, Any]:
    """Best-effort extraction of a JSON object from an LLM response."""

    stripped = text.strip()

    # Strip ```json ... ``` / ``` ... ``` fences if present.
    fence = re.match(r"^```[a-zA-Z]*\n(.*)\n```$", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Fall back to the first balanced {...} span.
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = stripped[start : end + 1]
        return json.loads(candidate)

    raise ValueError("Could not parse JSON from identifier LLM response.")


def _apply_default_bounds(
    params: List[HyperParam], cfg: VizierTuningConfig
) -> List[HyperParam]:
    """Fill in missing numeric bounds from the current value and drop unusable params."""

    d = cfg.search_space_defaults
    kept: List[HyperParam] = []
    for p in params:
        if p.type in ("int", "float"):
            if p.min is None or p.max is None:
                if p.current is None:
                    logger.warning(
                        "Dropping parameter '%s': no bounds and no current value.",
                        p.name,
                    )
                    continue
                try:
                    cur = float(p.current)
                except (TypeError, ValueError):
                    logger.warning(
                        "Dropping parameter '%s': non-numeric current value %r.",
                        p.name,
                        p.current,
                    )
                    continue
                if p.type == "int":
                    lo = max(1, int(round(cur * d.int_min_factor)))
                    hi = max(lo + 1, int(round(cur * d.int_max_factor)))
                else:
                    lo = cur * d.float_min_factor
                    hi = cur * d.float_max_factor
                    if lo == hi:
                        hi = lo + 1.0
                p.min = p.min if p.min is not None else lo
                p.max = p.max if p.max is not None else hi
        kept.append(p)
    return kept


def identify_hyperparameters(
    source: str,
    workflow_config_path,
    cfg: VizierTuningConfig,
) -> IdentificationResult:
    """Run the Stage-A identification LLM call and return a validated result."""

    llm = build_identifier_llm(workflow_config_path, cfg)
    # Use replace(), not str.format(): the JSON schema in _SYSTEM_MESSAGE contains
    # literal { } braces that format() would treat as placeholders.
    system_message = _SYSTEM_MESSAGE.replace("{max_parameters}", str(cfg.max_parameters))
    user_message = _USER_TEMPLATE.replace("{source}", source)

    logger.info("Identifying hyperparameters with model '%s'...", llm.model)
    response = asyncio.run(
        llm.generate_with_context(
            system_message=system_message,
            messages=[{"role": "user", "content": user_message}],
            temperature=cfg.identifier_temperature,
            max_tokens=cfg.identifier_max_tokens,
        )
    )

    data = _extract_json(response)
    templated_source = data.get("templated_source")
    if not isinstance(templated_source, str) or not templated_source.strip():
        raise ValueError("Identifier response missing a non-empty 'templated_source'.")

    raw_params = data.get("parameters") or []
    if not isinstance(raw_params, list):
        raise ValueError("Identifier response 'parameters' must be a list.")

    params = parse_param_specs(raw_params)
    params = _apply_default_bounds(params, cfg)

    if len(params) > cfg.max_parameters:
        logger.info(
            "Identifier proposed %d params; capping to %d.",
            len(params),
            cfg.max_parameters,
        )
        params = params[: cfg.max_parameters]

    # Drop spec entries whose placeholder is absent from the template (e.g. the
    # ones we just trimmed), then validate the strict 1:1 correspondence.
    present = set(re.findall(r"\{\{HP_[A-Za-z_][A-Za-z0-9_]*\}\}", templated_source))
    params = [p for p in params if p.placeholder in present]

    # If trimming left orphan placeholders in the template, fail loudly so we do
    # not search over a half-specified template.
    validate_template_and_spec(templated_source, params)

    logger.info(
        "Identified %d tunable hyperparameters: %s",
        len(params),
        ", ".join(p.name for p in params),
    )
    return IdentificationResult(
        templated_source=templated_source,
        parameters=params,
        raw_response=response,
    )
