"""Metadata contracts coordinating PF and RP through the metadata channel."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ContractId = Literal["confidence_rrpv", "seed_ipcp"]


@dataclass(frozen=True)
class MetadataContract:
    id: ContractId
    summary: str
    pf_requirements: tuple[str, ...]
    rp_requirements: tuple[str, ...]
    pf_patterns: tuple[re.Pattern[str], ...]
    rp_patterns: tuple[re.Pattern[str], ...]


CONTRACTS: dict[ContractId, MetadataContract] = {
    "confidence_rrpv": MetadataContract(
        id="confidence_rrpv",
        summary=(
            "Metadata contract: low 8 bits = prefetch type enum; bits 8-15 = confidence "
            "(0=low/near-evict, 255=high/protect). PF sets on prefetch_line(); RP "
            "decodes in replacement_cache_fill."
        ),
        pf_requirements=(
            "Encode prefetch type in the low metadata byte.",
            "Encode confidence in metadata bits 8-15 for each prefetch.",
        ),
        rp_requirements=(
            "Read metadata in replacement_cache_fill.",
            "Map confidence to insertion RRPV (high -> protect, low -> near-evict).",
        ),
        pf_patterns=(
            re.compile(r"prefetch_line\s*\("),
        ),
        rp_patterns=(
            re.compile(r"replacement_cache_fill\s*\("),
        ),
    ),
    "seed_ipcp": MetadataContract(
        id="seed_ipcp",
        summary=(
            "Seed IPCP layout: stride in low bits, type in bits 8+, spec_nl in bit 12. "
            "RP should treat prefetch flag and metadata type when inserting."
        ),
        pf_requirements=("Use encode_metadata or equivalent type/confidence encoding.",),
        rp_requirements=("Handle prefetch fills distinctly from demand fills.",),
        pf_patterns=(re.compile(r"encode_metadata|prefetch_line\s*\("),),
        rp_patterns=(re.compile(r"replacement_cache_fill\s*\("),),
    ),
}


def get_contract_text(contract_id: ContractId | None) -> str:
    if contract_id is None:
        return ""
    contract = CONTRACTS.get(contract_id)
    return contract.summary if contract else ""


def check_metadata_contract(
    pf_section: str,
    rp_section: str,
    contract_id: ContractId | None,
    *,
    joint_edit: bool,
) -> list[str]:
    """Return critic issues when a round-specific metadata contract is not satisfied."""

    if contract_id is None or not joint_edit:
        return []

    contract = CONTRACTS.get(contract_id)
    if contract is None:
        return [f"Unknown metadata contract: {contract_id}"]

    issues: list[str] = []
    for pattern in contract.pf_patterns:
        if not pattern.search(pf_section):
            issues.append(f"Metadata contract ({contract_id}): prefetcher missing {pattern.pattern}")
            break
    for pattern in contract.rp_patterns:
        if not pattern.search(rp_section):
            issues.append(f"Metadata contract ({contract_id}): replacement missing {pattern.pattern}")
            break
    return issues
