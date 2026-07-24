"""SG2 validation report generator (brief Section 6).

Samples the Section 5 generators, checks empirical distributions against the spec
parameters, demonstrates the runaway family, verifies seed reproducibility, writes
distribution plots and a markdown report to docs/validation/.

Run:  make validate   (from code/sim)
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: no display, write PNGs only
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from ..core.orchestration import CONFIG_DIR, load_yaml  # noqa: E402
from .flow_structure import ClassSpec, RunawaySpec  # noqa: E402
from .generator import WorkloadGenerator  # noqa: E402
from . import validation as V  # noqa: E402

# Sample sizes / horizons for the report (validation only, not the grid).
N_STRUCT = 20_000          # directly-sampled flows for structural distributions
REPORT_HORIZON_S = 86_400  # one day, to exercise diurnal + bursts
REPORT_SEED = 20_260_713
RUNAWAY_DEMO_FRACTION = 0.10

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "validation"


def _hist_with_targets(ax, data, target, title, xlabel, logx=False, bins=60):
    if logx and data.size > 0:
        positive = data[data > 0]
        bins = np.logspace(np.log10(max(positive.min(), 1e-6)),
                           np.log10(positive.max()), bins)
        ax.set_xscale("log")
    ax.hist(data, bins=bins, color="#4C78A8", alpha=0.85, edgecolor="none")
    if target:
        ax.axvline(target["median"], color="#E45756", linestyle="--",
                   label=f"target median {target['median']:g}")
        ax.axvline(target["p95"], color="#F58518", linestyle=":",
                   label=f"target p95 {target['p95']:g}")
    ax.axvline(float(np.percentile(data, 50)), color="#54A24B", linestyle="-",
               linewidth=1, label="empirical median")
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("count")
    ax.legend(fontsize=7)


def _fmt(stats: V.DistStats, target: dict) -> str:
    return (f"{stats.median:.1f} / {stats.p95:.1f}  "
            f"(target {target['median']:g} / {target['p95']:g}, n={stats.n})")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_yaml(CONFIG_DIR / "workload.yaml")
    agentic = ClassSpec.from_config("agentic", cfg["classes"]["agentic"])
    interactive = ClassSpec.from_config("interactive", cfg["classes"]["interactive"])
    runaway_spec = RunawaySpec.from_config(cfg["adversarial"]["runaway"])

    # --- 1. structural distributions (direct sampling) --------------------------
    ag_flows = V.sample_class_flows(agentic, REPORT_SEED, N_STRUCT)
    inv_counts = V.invocation_counts(ag_flows)
    gaps = V.all_gaps(ag_flows)
    out_tokens = V.all_output_tokens(ag_flows)

    inv_stats = V.DistStats.of(inv_counts)
    gap_stats = V.DistStats.of(gaps)
    tok_stats = V.DistStats.of(out_tokens)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    _hist_with_targets(axes[0], inv_counts, V.target_of(agentic.invocations_per_flow),
                       "Agentic invocations per flow", "invocations", logx=False)
    _hist_with_targets(axes[1], gaps, V.target_of(agentic.gap_s),
                       "Agentic inter-invocation gap", "seconds", logx=True)
    _hist_with_targets(axes[2], out_tokens, V.target_of(agentic.output_tokens),
                       "Agentic output tokens", "tokens", logx=True)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "flow_structure.png", dpi=90)
    plt.close(fig)

    # --- 2. mix + arrivals (full generator run) ---------------------------------
    gen = WorkloadGenerator(cfg, REPORT_SEED, REPORT_HORIZON_S)
    run_flows = V.materialize(gen)
    mix = V.mix_token_share(run_flows)
    centers, counts = V.arrival_counts_per_bin(run_flows, REPORT_HORIZON_S)
    mean_rate = len(run_flows) / REPORT_HORIZON_S

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.bar(centers / 3600.0, counts, width=(REPORT_HORIZON_S / len(centers)) / 3600.0,
           color="#4C78A8", alpha=0.85)
    ax.set_title("Arrivals over one day (diurnal modulation + burst episodes)")
    ax.set_xlabel("hour")
    ax.set_ylabel("arrivals per 15 min")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "arrivals.png", dpi=90)
    plt.close(fig)

    # --- 3. adversarial runaway demonstration -----------------------------------
    normal_ag = V.sample_class_flows(agentic, REPORT_SEED + 1, 5000)
    runaway_ag = V.sample_class_flows(agentic, REPORT_SEED + 1, 5000,
                                      runaway=True, runaway_spec=runaway_spec)
    normal_counts = V.invocation_counts(normal_ag)
    runaway_counts = V.invocation_counts(runaway_ag)
    normal_tokens = np.array([V.total_tokens([f]) for f in normal_ag])
    runaway_tokens = np.array([V.total_tokens([f]) for f in runaway_ag])

    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    axes[0].hist(normal_counts, bins=50, color="#4C78A8", alpha=0.7, label="normal")
    axes[0].hist(runaway_counts, bins=50, color="#E45756", alpha=0.7, label="runaway")
    axes[0].set_title("Invocations per flow: normal vs runaway")
    axes[0].set_xlabel("invocations")
    axes[0].set_ylabel("count")
    axes[0].legend(fontsize=8)
    axes[1].hist(np.log10(normal_tokens), bins=50, color="#4C78A8", alpha=0.7,
                 label="normal")
    axes[1].hist(np.log10(runaway_tokens), bins=50, color="#E45756", alpha=0.7,
                 label="runaway")
    axes[1].set_title("Total tokens per flow (log10): normal vs runaway")
    axes[1].set_xlabel("log10 tokens")
    axes[1].set_ylabel("count")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "adversarial.png", dpi=90)
    plt.close(fig)

    # Injection-fraction check: a small run with runaway_fraction set.
    adv_cfg = load_yaml(CONFIG_DIR / "workload.yaml")
    adv_cfg["adversarial"]["runaway_fraction_of_agentic"] = RUNAWAY_DEMO_FRACTION
    adv_flows = V.materialize(WorkloadGenerator(adv_cfg, REPORT_SEED, REPORT_HORIZON_S))
    agentic_flows = [f for f in adv_flows if f.cls == "agentic"]
    runaway_flows = [f for f in agentic_flows if f.is_runaway]
    injected = len(runaway_flows) / len(agentic_flows) if agentic_flows else 0.0

    # --- 4. reproducibility -----------------------------------------------------
    h1 = V.workload_hash(V.materialize(WorkloadGenerator(cfg, REPORT_SEED, 3600.0)))
    h2 = V.workload_hash(V.materialize(WorkloadGenerator(cfg, REPORT_SEED, 3600.0)))
    h_diff = V.workload_hash(
        V.materialize(WorkloadGenerator(cfg, REPORT_SEED + 999, 3600.0)))

    # --- write report -----------------------------------------------------------
    _write_report(OUT_DIR / "VALIDATION_REPORT.md", cfg, agentic, interactive,
                  inv_stats, gap_stats, tok_stats, mix, mean_rate, counts,
                  normal_counts, runaway_counts, normal_tokens, runaway_tokens,
                  injected, h1, h2, h_diff)

    # --- console summary --------------------------------------------------------
    print("=== SG2 workload validation ===")
    print(f"  agentic invocations/flow : {_fmt(inv_stats, V.target_of(agentic.invocations_per_flow))}")
    print(f"  agentic gap (s)          : {_fmt(gap_stats, V.target_of(agentic.gap_s))}")
    print(f"  agentic output tokens    : {_fmt(tok_stats, V.target_of(agentic.output_tokens))}")
    print(f"  achieved agentic token share : {mix['agentic_token_share']:.3f} "
          f"(target {cfg['mix']['agentic_token_share']})")
    print(f"  mean arrival rate (1/s)  : {mean_rate:.3f} (base {cfg['arrivals']['base_rate_per_s']})")
    print(f"  runaway invocations median : {np.median(runaway_counts):.0f} "
          f"vs normal {np.median(normal_counts):.0f}")
    print(f"  runaway tokens median      : {np.median(runaway_tokens):.0f} "
          f"vs normal {np.median(normal_tokens):.0f}")
    print(f"  injected runaway fraction  : {injected:.3f} (target {RUNAWAY_DEMO_FRACTION})")
    print(f"  reproducibility: same-seed hashes match = {h1 == h2}; "
          f"different-seed differs = {h1 != h_diff}")
    print(f"  report + plots written to {OUT_DIR}")


def _write_report(path, cfg, agentic, interactive, inv_stats, gap_stats, tok_stats,
                  mix, mean_rate, arr_counts, normal_counts, runaway_counts,
                  normal_tokens, runaway_tokens, injected, h1, h2, h_diff) -> None:
    def row(name, stats, target):
        return (f"| {name} | {stats.median:.1f} | {target['median']:g} | "
                f"{stats.p95:.1f} | {target['p95']:g} | {stats.n} |")

    lines = [
        "# SG2 Workload Validation Report",
        "",
        "Auto-generated by `make validate` (sim/workload/validate.py). Validates the",
        "spec Section 5 generators. House style: hyphens only (D-026).",
        "",
        "## 1. Flow-structure distributions (agentic class, direct sampling)",
        "",
        f"Sampled {inv_stats.n} agentic flows. Empirical median/p95 vs the spec targets:",
        "",
        "| quantity | emp median | target median | emp p95 | target p95 | n |",
        "|----------|-----------:|--------------:|--------:|-----------:|--:|",
        row("invocations per flow", inv_stats, V.target_of(agentic.invocations_per_flow)),
        row("inter-invocation gap (s)", gap_stats, V.target_of(agentic.gap_s)),
        row("output tokens per step", tok_stats, V.target_of(agentic.output_tokens)),
        "",
        "![flow structure](flow_structure.png)",
        "",
        "## 2. Traffic mix (by token volume)",
        "",
        f"- Target agentic token share: **{cfg['mix']['agentic_token_share']}**",
        f"- Achieved agentic token share: **{mix['agentic_token_share']:.3f}**",
        f"- Flow counts: {mix['flow_counts']}  (agentic flows are rare because each",
        "  carries far more tokens than an interactive turn -- the mix is by token",
        "  volume, not by flow count).",
        "",
        "## 3. Arrival process (Poisson + diurnal + bursts)",
        "",
        f"- Mean arrival rate: **{mean_rate:.3f}/s** (config base {cfg['arrivals']['base_rate_per_s']}/s;",
        "  diurnal averages to ~1, bursts lift the mean slightly).",
        f"- Per-15-min arrival counts range {int(arr_counts.min())}-{int(arr_counts.max())}",
        "  across the day (diurnal swing + burst spikes visible below).",
        "",
        "![arrivals](arrivals.png)",
        "",
        "## 4. Adversarial family (demonstrably runaway)",
        "",
        f"- Invocations per flow: runaway median **{np.median(runaway_counts):.0f}** vs "
        f"normal **{np.median(normal_counts):.0f}** "
        f"({np.median(runaway_counts) / max(np.median(normal_counts), 1):.0f}x).",
        f"- Total tokens per flow: runaway median **{np.median(runaway_tokens):.0f}** vs "
        f"normal **{np.median(normal_tokens):.0f}** "
        f"({np.median(runaway_tokens) / max(np.median(normal_tokens), 1):.0f}x).",
        f"- Injection at target {injected:.3f} of agentic arrivals flagged runaway "
        "(config-driven).",
        "",
        "![adversarial](adversarial.png)",
        "",
        "## 5. Reproducibility",
        "",
        f"- Same seed, two runs -> identical hash: **{h1 == h2}**",
        f"  (`{h1[:16]}...`)",
        f"- Different seed -> different hash: **{h1 != h_diff}**",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
