"""Prefetcher and replacement engineer agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]

PF_SYSTEM = """You are the L2C Prefetcher Engineer for ChampSim co-design.
Edit ONLY the prefetcher half you are given. Respond ONLY with SEARCH/REPLACE diff blocks:

<<<<<<< SEARCH
(exact code to find)
=======
(replacement code)
>>>>>>> REPLACE

Rules:
- Keep all required member function signatures unchanged.
- Keep #include "openevolve_prefetcher.h" and do NOT include openevolve_replacement.h.
- State must stay inside the anonymous namespace in the EVOLVE-BLOCK.
- No file I/O, logging, exceptions, or unbounded dynamic allocation in the hot path.
- Use prefetch_line(addr, fill_this_level, metadata) to issue prefetches.
- Encode prefetch confidence/type in the returned metadata for the replacement policy.
- Do not edit split markers or the replacement section.
"""

RP_SYSTEM = """You are the L2C Replacement Engineer for ChampSim co-design.
Edit ONLY the replacement half you are given. Respond ONLY with SEARCH/REPLACE diff blocks:

<<<<<<< SEARCH
(exact code to find)
=======
(replacement code)
>>>>>>> REPLACE

Rules:
- Keep all required member function signatures unchanged.
- Keep #include "openevolve_replacement.h" and do NOT include openevolve_prefetcher.h.
- State must stay inside the anonymous namespace in the EVOLVE-BLOCK.
- No file I/O, logging, exceptions, or unbounded dynamic allocation in the hot path.
- Read prefetch metadata in replacement_cache_fill for prefetch-aware insertion.
- find_victim must return a way in [0, NUM_WAY).
- Do not edit split markers or the prefetcher section.
"""


def _read_header(name: str) -> str:
    path = REPO_ROOT / "openevolve-components" / name
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace")
    return f"<missing {name}>"


def build_engineer_user_prompt(
    *,
    role: str,
    section_source: str,
    directive_text: str,
    insights: str,
    critic_feedback: str = "",
) -> str:
    header_name = "openevolve_prefetcher.h" if role == "prefetcher" else "openevolve_replacement.h"
    header = _read_header(header_name)
    parts = [
        directive_text,
        "",
        "=== Advisor insights ===",
        insights[:6000],
        "",
        f"=== API header ({header_name}) ===",
        header[:8000],
        "",
        "=== Current section source (edit with SEARCH/REPLACE only) ===",
        section_source,
    ]
    if critic_feedback:
        parts.extend(["", "=== Critic feedback (fix these issues) ===", critic_feedback])
    return "\n".join(parts)


async def run_engineer(
    llm_ensemble: Any,
    *,
    role: str,
    section_source: str,
    directive_text: str,
    insights: str,
    critic_feedback: str = "",
) -> str:
    system = PF_SYSTEM if role == "prefetcher" else RP_SYSTEM
    user = build_engineer_user_prompt(
        role=role,
        section_source=section_source,
        directive_text=directive_text,
        insights=insights,
        critic_feedback=critic_feedback,
    )
    return await llm_ensemble.generate_with_context(
        system_message=system,
        messages=[{"role": "user", "content": user}],
    )
