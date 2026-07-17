#!/usr/bin/env python3
"""Generate Cyrinx 2.0 paper figures from tracked evidence files.

The script uses no raw audio and performs no hardware I/O. Values come from the
tracked Pixel results ledger and iOS phase-coherence JSON named below.

Run: .venv/bin/python scripts/gen-cyrinx2-paper-figures.py
"""
import json
import os

# Matplotlib's PDF backend otherwise records the wall-clock generation time.
# Pin it to the measurement date so identical evidence produces byte-identical
# figure assets on repeated runs with the same toolchain.
os.environ.setdefault("SOURCE_DATE_EPOCH", "1784246400")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
LEDGER = os.path.join(
    REPO,
    "scratch",
    "hw20k",
    "evidence",
    "pixel7a-cyrinx2-2026-07-17",
    "results-ledger.json",
)
IOS_PHASE = os.path.join(REPO, "scratch", "hw20k", "data", "ios_phase_coherence.json")
OUT = os.path.join(REPO, "docs", "whitepaper")

INK = "#1b1730"
PURPLE = "#7257c8"
AMBER = "#d07b16"
GRAY = "#7b7787"
GRID = "#d8d4df"


def load_ledger():
    with open(LEDGER, encoding="utf-8") as stream:
        return json.load(stream)


def save(fig, stem):
    pdf = os.path.join(OUT, f"{stem}.pdf")
    png = os.path.join(OUT, f"{stem}.png")
    fig.savefig(pdf, bbox_inches="tight")
    fig.savefig(png, dpi=180, bbox_inches="tight")
    print(f"wrote {pdf}")
    print(f"wrote {png}")


def goodput_reliability(ledger):
    labels = {
        "pixel7a-p16-sym64-gap12000-fresh": "flagship\np16 · 64 sym · 250 ms gaps",
        "pixel7a-p64-sym96-gap0": "peak\np64 · 96 sym · zero gap",
        "pixel7a-p16-sym128-gap0": "long frame\np16 · 128 sym · zero gap",
        "pixel7a-p16-sym64-gap0": "dense pilots\np16 · 64 sym · zero gap",
        "pixel7a-p32-sym64-gap0": "mid pilots\np32 · 64 sym · zero gap",
    }

    fig, ax = plt.subplots(figsize=(7.25, 4.2))
    for campaign in ledger["campaigns"]:
        candidate = campaign["candidate"]
        control = campaign["paired_control"]
        primary = campaign["claim_role"].startswith("primary")
        peak = campaign["id"] == "pixel7a-p64-sym96-gap0"
        color = PURPLE if primary else AMBER if peak else GRAY
        mean = candidate["mean_scheduled_goodput_bps"] / 1000
        ax.errorbar(
            candidate["block_success_rate"] * 100,
            mean,
            yerr=[
                [mean - candidate["minimum_scheduled_goodput_bps"] / 1000],
                [candidate["maximum_scheduled_goodput_bps"] / 1000 - mean],
            ],
            fmt="o",
            markersize=9 if primary or peak else 7,
            color=color,
            markeredgecolor="white",
            markeredgewidth=0.8,
            capsize=2.5,
            elinewidth=0.9,
            zorder=3,
        )
        ax.annotate(
            labels[campaign["id"]],
            (
                candidate["block_success_rate"] * 100,
                candidate["mean_scheduled_goodput_bps"] / 1000,
            ),
            xytext=(-6 if primary else 5, 7 if peak else -23 if primary else 6),
            textcoords="offset points",
            fontsize=7.4,
            ha="right" if primary else "left",
            color=INK,
        )
        ax.scatter(
            control["block_success_rate"] * 100,
            control["mean_scheduled_goodput_bps"] / 1000,
            marker="D",
            s=24,
            color="#aaa5b3",
            alpha=0.78,
            zorder=2,
        )

    ax.axhline(
        ledger["accepted_reference_scheduled_goodput_bps"] / 1000,
        color=INK,
        linestyle=(0, (4, 3)),
        linewidth=1.0,
        label="accepted 1.x reference (36.571 kbps)",
    )
    ax.set_xlim(87.5, 100.35)
    ax.set_ylim(34, 74)
    ax.set_xlabel("scheduled block recovery (%)")
    ax.set_ylabel("mean scheduled payload goodput (kbps)")
    ax.grid(True, color=GRID, linewidth=0.55, alpha=0.8)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    ax.text(
        0.99,
        0.03,
        "diamonds: paired conservative controls",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=7.3,
        color=GRAY,
    )
    fig.subplots_adjust(left=0.11, right=0.98, top=0.97, bottom=0.14)
    save(fig, "cyrinx2-goodput-reliability")
    plt.close(fig)


def receiver_replay(ledger):
    replay = ledger["isolated_receiver_replay"]
    total = replay["total_blocks"]
    values = [replay["legacy_verified_blocks"], replay["current_verified_blocks"]]
    percents = [100 * value / total for value in values]

    fig, ax = plt.subplots(figsize=(5.9, 3.35))
    bars = ax.bar(
        ["global-only\nreliability", "pilot-local\nreliability"],
        percents,
        color=["#aaa5b3", PURPLE],
        width=0.58,
    )
    for bar, value, percent in zip(bars, values, percents):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            percent + 0.55,
            f"{value:,}/{total:,}\n{percent:.2f}%",
            ha="center",
            va="bottom",
            fontsize=9,
            color=INK,
        )
    ax.text(
        0.5,
        95.0,
        f"+{replay['delta_verified_blocks']} blocks\n8/8 runs improved",
        ha="center",
        va="center",
        fontsize=8.4,
        color=INK,
        bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "edgecolor": GRID},
    )
    ax.set_ylim(82, 101.8)
    ax.set_ylabel("ordered block recovery (%)")
    ax.grid(axis="y", color=GRID, linewidth=0.55, alpha=0.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    fig.subplots_adjust(left=0.14, right=0.98, top=0.94, bottom=0.18)
    save(fig, "cyrinx2-receiver-replay")
    plt.close(fig)


def ultrasonic_phase():
    with open(IOS_PHASE, encoding="utf-8") as stream:
        phase = json.load(stream)

    conditions = phase["conditions"]
    labels = [
        (
            f"{condition['freq_hz'] / 1000:g} kHz\n"
            f"peak {condition['amp']:g} · n={len(condition['phase_std_rad'])}"
        )
        for condition in conditions
    ]
    medians = [condition["std_median"] for condition in conditions]
    lower = [
        condition["std_median"] - condition["std_min"] for condition in conditions
    ]
    upper = [
        condition["std_max"] - condition["std_median"] for condition in conditions
    ]
    colors = [PURPLE if condition["interp"] == "coherent" else AMBER for condition in conditions]

    fig, ax = plt.subplots(figsize=(7.15, 3.55))
    positions = list(range(len(conditions)))
    for index, condition in enumerate(conditions):
        values = condition["phase_std_rad"]
        ax.scatter(
            [index] * len(values),
            values,
            s=17,
            color=colors[index],
            alpha=0.48,
            linewidth=0,
            zorder=2,
        )
    ax.errorbar(
        positions,
        medians,
        yerr=[lower, upper],
        fmt="D",
        color=INK,
        markersize=4.8,
        capsize=3,
        linewidth=1.0,
        zorder=3,
    )
    ax.set_yscale("log")
    ax.set_xticks(positions, labels)
    ax.set_ylabel("phase-jitter std. dev. (rad)")
    ax.grid(axis="y", which="both", color=GRID, linewidth=0.55, alpha=0.8)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.text(
        0.01,
        0.97,
        "iPhone 17 Pro Max speaker → Mac microphone · 96 kHz capture",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=8,
        color=GRAY,
    )
    fig.subplots_adjust(left=0.12, right=0.98, top=0.96, bottom=0.2)
    save(fig, "cyrinx2-ultrasonic-phase")
    plt.close(fig)


def main():
    ledger = load_ledger()
    goodput_reliability(ledger)
    receiver_replay(ledger)
    ultrasonic_phase()


if __name__ == "__main__":
    main()
