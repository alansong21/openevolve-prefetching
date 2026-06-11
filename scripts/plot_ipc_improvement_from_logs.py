#!/usr/bin/env python3
"""Plot per-trace IPC percent improvement from ChampSim log directories."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from dataclasses import dataclass

try:
    import matplotlib.pyplot as plt
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing dependency: matplotlib. Install it with `pip install matplotlib` "
        "or add it to your environment, then rerun this script."
    ) from exc

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_ROOT = REPO_ROOT / "openevolve-components" / "openevolve_output" / "logs" / "champsim"

WORKLOAD_DIRS = {
    "spec": (LOG_ROOT / "SPEC_init_logs", LOG_ROOT / "SPEC_best_logs"),
    "ai": (LOG_ROOT / "AI_init_logs", LOG_ROOT / "AI_best_logs"),
}
WORKLOAD_OUTPUTS = {
    "spec": REPO_ROOT / "plots" / "spec_ipc_percent_improvement.pdf",
    "ai": REPO_ROOT / "plots" / "AI_ipc_percent_improvement.pdf",
}


@dataclass(frozen=True)
class PlotStyle:
    figsize: tuple[float, float]
    label_font: int
    tick_font: int
    annotation_font: int
    bar_width: float
    bottom_margin: float
    x_rotation: float


PLOT_STYLES = {
    "spec": PlotStyle(
        figsize=(14, 7),
        label_font=11,
        tick_font=9,
        annotation_font=8,
        bar_width=0.8,
        bottom_margin=0.22,
        x_rotation=60,
    ),
    "ai": PlotStyle(
        figsize=(28, 8),
        label_font=17,
        tick_font=17,
        annotation_font=13,
        bar_width=0.55,
        bottom_margin=0.34,
        x_rotation=60,
    ),
}
IPC_PATTERN = re.compile(r"cumulative IPC:\s+([0-9.]+)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a per-trace bar plot of IPC percent improvement: "
            "(best - init) / init * 100, parsed from ChampSim .log files."
        )
    )
    parser.add_argument(
        "--workload",
        choices=sorted(WORKLOAD_DIRS),
        default="spec",
        help="Workload preset for init/best log directories and default output path.",
    )
    parser.add_argument(
        "--init-dir",
        type=Path,
        help="Directory containing initial-program ChampSim logs.",
    )
    parser.add_argument(
        "--best-dir",
        type=Path,
        help="Directory containing best-program ChampSim logs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output PDF file path.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display the figure interactively in addition to saving.",
    )
    args = parser.parse_args()

    init_dir, best_dir = WORKLOAD_DIRS[args.workload]
    args.init_dir = args.init_dir or init_dir
    args.best_dir = args.best_dir or best_dir
    args.output = args.output or WORKLOAD_OUTPUTS[args.workload]
    return args


def parse_ipc(log_path: Path) -> float:
    matches = IPC_PATTERN.findall(log_path.read_text(encoding="utf-8", errors="replace"))
    if not matches:
        raise ValueError(f"Could not find cumulative IPC in {log_path}")
    return float(matches[-1])


def load_ipc_by_trace(log_dir: Path) -> dict[str, float]:
    if not log_dir.is_dir():
        raise FileNotFoundError(f"Log directory not found: {log_dir}")

    ipc_by_trace: dict[str, float] = {}
    for log_path in sorted(log_dir.glob("*.log")):
        trace_name = log_path.name.removesuffix(".log")
        ipc_by_trace[trace_name] = parse_ipc(log_path)
    if not ipc_by_trace:
        raise ValueError(f"No .log files found under {log_dir}")
    return ipc_by_trace


def display_label(trace_name: str) -> str:
    for suffix in (".champsimtrace.xz", ".champsimtrace.gz"):
        if trace_name.endswith(suffix):
            return trace_name[: -len(suffix)]
    return trace_name


def app_label(label: str) -> str:
    if re.search(r"\.\d+$", label):
        return label.rsplit(".", 1)[0]
    return label.split("-", 1)[0]


def main() -> None:
    args = parse_args()
    style = PLOT_STYLES[args.workload]

    init_ipc = load_ipc_by_trace(args.init_dir)
    best_ipc = load_ipc_by_trace(args.best_dir)

    missing_init = sorted(set(best_ipc) - set(init_ipc))
    missing_best = sorted(set(init_ipc) - set(best_ipc))
    if missing_init or missing_best:
        raise ValueError(
            "Trace mismatch between directories. "
            f"Missing from init: {missing_init}. Missing from best: {missing_best}."
        )

    trace_names = sorted(init_ipc)
    percent_improvement = [
        ((best_ipc[name] - init_ipc[name]) / init_ipc[name]) * 100.0 for name in trace_names
    ]
    labels = [display_label(name) for name in trace_names]
    app_labels = [app_label(label) for label in labels]
    colors = ["#2b8cbe" if value >= 0 else "#d95f0e" for value in percent_improvement]

    fig, ax = plt.subplots(figsize=style.figsize)
    bars = ax.bar(
        range(len(trace_names)),
        percent_improvement,
        width=style.bar_width,
        color=colors,
        edgecolor="black",
        linewidth=0.8,
    )
    ax.axhline(0, color="black", linewidth=1)

    ax.set_xlabel("Trace", fontsize=style.label_font)
    ax.set_ylabel("IPC Improvement (%)", fontsize=style.label_font)
    ax.tick_params(axis="y", labelsize=style.tick_font)
    ax.set_xticks(range(len(trace_names)))
    ax.set_xticklabels(labels, rotation=style.x_rotation, ha="right", fontsize=style.tick_font)
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    for idx in range(1, len(app_labels)):
        if app_labels[idx] != app_labels[idx - 1]:
            ax.axvline(idx - 0.5, color="#3182bd", linewidth=1.2, linestyle=":", alpha=0.9)

    for bar, value in zip(bars, percent_improvement):
        y_offset = 0.3 if value >= 0 else -0.3
        va = "bottom" if value >= 0 else "top"
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            value + y_offset,
            f"{value:.1f}%",
            ha="center",
            va=va,
            fontsize=style.annotation_font,
        )

    mean_init = sum(init_ipc[name] for name in trace_names) / len(trace_names)
    mean_best = sum(best_ipc[name] for name in trace_names) / len(trace_names)
    mean_pct = ((mean_best - mean_init) / mean_init) * 100.0
    print(f"Traces plotted: {len(trace_names)}")
    print(f"Mean init IPC: {mean_init:.4f}")
    print(f"Mean best IPC:  {mean_best:.4f}")
    print(f"Mean improvement: {mean_pct:.2f}%")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.subplots_adjust(bottom=style.bottom_margin)
    fig.savefig(args.output, format="pdf", bbox_inches="tight")
    print(f"Saved plot to {args.output}")

    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
