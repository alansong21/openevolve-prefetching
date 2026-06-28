"""Merge prefetcher and replacement halves into a valid combined source file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from split_source import (
    PREFETCHER_BEGIN,
    PREFETCHER_END,
    REPLACEMENT_BEGIN,
    REPLACEMENT_END,
    parse_marker_positions,
    split_combined_source,
)

MARKERS = (PREFETCHER_BEGIN, PREFETCHER_END, REPLACEMENT_BEGIN, REPLACEMENT_END)


@dataclass(frozen=True)
class CombinedLayout:
    prefix: str
    prefetcher_section: str
    middle: str
    replacement_section: str
    suffix: str


def extract_layout(combined: str) -> CombinedLayout:
    positions = parse_marker_positions(combined)

    pf_begin = positions["PREFETCHER_BEGIN"]
    pf_end = positions["PREFETCHER_END"]
    rp_begin = positions["REPLACEMENT_BEGIN"]
    rp_end = positions["REPLACEMENT_END"]

    if not (pf_begin < pf_end < rp_begin < rp_end):
        raise ValueError("Split markers are out of order")

    pf_body_start = combined.find("\n", pf_begin)
    if pf_body_start == -1:
        raise ValueError("Prefetcher BEGIN marker must be followed by a newline")
    pf_body_start += 1

    rp_body_start = combined.find("\n", rp_begin)
    if rp_body_start == -1:
        raise ValueError("Replacement BEGIN marker must be followed by a newline")
    rp_body_start += 1

    return CombinedLayout(
        prefix=combined[: pf_begin + len(PREFETCHER_BEGIN) + 1],
        prefetcher_section=combined[pf_body_start:pf_end],
        middle=combined[pf_end : rp_begin + len(REPLACEMENT_BEGIN) + 1],
        replacement_section=combined[rp_body_start:rp_end],
        suffix=combined[rp_end:],
    )


def merge_sections(
  layout: CombinedLayout,
  prefetcher_section: str,
  replacement_section: str,
) -> str:
    pf = prefetcher_section
    rp = replacement_section
    if pf and not pf.endswith("\n"):
        pf += "\n"
    if rp and not rp.endswith("\n"):
        rp += "\n"
    return f"{layout.prefix}{pf}{layout.middle}{rp}{layout.suffix}"


def merge_from_parent(
    parent_combined: str,
    prefetcher_section: str,
    replacement_section: str,
) -> str:
    layout = extract_layout(parent_combined)
    merged = merge_sections(layout, prefetcher_section, replacement_section)
    validate_combined_source(merged)
    return merged


def validate_combined_source(combined: str) -> None:
    """Round-trip validation using the combined evaluator splitter."""

    split_combined_source(combined)


def extract_evolve_block(section: str) -> str | None:
    match = re.search(
        r"// EVOLVE-BLOCK-START\n(.*?)// EVOLVE-BLOCK-END",
        section,
        flags=re.DOTALL,
    )
    return match.group(1) if match else None


def replace_evolve_block(section: str, new_body: str) -> str:
    body = new_body.rstrip("\n")
    return re.sub(
        r"// EVOLVE-BLOCK-START\n.*?// EVOLVE-BLOCK-END",
        f"// EVOLVE-BLOCK-START\n{body}\n// EVOLVE-BLOCK-END",
        section,
        count=1,
        flags=re.DOTALL,
    )


def section_kind(section: str) -> Literal["prefetcher", "replacement"]:
    if "openevolve_prefetcher.h" in section:
        return "prefetcher"
    if "openevolve_replacement.h" in section:
        return "replacement"
    raise ValueError("Could not determine section kind")
