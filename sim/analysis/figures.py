"""Figure and table generation for the merged paper (PKG3, FIGURE_SPEC_MERGED v1.0 + PFC-3).

Every figure derives from MANIFESTS via the released analysis code -- never from
readout markdown -- with two spec'd exceptions carried as data blocks below:
F4's horizon-deficit series (the FIGURE_SPEC names the H3_ZERO_CHECK horizon
table as F4's data) and T4's sensitivity rows (from the harness check document).
Each figure asserts agreement with the frozen-paper numbers; an assertion
failure is a STOP-and-escalate, never a silent fix.

Assertions (FIGURE_SPEC CODE INSTRUCTION + F2 PFC-2 + F4 PFC-3):
  F1: computed per-cell median improvement == -10.3 percent.
  F2: panel (b) finds 77 at-or-above-nominal cells in [93,100], three in
      [88,90], and exactly one cell with zero granted reservations.
  F3: computed H3 rates match the frozen T3 table exactly.
  F4: population median lifetime in [690, 730] and completable-at-200s in
      [23, 26] (workload/lifetimes.py, n=2000, fixed seed); deficit points
      match the zero-check table; discovery-realization points (863s, 21.5
      percent) overlaid as open markers.
  F5: computed AUCs match the frozen values (0.626 vs 0.648, -3.4 percent).

VISUAL QC GATE (operator ruling, permanent): constrained_layout on; legends
outside dense axes; jittered clouds at alpha 0.6; grouped sparse rotated tick
labels on per-cell axes; every annotation explicitly offset and collision-
checked by 2x-PNG inspection before acceptance.

Captions are FINAL PROSE from the spec and live in the LaTeX sources; this
module renders images only. Style: colorblind-safe (Okabe-Ito), grayscale-safe
(markers + dash patterns), Type-42 fonts, no in-image titles, no chartjunk.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams.update({
    "pdf.fonttype": 42, "ps.fonttype": 42,
    "font.size": 8, "axes.labelsize": 8, "legend.fontsize": 6.5,
    "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.spines.top": False, "axes.spines.right": False,
})
import matplotlib.pyplot as plt  # noqa: E402

from .h1_readout import h1_verdict, h2_auc, h3_deltas  # noqa: E402

SIM_ROOT = Path(__file__).resolve().parent.parent
RESULTS = SIM_ROOT / "experiments" / "results"
OUT_FIG = SIM_ROOT / "analysis" / "figures"
OUT_TAB = SIM_ROOT / "analysis" / "tables"

SINGLE_W, DOUBLE_W = 3.4, 7.0

# Okabe-Ito, grayscale-distinguishable ordering; one fixed slot per policy.
POLICY_STYLE = {
    "reservation":    dict(color="#000000", marker="o", ls="-",  lw=1.8, label="reservation"),
    "niyama_style":   dict(color="#E69F00", marker="s", ls="--", lw=1.0, label="niyama-style"),
    "vllm_style":     dict(color="#56B4E9", marker="^", ls="-.", lw=1.0, label="vllm-style"),
    "fastserve_mlfq": dict(color="#009E73", marker="D", ls=":",  lw=1.0, label="fastserve-mlfq"),
    "fcfs":           dict(color="#0072B2", marker="v", ls="--", lw=1.0, label="fcfs"),
    "mars_style":     dict(color="#D55E00", marker="P", ls="-.", lw=1.0, label="mars-style"),
    "concur_style":   dict(color="#CC79A7", marker="X", ls=":",  lw=1.0, label="concur-style"),
}
MIX_COLOR = {0.1: "#0072B2", 0.3: "#E69F00", 0.5: "#009E73"}
GAP_MARKER = {20.0: "o", 60.0: "s", 300.0: "^"}

# The frozen T3 table (H1_READOUT_FINAL H3 section), the F3 assertion target.
FROZEN_T3 = {
    "reservation":    ([0.503, 0.475, 0.499, 0.471], "-3.1"),
    "fcfs":           ([0.332, 0.295, 0.306, 0.282], "-5.0"),
    "mars_style":     ([0.249, 0.223, 0.245, 0.227], "-2.6"),
    "fastserve_mlfq": ([0.102, 0.100, 0.114, 0.116], "-0.3"),
    "concur_style":   ([0.082, 0.082, 0.070, 0.063], "-1.9"),
    "vllm_style":     ([0.000, 0.000, 0.000, 0.000], "+0.0"),
    "niyama_style":   ([0.000, 0.000, 0.000, 0.000], "+0.0"),
}

# The frozen F5 assertion targets (H1_READOUT_FINAL H2 section).
FROZEN_AUC = {"niyama_style": 0.648, "fastserve_mlfq": 0.639, "vllm_style": 0.633,
              "reservation": 0.626, "fcfs": 0.519, "mars_style": 0.411,
              "concur_style": 0.227}

# F4 deficit series: the H3_ZERO_CHECK horizon table (the FIGURE_SPEC's named
# data source; the 2026-07-15 diagnostic manifests were not retained).
ZERO_CHECK_HORIZONS = [300, 1500, 3000, 6000]
ZERO_CHECK_DEFICIT = [-22.3, -7.8, -8.8, -8.8]
# Discovery-realization points (PFC-3): overlaid as open markers, never as the
# population curve.
DISCOVERY_MEDIAN_S = 863.0
DISCOVERY_FRAC200 = 21.5


def _load(pattern: str) -> list[dict]:
    out = []
    for f in sorted((RESULTS).glob(pattern)):
        d = json.loads(f.read_text())
        if "spec" in d:
            out.append(d)
    return out


def _loadof(key: str) -> float:
    return float(key.split("_load")[1].split("_")[0])


def _mixof(key: str) -> float:
    return float(key.split("mix")[1].split("_")[0])


def _gapof(key: str) -> float:
    return float(key.split("_gap")[1].split("_")[0])


def _core_cells():
    v = h1_verdict(_load("*.json"))
    return [c for c in v.cells if not c.excluded], v


def _save(fig, name: str) -> None:
    OUT_FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_FIG / f"{name}.pdf")
    fig.savefig(OUT_FIG / f"{name}.png", dpi=220)
    plt.close(fig)
    print(f"[figures] wrote {name}.pdf/.png")


def _fail(msg: str) -> None:
    print(f"[figures] ASSERTION FAIL - STOP AND ESCALATE: {msg}")
    sys.exit(2)


# --- F1: improvement vs offered load ------------------------------------------

def fig_f1() -> None:
    core, v = _core_cells()
    med = round(v.median_improvement * 100, 1)
    if med != -10.3:
        _fail(f"F1 median improvement {med} != -10.3")

    import random
    rng = random.Random(20260721)
    fig, ax = plt.subplots(figsize=(DOUBLE_W, 3.1), layout="constrained")
    ax.axhspan(-30, 5, color="0.94", zorder=0)
    ax.axhline(0, color="0.2", lw=0.8)
    for y, lab in ((5, "WEAK (+5%)"), (10, "SUPPORT (+10%)")):
        ax.axhline(y, color="0.4", lw=0.8, ls="--")
        ax.annotate(lab, xy=(0.652, y), xytext=(0, 2),
                    textcoords="offset points", fontsize=6, color="0.25")
    for c in core:
        x = _loadof(c.cell_key) + rng.uniform(-0.022, 0.022)
        y = c.improvement * 100
        kw = dict(s=13, lw=0.7, zorder=3, alpha=0.6,
                  marker=GAP_MARKER[_gapof(c.cell_key)])
        if c.guardrail_ok:
            ax.scatter(x, y, color=MIX_COLOR[_mixof(c.cell_key)],
                       edgecolor="white", **kw)
        else:
            ax.scatter(x, y, facecolor="none",
                       edgecolor=MIX_COLOR[_mixof(c.cell_key)], **kw)
    loads = sorted({_loadof(c.cell_key) for c in core})
    med_line = [statistics.median([c.improvement * 100 for c in core
                                   if _loadof(c.cell_key) == L]) for L in loads]
    ax.plot(loads, med_line, color="black", lw=2.2, marker="o", ms=4, zorder=4)
    mix_h = [plt.Line2D([], [], color=col, marker="o", ls="", ms=4,
                        label=f"mix {100 - int(m * 100)}/{int(m * 100)} (interactive/agentic)")
             for m, col in MIX_COLOR.items()]
    gap_h = [plt.Line2D([], [], color="0.35", marker=mk, ls="", ms=4,
                        label=f"gap median {int(g)}s") for g, mk in GAP_MARKER.items()]
    other_h = [plt.Line2D([], [], color="0.35", marker="o", ls="", ms=4,
                          mfc="none", label="guardrail FAIL"),
               plt.Line2D([], [], color="black", lw=2.2, label="per-load median")]
    ax.legend(handles=mix_h + gap_h + other_h, ncol=4,
              loc="lower center", bbox_to_anchor=(0.5, 1.01), frameon=False)
    ax.set_xlabel("offered load (x measured capacity)")
    ax.set_ylabel("improvement vs best baseline (%)")
    ax.set_xticks(loads)
    ax.set_xlim(0.62, 1.58)
    ax.set_ylim(-27, 13)
    _save(fig, "F1_improvement_vs_load")
    print(f"[figures] F1 ASSERT PASS: median == {med}")


# --- F2: isolation and stranding ----------------------------------------------

def fig_f2() -> None:
    core, _ = _core_cells()
    manifests = _load("*.json")
    res_by_cell: dict[str, dict[str, list]] = {}
    for m in manifests:
        if m.get("collapsed") or m["spec"]["policy"] != "reservation":
            continue
        d = res_by_cell.setdefault(m["cell_key"], {"idle": [], "resv": []})
        d["idle"].append(m["metrics"]["reserved_idle_fraction"] * 100)
        d["resv"].append(m["metrics"]["reservations"])

    order = sorted(core, key=lambda c: (_loadof(c.cell_key), _mixof(c.cell_key),
                                        c.cell_key))
    hi = [c for c in order if _loadof(c.cell_key) >= 1.0]

    in_band = band_low = zero_resv = 0
    for c in hi:
        idle = statistics.median(res_by_cell[c.cell_key]["idle"])
        resv = statistics.median(res_by_cell[c.cell_key]["resv"])
        if resv == 0:
            zero_resv += 1
        elif 93.0 <= idle <= 100.0:
            in_band += 1
        elif 88.0 <= idle < 90.0:
            band_low += 1
    if (in_band, band_low, zero_resv) != (77, 3, 1):
        _fail(f"F2 partition (in [93,100], in [88,90], zero-reservation) = "
              f"({in_band}, {band_low}, {zero_resv}) != (77, 3, 1)")

    # Grouped x positions: 12 groups (load x mix), gap between groups.
    xs, group_ticks, group_labels = [], [], []
    x = 0.0
    prev = None
    for c in order:
        g = (_loadof(c.cell_key), _mixof(c.cell_key))
        if prev is not None and g != prev:
            x += 2.5 if g[0] != prev[0] else 1.2
        xs.append(x)
        x += 1.0
        prev = g
    for g in sorted({( _loadof(c.cell_key), _mixof(c.cell_key)) for c in order}):
        pos = [xi for xi, c in zip(xs, order)
               if (_loadof(c.cell_key), _mixof(c.cell_key)) == g]
        group_ticks.append(sum(pos) / len(pos))
        group_labels.append(f"{g[0]:g}x / {int(g[1] * 100)}%")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(DOUBLE_W, 4.4), sharex=True,
                                   layout="constrained")
    ax1.axhline(0, color="0.2", lw=0.8)
    ax1.axhline(5, color="0.4", lw=0.8, ls="--")
    ax1.annotate("guardrail (+5%)", xy=(xs[-1], 5), xytext=(-2, 4),
                 textcoords="offset points", fontsize=6, color="0.25",
                 ha="right")
    corner_pts = []
    for xi, c in zip(xs, order):
        y = c.ttft_degradation * 100
        if not c.guardrail_ok:
            ax1.scatter(xi, y, marker="^", s=22, facecolor="none",
                        edgecolor="#D55E00", lw=1.0, zorder=4)
            if y > 100:
                corner_pts.append((xi, y))
        else:
            ax1.scatter(xi, y, marker="o", s=9, color="#0072B2",
                        edgecolor="white", lw=0.4, zorder=3, alpha=0.75)
    ax1.set_yscale("symlog", linthresh=10)
    ax1.set_yticks([-100, -10, 0, 10, 100, 500])
    ax1.set_yticklabels(["-100", "-10", "0", "+10", "+100", "+500"])
    ax1.set_ylabel("p95 TTFT change\nvs best baseline (%)")
    if corner_pts:
        cx = statistics.mean([p[0] for p in corner_pts])
        ax1.annotate("inversion corner\n(+470 to +530%)", xy=(cx, 500),
                     xytext=(-58, -4), textcoords="offset points", fontsize=6,
                     va="top",
                     arrowprops=dict(arrowstyle="-", lw=0.6, color="0.3"))

    ax2.axhspan(93, 100, color="0.94", zorder=0)
    for xi, c in zip(xs, order):
        idle = statistics.median(res_by_cell[c.cell_key]["idle"])
        resv = statistics.median(res_by_cell[c.cell_key]["resv"])
        if resv == 0:
            ax2.scatter(xi, idle, marker="s", s=24, facecolor="none",
                        edgecolor="#D55E00", lw=1.0, zorder=4)
            ax2.annotate("no reservations granted", xy=(xi, idle),
                         xytext=(-70, 16), textcoords="offset points",
                         fontsize=6,
                         arrowprops=dict(arrowstyle="-", lw=0.6, color="0.3"))
        elif idle < 93 and _loadof(c.cell_key) >= 1.0:
            ax2.scatter(xi, idle, marker="o", s=20, facecolor="none",
                        edgecolor="#D55E00", lw=1.0, zorder=4)
        else:
            ax2.scatter(xi, idle, marker="o", s=9, color="#009E73",
                        edgecolor="white", lw=0.4, zorder=3, alpha=0.75)
    ax2.annotate("93-100 band", xy=(xs[1], 93), xytext=(-4, -14),
                 textcoords="offset points", fontsize=6, color="0.3",
                 arrowprops=dict(arrowstyle="-", lw=0.5, color="0.5"))
    ax2.set_ylabel("reserved-idle\nfraction (%)")
    ax2.set_ylim(-5, 106)
    ax2.set_xticks(group_ticks)
    ax2.set_xticklabels(group_labels, rotation=45, ha="right", fontsize=6)
    ax2.set_xlabel("cell groups: offered load / agentic mix "
                   "(9 cells per group: 3 gaps x 3 cluster sizes)")
    _save(fig, "F2_isolation_and_stranding")
    print("[figures] F2 ASSERT PASS: partition == (77, 3, 1)")


# --- F3: containment and starvation -------------------------------------------

def fig_f3() -> None:
    d3 = h3_deltas(_load("h3/*.json"))
    for pol, (rates, worst) in FROZEN_T3.items():
        got = [round(r, 3) for r in d3[pol]["curve"].values()]
        if got != rates:
            _fail(f"F3 {pol} rates {got} != frozen {rates}")
        gw = f"{d3[pol]['worst_delta'] * 100:+.1f}"
        if gw != worst and gw.replace("+", "-") != worst:
            _fail(f"F3 {pol} worst delta {gw} != frozen {worst}")

    fig, ax = plt.subplots(figsize=(SINGLE_W, 3.0), layout="constrained")
    inj = [0, 1, 5, 10]
    for pol, st in POLICY_STYLE.items():
        rates = list(d3[pol]["curve"].values())
        z = 5 if pol == "reservation" else 3
        ax.plot(inj, rates, color=st["color"], marker=st["marker"],
                ls=st["ls"], lw=st["lw"], ms=3.5, zorder=z, label=st["label"])
    ax.annotate("vllm-style, niyama-style:\n0.000 at every level",
                xy=(7.5, 0.004), xytext=(4.0, 0.155), textcoords="data",
                fontsize=6, ha="center", va="bottom",
                arrowprops=dict(arrowstyle="-", lw=0.6, color="0.3"))
    ax.set_xlabel("runaway injection (% of agentic arrivals)")
    ax.set_ylabel("compliant-flow completion rate")
    ax.set_xticks(inj)
    ax.set_ylim(-0.02, 0.56)
    ax.legend(frameon=False, ncol=2, loc="lower center",
              bbox_to_anchor=(0.5, 1.01))
    _save(fig, "F3_containment_starvation")
    print("[figures] F3 ASSERT PASS: rates match the frozen T3 table")


# --- F4: the horizon lesson (PFC-3 dual-report) -------------------------------

def fig_f4() -> None:
    from ..core.orchestration import CONFIG_DIR, load_service_model, load_yaml
    from ..workload.lifetimes import agentic_flow_min_lifetimes

    service = load_service_model()
    workload = load_yaml(CONFIG_DIR / "workload.yaml")
    lts = sorted(agentic_flow_min_lifetimes(workload, service, n_flows=2000,
                                            seed=1))
    median_lt = statistics.median(lts)
    frac200 = 100.0 * sum(1 for t in lts if t <= 200.0) / len(lts)
    if not (690.0 <= median_lt <= 730.0):
        _fail(f"F4 population median lifetime {median_lt:.0f}s not in [690, 730]")
    if not (23.0 <= frac200 <= 26.0):
        _fail(f"F4 completable fraction at 200s {frac200:.1f}% not in [23, 26]")

    fig, ax = plt.subplots(figsize=(SINGLE_W, 3.0), layout="constrained")
    ax.plot(ZERO_CHECK_HORIZONS, ZERO_CHECK_DEFICIT, color="black",
            marker="o", ms=4, lw=1.6, label="deficit vs best baseline")
    ax.axhline(0, color="0.2", lw=0.6)
    offsets = {300: (10, -3), 1500: (0, 7), 3000: (0, -11), 6000: (0, 7)}
    for h, dv in zip(ZERO_CHECK_HORIZONS, ZERO_CHECK_DEFICIT):
        ax.annotate(f"{dv:+.1f}%", xy=(h, dv), xytext=offsets[h],
                    textcoords="offset points", fontsize=6, ha="center")
    ax.set_xlabel("measurement horizon (s)")
    ax.set_ylabel("reservation vs best baseline (%)")
    ax.set_xscale("log")
    ax.set_xticks(ZERO_CHECK_HORIZONS)
    ax.set_xticklabels([str(h) for h in ZERO_CHECK_HORIZONS])
    ax.set_ylim(-26, 3)

    ax2 = ax.twinx()
    ax2.spines.right.set_visible(True)
    hs = [50 * i for i in range(1, 121)]
    frac = [100.0 * sum(1 for t in lts if t <= h) / len(lts) for h in hs]
    ax2.plot(hs, frac, color="#0072B2", lw=1.0, ls="--",
             label="completable fraction (population)")
    ax2.scatter([200], [frac200], color="#0072B2", s=14, zorder=4)
    ax2.annotate(f"200s: {frac200:.1f}% (pop.)", xy=(200, frac200),
                 xytext=(62, 36), textcoords="data", fontsize=6,
                 color="#0072B2", ha="left",
                 arrowprops=dict(arrowstyle="-", lw=0.5, color="#0072B2"))
    # Discovery-realization points (PFC-3): open markers.
    ax2.scatter([200], [DISCOVERY_FRAC200], marker="o", s=22, facecolor="none",
                edgecolor="#0072B2", lw=1.0, zorder=4)
    ax2.annotate(f"{DISCOVERY_FRAC200:.1f}% (discovery)",
                 xy=(200, DISCOVERY_FRAC200), xytext=(62, 6),
                 textcoords="data", fontsize=6, color="#0072B2", ha="left",
                 arrowprops=dict(arrowstyle="-", lw=0.5, color="#0072B2"))
    ax2.axvline(median_lt, color="0.5", lw=0.7, ls=":")
    ax2.axvline(DISCOVERY_MEDIAN_S, color="0.6", lw=0.7, ls=(0, (1, 3)))
    ax2.annotate(f"median lifetime:\n{median_lt:.0f}s (pop.), "
                 f"{DISCOVERY_MEDIAN_S:.0f}s (disc.)",
                 xy=(DISCOVERY_MEDIAN_S, 96), xytext=(230, 90),
                 textcoords="data", fontsize=6, color="0.35", ha="left",
                 va="top",
                 arrowprops=dict(arrowstyle="-", lw=0.5, color="0.5"))
    ax2.set_ylabel("agentic flows completable\non an idle cluster (%)",
                   color="#0072B2")
    ax2.set_ylim(0, 104)
    h1_, l1_ = ax.get_legend_handles_labels()
    h2_, l2_ = ax2.get_legend_handles_labels()
    ax.legend(h1_ + h2_, l1_ + l2_, frameon=False, loc="lower center",
              bbox_to_anchor=(0.5, 1.01), fontsize=6)
    _save(fig, "F4_horizon_lesson")
    print(f"[figures] F4 ASSERT PASS: population median {median_lt:.0f}s in "
          f"[690, 730]; completable at 200s {frac200:.1f}% in [23, 26]; "
          f"deficit series = zero-check table; discovery points overlaid")


# --- F5: H2 served value ------------------------------------------------------

def fig_f5() -> None:
    a = h2_auc(_load("h2/*.json"))
    for pol, target in FROZEN_AUC.items():
        got = round(a["auc"][pol], 3)
        if got != target:
            _fail(f"F5 AUC {pol} {got} != frozen {target}")
    rel = f"{a['relative'] * 100:+.1f}"
    if rel != "-3.4":
        _fail(f"F5 relative {rel} != -3.4")

    manifests = _load("h2/*.json")
    by: dict[str, dict[float, list]] = {}
    for m in manifests:
        by.setdefault(m["spec"]["policy"], {}).setdefault(
            float(m["spec"]["load"]), []).append(
            m["metrics"]["served_value_fraction"])
    fig, ax = plt.subplots(figsize=(SINGLE_W, 3.2), layout="constrained")
    for pol, st in POLICY_STYLE.items():
        loads = sorted(by[pol])
        ys = [statistics.median(by[pol][L]) for L in loads]
        z = 5 if pol == "reservation" else 3
        ax.plot(loads, ys, color=st["color"], marker=st["marker"], ls=st["ls"],
                lw=st["lw"], ms=3.5, zorder=z,
                label=f"{st['label']} (AUC {a['auc'][pol]:.3f})")
    ax.set_xlabel("offered load (x measured capacity)")
    ax.set_ylabel("served-value fraction")
    ax.set_xticks([0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
    ax.set_ylim(0, 1.02)
    ax.legend(frameon=False, fontsize=6, ncol=2, loc="lower center",
              bbox_to_anchor=(0.5, 1.01))
    _save(fig, "F5_served_value")
    print("[figures] F5 ASSERT PASS: AUCs match frozen values, relative -3.4%")


# --- tables -------------------------------------------------------------------

def _esc(s: str) -> str:
    return s.replace("_", r"\_").replace("%", r"\%")


def tables_all() -> None:
    OUT_TAB.mkdir(parents=True, exist_ok=True)
    core, v = _core_cells()

    # T2 - verdict summary (computed).
    med = v.median_improvement * 100
    frac = v.support_fraction * 100
    guard = v.guardrail_intact_fraction * 100
    t2 = [
        r"\begin{tabular}{llll}", r"\toprule",
        r"threshold & required & measured & outcome \\", r"\midrule",
        rf"median improvement & $\geq$ +10\% (support) / $\geq$ +5\% (weak) & {med:+.1f}\% & KILL \\",
        rf"supporting cells & $\geq$ 60\% of core cells & {v.support_cells} of {v.core_cells} ({frac:.0f}\%) & KILL \\",
        rf"interactive guardrail & p95 TTFT degradation $\leq$ 5\% & intact in {guard:.0f}\% of cells & holds (4 cells fail) \\",
        r"\bottomrule", r"\end{tabular}",
    ]
    (OUT_TAB / "T2_verdict.tex").write_text("\n".join(t2) + "\n")

    # T3 - H3 absolute rates (computed, asserted against frozen in fig_f3).
    d3 = h3_deltas(_load("h3/*.json"))
    rows = sorted(d3.items(), key=lambda kv: -max(kv[1]["curve"].values()))
    t3 = [r"\begin{tabular}{lrrrrr}", r"\toprule",
          r"policy & inj 0\% & inj 1\% & inj 5\% & inj 10\% & worst delta \\",
          r"\midrule"]
    for pol, r in rows:
        c = list(r["curve"].values())
        t3.append(rf"{_esc(pol)} & {c[0]:.3f} & {c[1]:.3f} & {c[2]:.3f} & "
                  rf"{c[3]:.3f} & {r['worst_delta'] * 100:+.1f} \\")
    t3 += [r"\bottomrule", r"\end{tabular}"]
    (OUT_TAB / "T3_h3_rates.tex").write_text("\n".join(t3) + "\n")

    # T1 - policy roster with tuned values (tuned_params.json: policy -> family).
    tuned = json.loads((SIM_ROOT / "configs" / "tuned_params.json").read_text())
    t1 = [r"\begin{tabular}{llp{3.1in}}", r"\toprule",
          r"policy & workload family & tuned parameters \\", r"\midrule"]
    for pol in sorted(tuned):
        fams = tuned[pol]
        if not isinstance(fams, dict) or not fams:
            t1.append(rf"{_esc(pol)} & all & (no parameters) \\")
            continue
        for fam in sorted(fams):
            params = ", ".join(f"{k}={v}" for k, v in sorted(fams[fam].items())) \
                if fams[fam] else "(no parameters)"
            t1.append(rf"{_esc(pol)} & {_esc(fam)} & "
                      rf"\texttt{{{_esc(params)}}} \\")
    t1 += [r"\bottomrule", r"\end{tabular}"]
    (OUT_TAB / "T1_roster.tex").write_text("\n".join(t1) + "\n")

    # T4 - sensitivity (SENSITIVITY_HARNESS_CHECK, values audited by PKG1).
    t4 = [
        r"\begin{tabular}{llll}", r"\toprule",
        r"constant & perturbation & representative-cell delta & binding \\",
        r"\midrule",
        r"single-stream decode rate & 0.7x / 1.3x & deficit 21.5\% $\to$ 4.4\% / 13.0\% & binding \\",
        r"peak decode rate & $\pm$30\% & none (0 of 19{,}342 decode calls bound) & non-binding \\",
        r"cache bytes per token & $\pm$30\% & none (peak HBM near 39\% of capacity) & non-binding \\",
        r"\bottomrule", r"\end{tabular}",
    ]
    (OUT_TAB / "T4_sensitivity.tex").write_text("\n".join(t4) + "\n")

    # Appendix D - full 108-cell H1 table (machine-generated).
    d = [r"\begin{longtable}{llrrl}", r"\toprule",
         r"cell & best baseline & improvement & 95\% CI & guardrail \\",
         r"\midrule", r"\endhead"]
    for c in sorted(core, key=lambda c: c.cell_key):
        cell = _esc(c.cell_key.replace("_slots16", "").replace("_rw0.0", ""))
        gr = "ok" if c.guardrail_ok else rf"FAIL ({c.ttft_degradation * 100:+.1f}\%)"
        d.append(rf"\texttt{{{cell}}} & {_esc(c.best_baseline)} & "
                 rf"{c.improvement * 100:+.1f}\% & "
                 rf"[{c.ci_lo * 100:+.1f}\%, {c.ci_hi * 100:+.1f}\%] & {gr} \\")
    d += [r"\bottomrule", r"\end{longtable}"]
    (OUT_TAB / "appendix_D_cells.tex").write_text("\n".join(d) + "\n")

    print(f"[figures] wrote tables T1-T4 + Appendix D to {OUT_TAB}")


FIGS = {"F1": fig_f1, "F2": fig_f2, "F3": fig_f3, "F4": fig_f4, "F5": fig_f5}


def main(argv: list[str]) -> None:
    targets = argv or ["all"]
    if targets == ["all"]:
        targets = ["F1", "F2", "F3", "F4", "F5", "tables"]
    for t in targets:
        if t == "tables":
            tables_all()
        elif t in FIGS:
            FIGS[t]()
        else:
            print(f"unknown target {t}; use F1..F5 or tables")
            sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
