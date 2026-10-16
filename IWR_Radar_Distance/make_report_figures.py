"""Builds report figures from logs/experiment/ into report/figures/."""

import csv
import glob
import os
import statistics as st
from collections import Counter, defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "logs", "experiment")
OUT = os.path.join(HERE, "report", "figures")
os.makedirs(OUT, exist_ok=True)

BIN_M = 0.04360212
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

COLOR = {  # colour follows the entity (top material), fixed across all charts
    "empty": MUTED,
    "metal": "#2a78d6",
    "plastic": "#eb6834",
    "cardboard": "#1baf7a",
    "felt": "#eda100",
    "plastic_on_metal": "#eb6834",
    "cardboard_on_metal": "#1baf7a",
    "felt_on_metal": "#eda100",
}
NAME = {
    "empty": "Empty desk",
    "metal": "Metal",
    "plastic": "Plastic",
    "cardboard": "Cardboard",
    "felt": "Felt",
    "cardboard_on_metal": "Cardboard on metal",
    "plastic_on_metal": "Plastic on metal",
    "felt_on_metal": "Felt on metal",
}
ORDER = ["empty", "metal", "plastic", "cardboard", "felt",
         "cardboard_on_metal", "plastic_on_metal", "felt_on_metal"]

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans"],
    "font.size": 10,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": AXIS,
    "axes.labelcolor": INK2,
    "axes.titlecolor": INK,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelcolor": INK2,
    "ytick.labelcolor": INK2,
    "legend.frameon": False,
    "legend.labelcolor": INK2,
    "savefig.dpi": 200,
    "savefig.bbox": "tight",
})


def subtitle(ax, text):
    ax.text(0, 1.02, text, transform=ax.transAxes, color=INK2, fontsize=9.5, va="bottom")


def footnote(fig, text):
    fig.text(0.01, -0.02, text, color=MUTED, fontsize=8, va="top", wrap=True)


# ------------------------------------------------------------------ load --
summary = list(csv.DictReader(open(os.path.join(DATA, "summary.csv"))))
by_label = defaultdict(list)
for r in summary:
    by_label[r["label"]].append(r)

desk_m = st.fmean(float(r["mean_m"]) for r in by_label["empty"])


def mean_profile(label):
    acc, n = None, 0
    for f in sorted(glob.glob(os.path.join(DATA, f"{label}_t*_profile.csv"))):
        d = [float(r["mean_db"]) for r in csv.DictReader(open(f))]
        acc = d if acc is None else [a + b for a, b in zip(acc, d)]
        n += 1
    return [a / n for a in acc]


def point_ranges(label):
    """Returns (Counter of range_m -> number of frames containing it, total frames)."""
    counts, frames = Counter(), 0
    for r in by_label[label]:
        frames += int(r["frames"])
        rows = list(csv.DictReader(open(os.path.join(DATA, r["points_file"]))))
        per_frame = defaultdict(set)
        for p in rows:
            per_frame[(r["trial"], p["frame_number"])].add(round(float(p["range_m"]), 4))
        for rs in per_frame.values():
            counts.update(rs)
    return counts, frames


profiles = {lbl: mean_profile(lbl) for lbl in ORDER}
x_bins = [i * BIN_M for i in range(len(profiles["empty"]))]


def radar_expected(label):
    return desk_m - float(by_label[label][0]["object_height_mm"]) / 1000


# ------------------------------------------------- 1. profiles, single box --
def plot_profiles(labels, fname, title, sub, label_at):
    """label_at: label -> (bin index to point at, (text_x_m, text_y_db))."""
    fig, ax = plt.subplots(figsize=(9, 5))
    lo, hi = 3, 23
    for lbl in labels:
        ls = (0, (4, 3)) if lbl == "empty" else "-"
        ax.plot(x_bins[lo:hi], profiles[lbl][lo:hi], color=COLOR[lbl], lw=2, ls=ls,
                label=NAME[lbl], marker="o", ms=3.5)
        b, xy_text = label_at[lbl]
        ax.annotate(NAME[lbl], (x_bins[b], profiles[lbl][b]), xytext=xy_text,
                    color=INK2, fontsize=9, va="center",
                    arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.7, shrinkA=2, shrinkB=3))
    ax.axvline(desk_m, color=AXIS, lw=1)
    ax.text(desk_m + 0.005, ax.get_ylim()[0] + 0.5, "desk surface\n(as measured by radar)",
            color=MUTED, fontsize=8)
    ax.set_xlabel("Range from radar (m)")
    ax.set_ylabel("Signal level (dB, relative)")
    ax.set_title(title, pad=22)
    subtitle(ax, sub)
    ax.legend(loc="lower right", fontsize=9)
    footnote(fig, "Range-profile TLV from TI mmWave SDK 3.5 OOB demo, averaged over every frame "
                  "of every trial for that condition. Points are range-bin centres (bin width 4.36 cm).")
    fig.savefig(os.path.join(OUT, fname))
    plt.close(fig)


plot_profiles(
    ["empty", "metal", "plastic", "cardboard", "felt"],
    "fig1_range_profile_single_boxes.png",
    "Range profile by material (single box on desk)",
    "Metal, plastic and cardboard form a distinct peak at their top surface; felt gives a flat, smeared return",
    {"metal": (10, (0.25, 48.5)), "plastic": (12, (0.62, 48.6)),
     "empty": (14, (0.68, 46.6)), "cardboard": (11, (0.54, 37.8)), "felt": (10, (0.24, 41.8))},
)

plot_profiles(
    ["metal", "cardboard_on_metal", "plastic_on_metal", "felt_on_metal"],
    "fig2_range_profile_stacked.png",
    "Range profile with a second box stacked on the metal box",
    "Plastic forms a peak one bin above the metal; cardboard merges into a broad lump; felt suppresses the metal return",
    {"metal": (10, (0.50, 49.2)), "cardboard_on_metal": (10, (0.50, 47.4)),
     "plastic_on_metal": (8, (0.17, 47.6)), "felt_on_metal": (8, (0.17, 44.2))},
)

# ------------------------------------------------- 3. peak signal dot plot --
fig, ax = plt.subplots(figsize=(8, 4))
labels = ["metal", "plastic", "cardboard", "empty", "felt"]
for i, lbl in enumerate(labels):
    vals = [float(r["profile_peak_db"]) for r in by_label[lbl]]
    m = st.fmean(vals)
    sd = st.stdev(vals) if len(vals) > 1 else 0
    y = len(labels) - 1 - i
    ax.errorbar(m, y, xerr=sd, fmt="o", color=COLOR[lbl], ms=9, capsize=4, lw=1.5,
                markeredgecolor=SURFACE, markeredgewidth=2)
    note = "  (never detected)" if lbl == "felt" else ""
    ax.text(m + sd + 0.25, y, f"{m:.1f} dB ±{sd:.1f}{note}", va="center", color=INK2, fontsize=9)
ax.set_yticks(range(len(labels)))
ax.set_yticklabels([NAME[l] for l in reversed(labels)])
ax.set_xlim(41.5, 52)
ax.grid(axis="y", visible=False)
ax.set_xlabel("Peak signal at the target's range (dB, relative)")
ax.set_title("Reflected signal strength by material", pad=22)
subtitle(ax, "Mean of trials ± 1 standard deviation (3 trials per box, 2 for empty desk)")
footnote(fig, "Peak = highest range-profile bin within ±3 bins (±13 cm) of the target's expected range. "
              "Values are relative, not calibrated RCS.")
fig.savefig(os.path.join(OUT, "fig3_signal_strength_by_material.png"))
plt.close(fig)

# --------------------------------------------------- 4. detection rate bars --
fig, ax = plt.subplots(figsize=(8, 4.6))
for i, lbl in enumerate(ORDER):
    rate = st.fmean(float(r["detection_rate"]) for r in by_label[lbl]) * 100
    y = len(ORDER) - 1 - i
    ax.barh(y, rate, height=0.6, color="#2a78d6")
    txt = f"{rate:.0f}%" if rate > 0 else "0%  (not detected)"
    ax.text(rate + 1.5, y, txt, va="center", color=INK2, fontsize=9)
ax.set_yticks(range(len(ORDER)))
ax.set_yticklabels([f"{NAME[l]}  (n={len(by_label[l])})" for l in reversed(ORDER)])
ax.set_xlim(0, 118)
ax.set_xticks([0, 25, 50, 75, 100])
ax.grid(axis="y", visible=False)
ax.set_xlabel("Frames with a detection at the target (%)")
ax.set_title("Detection rate by condition", pad=22)
subtitle(ax, "A frame counts if the CFAR detector reported a point within ±10 cm of the expected top surface")
footnote(fig, "~150 frames per 15 s trial at 10 frames/s. n = number of trials.")
fig.savefig(os.path.join(OUT, "fig4_detection_rate.png"))
plt.close(fig)

# --------------------------------------------- 5. measured vs actual height --
fig, ax = plt.subplots(figsize=(6.5, 6))
half_bin_mm = BIN_M * 1000 / 2
line = [0, 220]
ax.fill_between(line, [v - half_bin_mm for v in line], [v + half_bin_mm for v in line],
                color="#cde2fb", lw=0, label="± half a range bin (±21.8 mm)")
ax.plot(line, line, color=AXIS, lw=1, label="measured = actual")
for lbl in ["metal", "plastic", "cardboard", "plastic_on_metal"]:
    actual = float(by_label[lbl][0]["object_height_mm"])
    measured = (desk_m - st.fmean(float(r["mean_m"]) for r in by_label[lbl] if r["mean_m"])) * 1000
    ax.plot(actual, measured, "o", color=COLOR[lbl], ms=9, markeredgecolor=SURFACE, markeredgewidth=2)
    off = {"plastic": (8, 6), "cardboard": (4, -30), "metal": (8, -12), "plastic_on_metal": (-30, 10)}[lbl]
    ax.annotate(f"{NAME[lbl]}\n{measured:.0f} vs {actual:.0f} mm", (actual, measured), xytext=off,
                textcoords="offset points", color=INK2, fontsize=8.5,
                ha="right" if off[0] < 0 else "left")
ax.set_xlim(0, 220)
ax.set_ylim(0, 220)
ax.set_aspect("equal")
ax.set_xlabel("Actual box height (mm)")
ax.set_ylabel("Measured height = desk reading − box reading (mm)")
ax.set_title("Measured vs actual height", pad=22)
subtitle(ax, "Detected conditions only; heights relative to the radar's own desk reading")
ax.legend(loc="upper left", fontsize=8.5)
footnote(fig, "Not detected (no point plotted): felt (80 mm), cardboard on metal (170 mm), felt on metal (215 mm).")
fig.savefig(os.path.join(OUT, "fig5_measured_vs_actual_height.png"))
plt.close(fig)

# ------------------------------------------------ 6. where detections landed --
fig, ax = plt.subplots(figsize=(9.5, 5.4))
x_max = 1.0
outliers = 0
for i, lbl in enumerate(ORDER):
    y = len(ORDER) - 1 - i
    counts, frames = point_ranges(lbl)
    for rng, c in counts.items():
        if rng > x_max:
            outliers += c
            continue
        frac = c / frames
        ax.scatter(rng, y, s=20 + 380 * frac, color="#2a78d6", alpha=0.85,
                   edgecolors=SURFACE, linewidths=1.5, zorder=3)
    if lbl != "empty":
        exp = radar_expected(lbl)
        ax.plot([exp, exp], [y - 0.32, y + 0.32], color=INK, lw=1.5, zorder=4)
ax.axvline(desk_m, color=AXIS, lw=1, zorder=1)
ax.text(desk_m + 0.006, len(ORDER) - 0.45, "desk", color=MUTED, fontsize=8)
ghost_y = len(ORDER) - 1 - ORDER.index("cardboard_on_metal")
ax.annotate("metal box's 2nd-bounce ghost,\nseen through the cardboard (Fig. 7)", (0.785, ghost_y),
            xytext=(0.80, ghost_y + 0.9), color=INK2, fontsize=8.5,
            arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.8))
ax.set_yticks(range(len(ORDER)))
ax.set_yticklabels([NAME[l] for l in reversed(ORDER)])
ax.set_xlim(0.2, x_max)
ax.set_ylim(-0.6, len(ORDER) - 0.2)
ax.grid(axis="y", visible=False)
ax.set_xlabel("Range of detected point (m)")
ax.set_title("Where the radar reported detections", pad=22)
subtitle(ax, "Dot area = share of frames with a detection at that range;  black tick = expected top surface "
             "(desk reading − box height)")
foot = "All trials pooled per condition."
if outliers:
    foot += (f" {outliers} detections beyond {x_max:.1f} m not shown - almost all are metal/plastic "
             "multipath ghosts (Fig. 7).")
footnote(fig, foot)
fig.savefig(os.path.join(OUT, "fig6_detection_locations.png"))
plt.close(fig)

# --------------------------------------------------- 7. multipath ghost ladder --
true_desk = float(by_label["empty"][0]["mount_height_m"])
bias = desk_m - true_desk
ladder = ["metal", "plastic", "cardboard", "felt", "empty"]
fit_text = {}
fig, ax = plt.subplots(figsize=(10, 4.6))
for i, lbl in enumerate(ladder):
    y = len(ladder) - 1 - i
    true_d = true_desk - float(by_label[lbl][0]["object_height_mm"]) / 1000
    for k in range(1, 7):
        pred = k * true_d + bias
        if pred <= 2.6:
            ax.plot([pred, pred], [y - 0.3, y + 0.3], color=AXIS, lw=1.2, zorder=1)
            if i == 0:
                ax.text(pred, y + 0.42, f"k={k}", ha="center", color=MUTED, fontsize=7.5)
    counts, frames = point_ranges(lbl)
    ks, rs = [], []
    for rng, c in counts.items():
        frac = c / frames
        if rng <= 2.6:
            ax.scatter(rng, y, s=15 + 380 * frac, color=COLOR[lbl] if lbl != "empty" else MUTED,
                       alpha=0.9, edgecolors=SURFACE, linewidths=1.5, zorder=3)
        if frac >= 0.05 and rng <= 2.6:
            k = round((rng - bias) / true_d)
            if k >= 1 and abs(rng - (k * true_d + bias)) < 0.08:
                ks.append(k)
                rs.append(rng)
    if len(set(ks)) >= 3:
        mk, mr = st.fmean(ks), st.fmean(rs)
        slope = sum((a - mk) * (b - mr) for a, b in zip(ks, rs)) / sum((a - mk) ** 2 for a in ks)
        fit_text[lbl] = (slope, true_d)
        ax.text(2.62, y, f"spacing {slope:.3f} m\n(true distance {true_d:.3f} m)",
                va="center", color=INK2, fontsize=8)
    elif lbl in ("cardboard", "felt", "empty"):
        ax.text(2.62, y, "no ghosts", va="center", color=MUTED, fontsize=8)
ax.set_yticks(range(len(ladder)))
ax.set_yticklabels([NAME[l] for l in reversed(ladder)])
ax.set_xlim(0.2, 2.6)
ax.set_ylim(-0.6, len(ladder) - 0.2)
ax.grid(axis="y", visible=False)
ax.set_xlabel("Range of detected point (m)")
ax.set_title("Multipath ghost ladder from strong reflectors", pad=22)
subtitle(ax, "Gray ticks = predicted k-th bounce at k × (true distance) + range offset; "
             "dot area = share of frames detected there")
footnote(fig, f"Range offset {bias * 100:.1f} cm = radar's desk reading minus tape-measured {true_desk:.2f} m. "
              "Spacing = least-squares slope of ghost range vs bounce number k (detections in ≥5% of frames).")
fig.savefig(os.path.join(OUT, "fig7_multipath_ghost_ladder.png"))
plt.close(fig)
for lbl, (slope, true_d) in fit_text.items():
    print(f"ghost spacing {lbl}: {slope:.4f} m vs true {true_d:.4f} m")

print("Wrote:", *sorted(os.listdir(OUT)), sep="\n  ")
