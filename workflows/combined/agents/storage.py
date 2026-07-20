"""Deterministic hardware-state storage estimator and gate."""

from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from split_source import split_dual_backend_source


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "workflows" / "combined" / "champsim_config.json"
DEFAULT_BUDGET_BYTES = 256 * 1024  # scaled for DPC4 1C.limitBW L2C (2 MiB)

TYPE_BYTES = {
    "bool": 1,
    "char": 1,
    "int8_t": 1,
    "uint8_t": 1,
    "int16_t": 2,
    "uint16_t": 2,
    "int32_t": 4,
    "uint32_t": 4,
    "int": 4,
    "unsigned": 4,
    "float": 4,
    "int64_t": 8,
    "uint64_t": 8,
    "addr_t": 8,
    "double": 8,
}


@dataclass
class StorageReport:
    primary_bytes: int
    drcachesim_bytes: int
    budget_bytes: int
    approved: bool
    reasons: list[str] = field(default_factory=list)
    breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def budget_ratio(self) -> float:
        return self.primary_bytes / max(self.budget_bytes, 1)

    def metrics(self) -> dict[str, float]:
        return {
            "storage_bytes": float(self.primary_bytes),
            "storage_budget_ratio": self.budget_ratio,
            "drcachesim_storage_bytes": float(self.drcachesim_bytes),
            "storage_gate_passed": 1.0 if self.approved else 0.0,
        }

    def text(self) -> str:
        status = "approved" if self.approved else "rejected"
        lines = [
            f"Storage Analyst: {status}",
            f"- ChampSim PF+RP: {self.primary_bytes} / {self.budget_bytes} bytes "
            f"({self.budget_ratio:.2%})",
            f"- drcachesim mirror: {self.drcachesim_bytes} bytes",
        ]
        lines.extend(f"- {reason}" for reason in self.reasons)
        return "\n".join(lines)


def _eval_int(expression: str, constants: dict[str, int]) -> int | None:
    expression = expression.strip()
    for name, value in sorted(constants.items(), key=lambda item: -len(item[0])):
        expression = re.sub(rf"\b{re.escape(name)}\b", str(value), expression)
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return None
    allowed = (
        ast.Expression,
        ast.Constant,
        ast.BinOp,
        ast.UnaryOp,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.FloorDiv,
        ast.Div,
        ast.Mod,
        ast.LShift,
        ast.RShift,
        ast.BitOr,
        ast.BitAnd,
        ast.BitXor,
        ast.USub,
        ast.UAdd,
    )
    if any(not isinstance(node, allowed) for node in ast.walk(tree)):
        return None
    try:
        return int(eval(compile(tree, "<storage-expression>", "eval"), {"__builtins__": {}}))
    except (ArithmeticError, TypeError, ValueError):
        return None


def _constants(section: str) -> dict[str, int]:
    values: dict[str, int] = {}
    pattern = re.compile(
        r"\bconstexpr\s+(?:std::size_t|size_t|int|unsigned|uint\d+_t)\s+"
        r"(\w+)\s*=\s*([^;]+);"
    )
    pending = list(pattern.findall(section))
    for _ in range(len(pending) + 1):
        changed = False
        for name, expression in pending:
            value = _eval_int(expression, values)
            if value is not None and values.get(name) != value:
                values[name] = value
                changed = True
        if not changed:
            break
    return values


def _struct_sizes(section: str) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for name, body in re.findall(r"\bstruct\s+(\w+)\s*\{(.*?)\};", section, re.DOTALL):
        offset = 0
        max_alignment = 1
        for type_name, array_expr in re.findall(
            r"^\s*([\w:]+)\s+\w+\s*(?:\[([^\]]+)\])?\s*(?:=[^;]+)?;",
            body,
            re.MULTILINE,
        ):
            size = TYPE_BYTES.get(type_name)
            if size is None:
                continue
            count = _eval_int(array_expr, {}) if array_expr else 1
            count = count if count is not None else 1
            alignment = min(size, 8)
            offset = (offset + alignment - 1) // alignment * alignment
            offset += size * count
            max_alignment = max(max_alignment, alignment)
        sizes[name] = (offset + max_alignment - 1) // max_alignment * max_alignment
    return sizes


def _estimate_section(
    section: str,
    *,
    cache_lines: int,
    num_cores: int,
) -> tuple[int, list[str]]:
    constants = _constants(section)
    sizes = {**TYPE_BYTES, **_struct_sizes(section)}
    total = 0
    reasons: list[str] = []

    for type_name, count_expr in re.findall(
        r"std::array\s*<\s*([\w:]+)\s*,\s*([^>]+)>\s+\w+", section
    ):
        count = _eval_int(count_expr, constants)
        size = sizes.get(type_name)
        if count is None or size is None:
            reasons.append(f"could not size std::array<{type_name}, {count_expr.strip()}>")
        else:
            total += size * count

    # Fixed cache-sized vectors used by the seed policies.
    for type_name, name in re.findall(r"std::vector\s*<\s*([^>]+)>\s+(\w+)", section):
        clean_type = type_name.strip()
        logical_name = name.lower().strip("_")
        if logical_name in {"rrpv", "last_used_cycles", "lru_counters"}:
            total += sizes.get(clean_type, 4) * cache_lines
        elif logical_name == "psel":
            total += 2 * num_cores
        else:
            reasons.append(f"vector '{name}' has no deterministic capacity model")
    # Nested per-set LRU age lists (drcachesim seed).
    if re.search(r"std::vector\s*<\s*std::vector\s*<\s*int\s*>\s*>\s+lru_counters_", section):
        total += TYPE_BYTES["int"] * cache_lines
    if "std::vector<champsim::msl::fwcounter<PSEL_WIDTH>> PSEL" in section:
        total += 2 * num_cores

    if re.search(r"\b(?:unordered_map|map|list|deque)\s*<", section):
        reasons.append("unbounded associative/linked container is forbidden")

    # Count namespace-level scalar state. This deliberately ignores indented
    # locals and struct fields.
    for type_name in re.findall(
        r"^(?:inline\s+)?(?:static\s+)?(bool|u?int(?:8|16|32|64)_t|int|unsigned|float|double)\s+\w+\s*(?:=[^;]+)?;",
        section,
        re.MULTILINE,
    ):
        total += TYPE_BYTES[type_name]
    return total, reasons


def analyze_storage(
    combined_source: str,
    *,
    budget_bytes: int | None = None,
    config_path: Path = DEFAULT_CONFIG,
) -> StorageReport:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    l2 = config.get("L2C", {})
    cache_lines = int(l2.get("sets", 2048)) * int(l2.get("ways", 16))
    num_cores = int(config.get("num_cores", 1))
    budget = budget_bytes or int(
        os.environ.get("STORAGE_BUDGET_BYTES", str(DEFAULT_BUDGET_BYTES))
    )

    cs_pf, cs_rp, dr_pf, dr_rp = split_dual_backend_source(combined_source)
    estimates: dict[str, int] = {}
    reasons: list[str] = []
    for label, section in (
        ("champsim_prefetcher", cs_pf),
        ("champsim_replacement", cs_rp),
        ("drcachesim_prefetcher", dr_pf),
        ("drcachesim_replacement", dr_rp),
    ):
        estimates[label], issues = _estimate_section(
            section, cache_lines=cache_lines, num_cores=num_cores
        )
        reasons.extend(f"{label}: {issue}" for issue in issues)

    primary = estimates["champsim_prefetcher"] + estimates["champsim_replacement"]
    dr_bytes = estimates["drcachesim_prefetcher"] + estimates["drcachesim_replacement"]
    if primary > budget:
        reasons.append(f"primary policy state exceeds budget by {primary - budget} bytes")
    if primary and dr_bytes > primary * 1.25:
        reasons.append("drcachesim mirror uses over 25% more state than ChampSim")
    approved = not reasons
    return StorageReport(
        primary_bytes=primary,
        drcachesim_bytes=dr_bytes,
        budget_bytes=budget,
        approved=approved,
        reasons=reasons,
        breakdown=estimates,
    )
