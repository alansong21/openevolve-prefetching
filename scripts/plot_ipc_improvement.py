#!/usr/bin/env python3
"""Plot per-trace IPC percent improvement for evolved vs initial program."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    import matplotlib.pyplot as plt
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing dependency: matplotlib. Install it with `pip install matplotlib` "
        "or add it to your environment, then rerun this script."
    ) from exc


TRACE_IPC_INITIAL = {
    "602.gcc_s-1850B.champsimtrace.xz": 0.8186,
    "602.gcc_s-734B.champsimtrace.xz": 1.128,
    "603.bwaves_s-1740B.champsimtrace.xz": 1.695,
    "603.bwaves_s-891B.champsimtrace.xz": 1.271,
    "605.mcf_s-1536B.champsimtrace.xz": 0.3534,
    "605.mcf_s-1554B.champsimtrace.xz": 0.2484,
    "605.mcf_s-1644B.champsimtrace.xz": 0.194,
    "605.mcf_s-472B.champsimtrace.xz": 0.4566,
    "605.mcf_s-484B.champsimtrace.xz": 0.5675,
    "607.cactuBSSN_s-2421B.champsimtrace.xz": 1.919,
    "619.lbm_s-2676B.champsimtrace.xz": 0.904,
    "619.lbm_s-2677B.champsimtrace.xz": 0.5156,
    "620.omnetpp_s-874B.champsimtrace.xz": 0.4751,
    "621.wrf_s-6673B.champsimtrace.xz": 1.369,
    "623.xalancbmk_s-10B.champsimtrace.xz": 0.354,
    "649.fotonik3d_s-1176B.champsimtrace.xz": 1.54,
    "649.fotonik3d_s-7084B.champsimtrace.xz": 1.676,
    "654.roms_s-293B.champsimtrace.xz": 2.035,
    "654.roms_s-294B.champsimtrace.xz": 2.107,
    "654.roms_s-523B.champsimtrace.xz": 1.134,
}

TRACE_IPC_EVOLVED = {
    "602.gcc_s-1850B.champsimtrace.xz": 0.8757,
    "602.gcc_s-734B.champsimtrace.xz": 1.174,
    "603.bwaves_s-1740B.champsimtrace.xz": 1.477,
    "603.bwaves_s-891B.champsimtrace.xz": 1.52,
    "605.mcf_s-1536B.champsimtrace.xz": 0.3676,
    "605.mcf_s-1554B.champsimtrace.xz": 0.2545,
    "605.mcf_s-1644B.champsimtrace.xz": 0.1954,
    "605.mcf_s-472B.champsimtrace.xz": 0.4817,
    "605.mcf_s-484B.champsimtrace.xz": 0.6154,
    "607.cactuBSSN_s-2421B.champsimtrace.xz": 1.851,
    "619.lbm_s-2676B.champsimtrace.xz": 0.9301,
    "619.lbm_s-2677B.champsimtrace.xz": 0.5235,
    "620.omnetpp_s-874B.champsimtrace.xz": 0.4668,
    "621.wrf_s-6673B.champsimtrace.xz": 1.467,
    "623.xalancbmk_s-10B.champsimtrace.xz": 0.3563,
    "649.fotonik3d_s-1176B.champsimtrace.xz": 2.02,
    "649.fotonik3d_s-7084B.champsimtrace.xz": 1.871,
    "654.roms_s-293B.champsimtrace.xz": 2.08,
    "654.roms_s-294B.champsimtrace.xz": 2.116,
    "654.roms_s-523B.champsimtrace.xz": 1.307,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a per-trace bar plot of IPC percent improvement: "
            "(evolved - initial) / initial * 100."
        )
    )
    parser.add_argument(
        "--output",
        default="plots/ipc_percent_improvement.png",
        help="Horizontal plot output PNG file path.",
    )
    parser.add_argument(
        "--vertical-output",
        default=None,
        help=(
            "Vertical plot output PNG file path. "
            "Default: same as --output with '_vertical' suffix."
        ),
    )
    parser.add_argument(
        "--side-by-side-output",
        default=None,
        help=(
            "Side-by-side IPC comparison output PNG file path. "
            "Default: same as --output with '_side_by_side' suffix."
        ),
    )
    parser.add_argument(
        "--mean-output",
        default=None,
        help=(
            "Mean IPC comparison output PNG file path. "
            "Default: same as --output with '_mean' suffix."
        ),
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display the figure interactively in addition to saving.",
    )
    return parser.parse_args()


def default_vertical_output(horizontal_output: Path) -> Path:
    return horizontal_output.with_name(f"{horizontal_output.stem}_vertical{horizontal_output.suffix}")


def default_side_by_side_output(horizontal_output: Path) -> Path:
    return horizontal_output.with_name(
        f"{horizontal_output.stem}_side_by_side{horizontal_output.suffix}"
    )


def default_mean_output(horizontal_output: Path) -> Path:
    return horizontal_output.with_name(f"{horizontal_output.stem}_mean{horizontal_output.suffix}")


def main() -> None:
    args = parse_args()

    missing = sorted(set(TRACE_IPC_INITIAL) ^ set(TRACE_IPC_EVOLVED))
    if missing:
        raise ValueError(f"Trace mismatch between datasets: {missing}")

    trace_names = sorted(TRACE_IPC_INITIAL)
    percent_improvement = [
        ((TRACE_IPC_EVOLVED[name] - TRACE_IPC_INITIAL[name]) / TRACE_IPC_INITIAL[name]) * 100.0
        for name in trace_names
    ]
    display_labels = [name.removesuffix(".champsimtrace.xz") for name in trace_names]
    app_labels = [label.split("-", 1)[0] for label in display_labels]

    colors = ["#2b8cbe" if value >= 0 else "#d95f0e" for value in percent_improvement]

    fig_h, ax_h = plt.subplots(figsize=(12, 9))
    bars_h = ax_h.barh(
        range(len(trace_names)),
        percent_improvement,
        color="none",
        edgecolor=colors,
        linewidth=1.0,
    )
    for bar, value in zip(bars_h, percent_improvement):
        bar.set_hatch("//////" if value >= 0 else "\\\\\\\\\\\\")
    ax_h.axvline(0, color="black", linewidth=1)

    ax_h.set_title("IPC Percent Improvement Per Trace (Evolved vs Initial) - Horizontal")
    ax_h.set_xlabel("Percent Improvement (%)")
    ax_h.set_ylabel("Trace")
    ax_h.set_yticks(range(len(trace_names)))
    ax_h.set_yticklabels(display_labels, fontsize=8)
    ax_h.grid(axis="x", linestyle="--", alpha=0.4)

    # Draw separators where the application prefix changes.
    for idx in range(1, len(app_labels)):
        if app_labels[idx] != app_labels[idx - 1]:
            ax_h.axhline(idx - 0.5, color="#3182bd", linewidth=1.2, linestyle=":", alpha=0.9)

    for bar, value in zip(bars_h, percent_improvement):
        x_offset = 0.2 if value >= 0 else -0.2
        ha = "left" if value >= 0 else "right"
        ax_h.text(
            value + x_offset,
            bar.get_y() + bar.get_height() / 2.0,
            f"{value:.1f}%",
            ha=ha,
            va="center",
            fontsize=7,
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig_h.tight_layout()
    fig_h.savefig(output_path, dpi=200)
    print(f"Saved horizontal plot to {output_path}")

    vertical_output = (
        Path(args.vertical_output) if args.vertical_output else default_vertical_output(output_path)
    )
    vertical_output.parent.mkdir(parents=True, exist_ok=True)

    vertical_axis_label_fontsize = 14
    vertical_tick_fontsize = 11
    vertical_value_fontsize = 10

    fig_v, ax_v = plt.subplots(figsize=(14, 7))
    bars_v = ax_v.bar(
        range(len(trace_names)),
        percent_improvement,
        color=colors,
        edgecolor="black",
        linewidth=0.8,
    )
    ax_v.axhline(0, color="black", linewidth=1)

    ax_v.set_xlabel("Trace", fontsize=vertical_axis_label_fontsize)
    ax_v.set_ylabel("IPC Improvement (%)", fontsize=vertical_axis_label_fontsize)
    ax_v.set_xticks(range(len(trace_names)))
    ax_v.set_xticklabels(
        display_labels,
        rotation=60,
        ha="right",
        fontsize=vertical_tick_fontsize,
    )
    ax_v.tick_params(axis="y", labelsize=vertical_tick_fontsize)
    ax_v.grid(axis="y", linestyle="--", alpha=0.4)

    # Draw separators where the application prefix changes.
    for idx in range(1, len(app_labels)):
        if app_labels[idx] != app_labels[idx - 1]:
            ax_v.axvline(idx - 0.5, color="#3182bd", linewidth=1.2, linestyle=":", alpha=0.9)

    for bar, value in zip(bars_v, percent_improvement):
        y_offset = 0.3 if value >= 0 else -0.3
        va = "bottom" if value >= 0 else "top"
        ax_v.text(
            bar.get_x() + bar.get_width() / 2.0,
            value + y_offset,
            f"{value:.1f}%",
            ha="center",
            va=va,
            fontsize=vertical_value_fontsize,
            rotation=0,
        )

    fig_v.tight_layout()
    fig_v.savefig(vertical_output, dpi=200)
    print(f"Saved vertical plot to {vertical_output}")

    side_by_side_output = (
        Path(args.side_by_side_output)
        if args.side_by_side_output
        else default_side_by_side_output(output_path)
    )
    side_by_side_output.parent.mkdir(parents=True, exist_ok=True)

    fig_s, ax_s = plt.subplots(figsize=(15, 7))
    x_positions = list(range(len(trace_names)))
    bar_width = 0.42
    initial_ipc = [TRACE_IPC_INITIAL[name] for name in trace_names]
    evolved_ipc = [TRACE_IPC_EVOLVED[name] for name in trace_names]
    mean_initial_ipc = sum(initial_ipc) / len(initial_ipc)
    mean_evolved_ipc = sum(evolved_ipc) / len(evolved_ipc)

    bars_initial = ax_s.bar(
        [x - bar_width / 2.0 for x in x_positions],
        initial_ipc,
        width=bar_width,
        color="none",
        edgecolor="#3182bd",
        linewidth=1.0,
        hatch="//////",
        label="Initial IPC",
    )
    bars_evolved = ax_s.bar(
        [x + bar_width / 2.0 for x in x_positions],
        evolved_ipc,
        width=bar_width,
        color="none",
        edgecolor="#f16913",
        linewidth=1.0,
        hatch="\\\\\\\\\\\\",
        label="Evolved IPC",
    )

    ax_s.set_title("IPC Per Trace (Initial vs Evolved)")
    ax_s.set_xlabel("Trace")
    ax_s.set_ylabel("IPC")
    ax_s.set_xticks(x_positions)
    ax_s.set_xticklabels(display_labels, rotation=60, ha="right", fontsize=8)
    ax_s.grid(axis="y", linestyle="--", alpha=0.4)
    ax_s.axhline(
        mean_initial_ipc,
        color="#3182bd",
        linestyle="--",
        linewidth=1.2,
        label=f"Mean Initial IPC ({mean_initial_ipc:.3f})",
    )
    ax_s.axhline(
        mean_evolved_ipc,
        color="#f16913",
        linestyle="--",
        linewidth=1.2,
        label=f"Mean Evolved IPC ({mean_evolved_ipc:.3f})",
    )
    ax_s.legend()

    # Draw separators where the application prefix changes.
    for idx in range(1, len(app_labels)):
        if app_labels[idx] != app_labels[idx - 1]:
            ax_s.axvline(idx - 0.5, color="#3182bd", linewidth=1.2, linestyle=":", alpha=0.9)

    for bar in bars_initial:
        ax_s.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + 0.015,
            f"{bar.get_height():.3g}",
            ha="center",
            va="bottom",
            fontsize=6,
        )
    for bar in bars_evolved:
        ax_s.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + 0.015,
            f"{bar.get_height():.3g}",
            ha="center",
            va="bottom",
            fontsize=6,
        )

    fig_s.tight_layout()
    fig_s.savefig(side_by_side_output, dpi=200)
    print(f"Saved side-by-side IPC plot to {side_by_side_output}")

    mean_output = Path(args.mean_output) if args.mean_output else default_mean_output(output_path)
    mean_output.parent.mkdir(parents=True, exist_ok=True)

    fig_m, ax_m = plt.subplots(figsize=(7, 6))
    mean_labels = ["Initial Mean IPC", "Evolved Mean IPC"]
    mean_values = [mean_initial_ipc, mean_evolved_ipc]
    mean_colors = ["#3182bd", "#f16913"]
    bars_m = ax_m.bar(mean_labels, mean_values, color=mean_colors, edgecolor="black", linewidth=0.8)
    ax_m.set_title("Mean IPC Across Traces")
    ax_m.set_ylabel("IPC")
    ax_m.grid(axis="y", linestyle="--", alpha=0.4)
    for bar, value in zip(bars_m, mean_values):
        ax_m.text(
            bar.get_x() + bar.get_width() / 2.0,
            value + 0.01,
            f"{value:.4f}",
            ha="center",
            va="bottom",
            fontsize=10,
        )

    fig_m.tight_layout()
    fig_m.savefig(mean_output, dpi=200)
    print(f"Saved mean IPC plot to {mean_output}")

    if args.show:
        plt.show()


if __name__ == "__main__":
    main()
