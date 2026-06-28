"""Static critic/reviewer for combined candidates before ChampSim build."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from split_source import (
    PREFETCHER_BEGIN,
    PREFETCHER_END,
    REPLACEMENT_BEGIN,
    REPLACEMENT_END,
    parse_marker_positions,
)
from merge import extract_evolve_block, validate_combined_source
from agents.directive import PF_REQUIRED, RP_REQUIRED


@dataclass
class CriticReport:
    approved: bool
    reasons: list[str] = field(default_factory=list)

    def text(self) -> str:
        if self.approved:
            return "Critic: approved"
        return "Critic: rejected\n- " + "\n- ".join(self.reasons)


FORBIDDEN_PATTERNS = [
    (re.compile(r"\bstd::cout\b"), "file I/O or logging (std::cout) is forbidden"),
    (re.compile(r"\bprintf\s*\("), "file I/O or logging (printf) is forbidden"),
    (re.compile(r"\bfopen\s*\("), "file I/O (fopen) is forbidden"),
    (re.compile(r"\bnew\s+[A-Za-z_]"), "hot-path dynamic allocation (new) is discouraged"),
]


def _check_section(
    section: str,
    *,
    required_methods: tuple[str, ...],
    required_header: str,
    forbidden_header: str,
    label: str,
) -> list[str]:
    issues: list[str] = []
    if required_header not in section:
        issues.append(f"{label}: missing {required_header}")
    if forbidden_header in section:
        issues.append(f"{label}: must not include {forbidden_header}")
    for method in required_methods:
        if method not in section:
            issues.append(f"{label}: missing required method {method}()")
    evolve = extract_evolve_block(section)
    if evolve is None:
        issues.append(f"{label}: missing EVOLVE-BLOCK markers")
    else:
        for pattern, message in FORBIDDEN_PATTERNS:
            if pattern.search(evolve):
                issues.append(f"{label}: {message}")
    return issues


def review_combined_source(
    combined: str,
    *,
    metadata_contract_id: ContractId | None = None,
    joint_edit: bool = False,
) -> CriticReport:
    reasons: list[str] = []

    try:
        markers = parse_marker_positions(combined)
    except ValueError as exc:
        return CriticReport(approved=False, reasons=[str(exc)])

    pf_start = markers["PREFETCHER_BEGIN"]
    pf_end = markers["PREFETCHER_END"]
    rp_start = markers["REPLACEMENT_BEGIN"]
    rp_end = markers["REPLACEMENT_END"]
    pf_section = combined[pf_start:pf_end]
    rp_section = combined[rp_start:rp_end]

    reasons.extend(
        _check_section(
            pf_section,
            required_methods=PF_REQUIRED,
            required_header="openevolve_prefetcher.h",
            forbidden_header="openevolve_replacement.h",
            label="Prefetcher",
        )
    )
    reasons.extend(
        _check_section(
            rp_section,
            required_methods=RP_REQUIRED,
            required_header="openevolve_replacement.h",
            forbidden_header="openevolve_prefetcher.h",
            label="Replacement",
        )
    )

    # Cross-TU isolation: evolve blocks should keep state in anonymous namespaces.
    for label, section in (("Prefetcher", pf_section), ("Replacement", rp_section)):
        evolve = extract_evolve_block(section) or ""
        if "namespace {" not in evolve and "namespace\n{" not in evolve:
            reasons.append(f"{label}: evolve block should use anonymous namespace for state")

    if joint_edit and metadata_contract_id:
        reasons.extend(
            check_metadata_contract(
                pf_section,
                rp_section,
                metadata_contract_id,
                joint_edit=True,
            )
        )

    if not reasons:
        try:
            validate_combined_source(combined)
        except ValueError as exc:
            reasons.append(f"Split validation failed: {exc}")

    return CriticReport(approved=not reasons, reasons=reasons)
