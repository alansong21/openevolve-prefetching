"""Unified implementation agent for both policies and simulator backends."""

from __future__ import annotations

from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]

IMPLEMENTER_SYSTEM = """You are the unified cache-policy Implementation Agent.
You own BOTH the prefetcher and replacement policy for BOTH ChampSim and
drcachesim. Edit the combined source in one response using SEARCH/REPLACE:

<<<<<<< SEARCH
(exact code to find)
=======
(replacement code)
>>>>>>> REPLACE

Rules:
- Preserve all eight OPENEVOLVE split markers verbatim and in order.
- Preserve every required public API and C-linkage plugin ABI symbol.
- Keep ChampSim PF/RP translation units independent; coordinate only through
  the documented metadata channel.
- Keep each algorithmic change aligned across ChampSim and drcachesim where the
  APIs permit. If a backend cannot model a feature, explain that only in code
  comments inside the relevant EVOLVE block.
- Treat PF and RP as one co-design: prefetch confidence/aggressiveness must have
  a matching insertion/victim policy where useful.
- No file I/O, logging, exceptions, trace-name checks, hardcoded workload PCs,
  or unbounded hot-path allocation.
- Keep hardware state bounded and within the supplied storage budget.
- Respond only with SEARCH/REPLACE blocks; no prose or markdown fences.
"""


def _read_api_context() -> str:
    paths = (
        REPO_ROOT / "openevolve-components" / "openevolve_prefetcher.h",
        REPO_ROOT / "openevolve-components" / "openevolve_replacement.h",
        REPO_ROOT
        / "DynamoRIO"
        / "clients"
        / "drcachesim"
        / "simulator"
        / "prefetcher_plugin.h",
        REPO_ROOT
        / "DynamoRIO"
        / "clients"
        / "drcachesim"
        / "simulator"
        / "replacement_policy_plugin.h",
    )
    chunks: list[str] = []
    for path in paths:
        if path.is_file():
            chunks.append(f"=== {path.name} ===\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(chunks)


def build_implementer_prompt(
    *,
    combined_source: str,
    directive_text: str,
    insights: str,
    critic_feedback: str = "",
) -> str:
    parts = [
        directive_text,
        "",
        "=== Advisor insights ===",
        insights[:8000],
        "",
        "=== API context ===",
        _read_api_context()[:16000],
        "",
        "=== Current four-section combined source ===",
        combined_source,
    ]
    if critic_feedback:
        parts.extend(["", "=== Gate feedback (fix every issue) ===", critic_feedback])
    return "\n".join(parts)


async def run_implementer(
    llm_ensemble: Any,
    *,
    combined_source: str,
    directive_text: str,
    insights: str,
    critic_feedback: str = "",
) -> str:
    return await llm_ensemble.generate_with_context(
        system_message=IMPLEMENTER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": build_implementer_prompt(
                    combined_source=combined_source,
                    directive_text=directive_text,
                    insights=insights,
                    critic_feedback=critic_feedback,
                ),
            }
        ],
    )
