#!/usr/bin/env python3
"""Plot per-trace L2C prefetch metric changes from ChampSim log directories."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

try:
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
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
    "spec": REPO_ROOT / "plots" / "spec_l2c_prefetch_changes.pdf",
    "ai": REPO_ROOT / "plots" / "ai_l2c_prefetch_changes.pdf",
}

@dataclass(frozen=True)
class PlotStyle:
    figsize: tuple[float, float]
    y_label_font: int
    y_tick_font: int
    x_tick_font: int
    x_label_font: int
    annotation_font: int
    bar_width: float
    x_rotation: float
    bottom_margin: float


PLOT_STYLES = {
    "spec": PlotStyle(
        figsize=(14, 12),
        y_label_font=10,
        y_tick_font=10,
        x_tick_font=10,
        x_label_font=10,
        annotation_font=10,
        bar_width=0.8,
        x_rotation=60,
        bottom_margin=0.18,
    ),
    "ai": PlotStyle(
        figsize=(30, 21),
        y_label_font=16,
        y_tick_font=16,
        x_tick_font=14,
        x_label_font=16,
        annotation_font=13,
        bar_width=0.45,
        x_rotation=65,
        bottom_margin=0.26,
    ),
}

L2C_TOTAL_PATTERN = re.compile(
    r"cpu0->cpu0_L2C TOTAL\s+ACCESS:\s+(\d+)\s+HIT:\s+(\d+)\s+MISS:\s+(\d+)"
)
L2C_PREFETCH_PATTERN = re.compile(
    r"cpu0->cpu0_L2C PREFETCH REQUESTED:\s+(\d+)\s+ISSUED:\s+(\d+)\s+USEFUL:\s+(\d+)\s+USELESS:\s+(\d+)"
)


@dataclass(frozen=True)
class L2CStats:
    access: int
    hit: int
    miss: int
    pf_issued: int
    pf_useful: int

    @property
    def hit_rate(self) -> float:
        return self.hit / self.access if self.access else 0.0


@dataclass(frozen=True)
class L2CChanges:
    issued_delta: int
    useful_delta: int
    hit_rate_delta_pp: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create per-trace bar plots of L2C metric differences (evolved - initial): "
            "prefetch issued count, useful prefetch count, and hit rate."
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


def parse_l2c_stats(log_path: Path) -> L2CStats:
    text = log_path.read_text(encoding="utf-8", errors="replace")

    total_matches = L2C_TOTAL_PATTERN.findall(text)
    if not total_matches:
        raise ValueError(f"Could not find L2C TOTAL stats in {log_path}")
    access, hit, miss = map(int, total_matches[-1])

    pf_matches = L2C_PREFETCH_PATTERN.findall(text)
    if not pf_matches:
        raise ValueError(f"Could not find L2C prefetch stats in {log_path}")
    _, issued, useful, _ = map(int, pf_matches[-1])

    return L2CStats(
        access=access,
        hit=hit,
        miss=miss,
        pf_issued=issued,
        pf_useful=useful,
    )


def load_stats_by_trace(log_dir: Path) -> dict[str, L2CStats]:
    if not log_dir.is_dir():
        raise FileNotFoundError(f"Log directory not found: {log_dir}")

    stats_by_trace: dict[str, L2CStats] = {}
    for log_path in sorted(log_dir.glob("*.log")):
        trace_name = log_path.name.removesuffix(".log")
        stats_by_trace[trace_name] = parse_l2c_stats(log_path)
    if not stats_by_trace:
        raise ValueError(f"No .log files found under {log_dir}")
    return stats_by_trace


def compute_changes(init: L2CStats, best: L2CStats) -> L2CChanges:
    return L2CChanges(
        issued_delta=best.pf_issued - init.pf_issued,
        useful_delta=best.pf_useful - init.pf_useful,
        hit_rate_delta_pp=(best.hit_rate - init.hit_rate) * 100.0,
    )


def display_label(trace_name: str) -> str:
    for suffix in (".champsimtrace.xz", ".champsimtrace.gz"):
        if trace_name.endswith(suffix):
            return trace_name[: -len(suffix)]
    return trace_name


def app_label(label: str) -> str:
    if re.search(r"\.\d+$", label):
        return label.rsplit(".", 1)[0]
    return label.split("-", 1)[0]


def format_millions(value: int) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def pick_scientific_exponent(values: list[float | int]) -> int:
    max_abs = max((abs(v) for v in values), default=0)
    return 7 if max_abs >= 5_000_000 else 6


def apply_scientific_yaxis(ax: plt.Axes, values: list[float | int], exponent: int) -> None:
    scale = 10**exponent
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value / scale:.1f}"))


def add_app_dividers(ax: plt.Axes, app_labels: list[str]) -> None:
    for idx in range(1, len(app_labels)):
        if app_labels[idx] != app_labels[idx - 1]:
            ax.axvline(idx - 0.5, color="#3182bd", linewidth=1.2, linestyle=":", alpha=0.9)


def plot_metric_bars(
    ax: plt.Axes,
    values: list[float | int],
    labels: list[str],
    app_labels: list[str],
    ylabel: str,
    value_formatter,
    style: PlotStyle,
    *,
    show_xlabels: bool = True,
    y_scientific_exponent: int | None = None,
) -> None:
    colors = ["#2b8cbe" if value >= 0 else "#d95f0e" for value in values]
    bars = ax.bar(
        range(len(values)),
        values,
        width=style.bar_width,
        color=colors,
        edgecolor="black",
        linewidth=0.8,
    )
    ax.axhline(0, color="black", linewidth=1)
    ax.set_ylabel(ylabel, fontsize=style.y_label_font)
    ax.tick_params(axis="y", labelsize=style.y_tick_font)
    if y_scientific_exponent is not None:
        apply_scientific_yaxis(ax, values, y_scientific_exponent)
    ax.set_xticks(range(len(values)))
    if show_xlabels:
        ax.set_xticklabels(labels, rotation=style.x_rotation, ha="right", fontsize=style.x_tick_font)
        ax.tick_params(axis="x", labelsize=style.x_tick_font)
    else:
        ax.set_xticklabels([])
        ax.tick_params(axis="x", labelbottom=False)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    add_app_dividers(ax, app_labels)

    for bar, value in zip(bars, values):
        y_offset = (max(abs(v) for v in values) * 0.02) if values else 0.0
        if value >= 0:
            y = value + y_offset
            va = "bottom"
        else:
            y = value - y_offset
            va = "top"
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            y,
            value_formatter(value),
            ha="center",
            va=va,
            fontsize=style.annotation_font,
        )


def main() -> None:
    args = parse_args()
    style = PLOT_STYLES[args.workload]

    init_stats = load_stats_by_trace(args.init_dir)
    best_stats = load_stats_by_trace(args.best_dir)

    missing_init = sorted(set(best_stats) - set(init_stats))
    missing_best = sorted(set(init_stats) - set(best_stats))
    if missing_init or missing_best:
        raise ValueError(
            "Trace mismatch between directories. "
            f"Missing from init: {missing_init}. Missing from best: {missing_best}."
        )

    trace_names = sorted(init_stats)
    changes = [compute_changes(init_stats[name], best_stats[name]) for name in trace_names]
    labels = [display_label(name) for name in trace_names]
    app_labels = [app_label(label) for label in labels]

    issued_deltas = [change.issued_delta for change in changes]
    useful_count_deltas = [change.useful_delta for change in changes]
    hit_rate_deltas = [change.hit_rate_delta_pp for change in changes]

    fig, axes = plt.subplots(3, 1, figsize=style.figsize, sharex=True)
    plot_metric_bars(
        axes[0],
        issued_deltas,
        labels,
        app_labels,
        "Difference in Prefetches Issued \n (Evolved - Initial)",
        format_millions,
        style,
        show_xlabels=False,
    )
    useful_exponent = pick_scientific_exponent(useful_count_deltas)
    plot_metric_bars(
        axes[1],
        useful_count_deltas,
        labels,
        app_labels,
        f"Difference in Useful Prefetches \n (Evolved - Initial)\n(×10^{useful_exponent})",
        format_millions,
        style,
        show_xlabels=False,
        y_scientific_exponent=useful_exponent,
    )
    plot_metric_bars(
        axes[2],
        hit_rate_deltas,
        labels,
        app_labels,
        "Difference in Hit Rate (percentage points) \n (Evolved - Initial)",
        lambda value: f"{value:.1f}pp",
        style,
        show_xlabels=True,
    )
    axes[2].set_xlabel("Trace", fontsize=style.x_label_font)

    mean_issued_delta = sum(issued_deltas) / len(issued_deltas)
    mean_useful_count_delta = sum(useful_count_deltas) / len(useful_count_deltas)
    mean_hit_rate_delta = sum(hit_rate_deltas) / len(hit_rate_deltas)

    print(f"Traces plotted: {len(trace_names)}")
    print(f"Mean difference in prefetches issued: {format_millions(int(mean_issued_delta))}")
    print(f"Mean difference in useful prefetches: {format_millions(int(mean_useful_count_delta))}")
    print(f"Mean difference in L2C hit rate: {mean_hit_rate_delta:+.2f} pp")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.subplots_adjust(bottom=style.bottom_margin, hspace=0.35)
    fig.savefig(args.output, format="pdf", bbox_inches="tight")
    print(f"Saved plot to {args.output}")

    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
