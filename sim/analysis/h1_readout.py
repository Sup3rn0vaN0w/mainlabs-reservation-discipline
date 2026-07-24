"""The mechanical H1 readout (spec Section 3; SG7 deliverable).

Consumes the grid manifest set and emits the verdict table -- per-cell medians,
best-baseline-per-cell, bootstrap CIs, corner-cell exclusions applied, the H1
support/weak/kill classification, plus H2 AUC and H3 deltas. NUMBERS AND THE
THRESHOLD COMPARISON ONLY. No interpretation, no prose conclusions (brief Section 6).

The classification is mechanical and fixed here so the verdict is a pure function of
the manifests: run it twice on the same results, get the same verdict. Per D-058 the
filing then executes off the H1 verdict with no re-decision, so this file is
load-bearing and is guarded by tests/test_h1_verdict.py with hand-computable cases.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .stats import bootstrap_relative_improvement

# Spec Section 3 thresholds.
SUPPORT_IMPROVEMENT = 0.10       # median improvement to count a cell as supporting
WEAK_IMPROVEMENT = 0.05
SUPPORT_CELL_FRACTION = 0.60     # >= this fraction of core cells must support
GUARDRAIL_MAX_TTFT_DEGRADATION = 0.05
GUARDRAIL_REGION_FRACTION = 0.50  # guardrail "violated across region" if intact in < this

OURS = "reservation"
_BASELINES = ("fcfs", "vllm_style", "niyama_style", "concur_style",
              "fastserve_mlfq", "mars_style")


# --- corner-cell exclusions (spec Section 5) ------------------------------------

def corner_exclusion(mix: float, load: float, gap_median_s: float) -> str | None:
    """Return the exclusion reason if this cell is a pre-flagged corner, else None.

    Spec Section 5, excluded from support: gap median > 15 min (with strict pinning),
    agentic share > 70 percent, offered load < 0.6x. Applied at the workload level;
    the gap corner is applied regardless of pin embodiment (conservative).
    """
    if gap_median_s > 15 * 60:
        return "gap median > 15 min"
    if mix > 0.70:
        return "agentic share > 70 percent"
    if load < 0.6:
        return "offered load < 0.6x"
    return None


# --- per-cell and aggregate H1 verdict ------------------------------------------

@dataclass
class CellVerdict:
    cell_key: str
    mix: float
    load: float
    num_devices: int
    gap_median_s: float
    excluded: str | None
    best_baseline: str
    ours_goodput: float
    best_goodput: float
    improvement: float
    ci_lo: float
    ci_hi: float
    ttft_degradation: float
    guardrail_ok: bool
    supports: bool           # improvement >= 10% AND guardrail ok (core cells only)
    n_paired: int = 3        # seeds BOTH policies completed; < 3 means a collapse


@dataclass
class H1Verdict:
    verdict: str             # SUPPORT | WEAK | KILL
    reason: str
    core_cells: int
    support_cells: int
    support_fraction: float
    median_improvement: float
    guardrail_intact_fraction: float
    excluded_cells: int
    cells: list[CellVerdict] = field(default_factory=list)


def _median(xs: list[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def _workload_tuple(spec: dict) -> tuple:
    """The canonical workload identity of a cell (schema-independent)."""
    return (float(spec["mix"]), float(spec["load"]), int(spec["num_devices"]),
            float(spec["gap_median_s"]), float(spec.get("runaway_fraction", 0.0)))


def assert_manifests_match_grid(manifests: list[dict], config: str = "grid_main.yaml",
                                ) -> None:
    """Every manifest must describe a cell the grid config actually defines.

    The duplicate-cell_key guard below catches stale manifests that COLLIDE with a
    current cell. It does NOT catch manifests that simply are not in the grid at all
    -- those get distinct cell keys and aggregate in silently.

    That gap was real. `make pilot` writes to the SAME results/ directory as the main
    grid and fixes num_devices=4, so a pilot run left 189 manifests at horizon 300s
    and 4 devices sitting in the final grid's evidence directory. Without this check
    the FINAL readout would have computed a support fraction over cells at a censored
    horizon and a cluster size Amendment 3 excludes -- and then fired D-058 on it.

    A verdict is only meaningful over exactly the pre-registered grid. Anything else
    in the directory is contamination, and this refuses to proceed rather than
    quietly averaging it in.
    """
    from ..experiments.grid import load_grid_config

    cfg = load_grid_config(config)
    axes, cell_cfg = cfg["axes"], cfg["cell"]
    ok_devices = set(axes["num_devices"])
    ok_seeds = set(cfg["seeds"])
    horizon = float(cell_cfg["horizon_s"])

    offenders: dict[str, int] = {}
    for m in manifests:
        spec = m.get("spec", {})
        why = None
        if float(spec.get("horizon_s", -1)) != horizon:
            why = f"horizon {spec.get('horizon_s')} != {horizon}"
        elif int(spec.get("num_devices", -1)) not in ok_devices:
            why = f"num_devices {spec.get('num_devices')} not in {sorted(ok_devices)}"
        elif int(spec.get("seed", -1)) not in ok_seeds:
            why = f"seed {spec.get('seed')} not in {sorted(ok_seeds)}"
        if why:
            offenders[why] = offenders.get(why, 0) + 1

    if offenders:
        detail = "; ".join(f"{n} manifests with {why}" for why, n in
                           sorted(offenders.items(), key=lambda kv: -kv[1]))
        raise ValueError(
            f"results set does not match {config}: {detail}. These cells are not in "
            f"the pre-registered grid, so a verdict computed over them is not the "
            f"pre-registered verdict. Move them out of the results directory (do not "
            f"delete -- they are evidence of something) and regenerate.")


def _by_cell_policy(manifests: list[dict]) -> dict:
    """cell_key -> policy -> {metric -> [values across seeds]}, plus cell axes.

    Guards the filing-critical verdict against a polluted result set:
      * duplicate run_ids (the same run synced twice) are collapsed, and
      * two cell_keys describing the SAME workload (a stale-schema manifest left in
        results/ after a config change) is a hard error, not a silent double-count.

    See assert_manifests_match_grid for the complementary check: manifests that are
    not in the grid AT ALL, which this one cannot see.
    """
    seen_runs: set[str] = set()
    workload_to_key: dict[tuple, str] = {}
    cells: dict = {}
    for m in manifests:
        rid = m.get("run_id")
        if rid is not None:
            if rid in seen_runs:
                continue                    # idempotent re-sync of the same run
            seen_runs.add(rid)

        ck = m["cell_key"]
        wl = _workload_tuple(m["spec"])
        prior = workload_to_key.setdefault(wl, ck)
        if prior != ck:
            raise ValueError(
                f"two cell keys describe the same workload {wl}: {prior!r} and "
                f"{ck!r}. The results set mixes schemas -- clear stale manifests and "
                f"regenerate (a config/schema change needs a fresh results dir).")

        cell = cells.setdefault(ck, {"policies": {}, "spec": m["spec"],
                                     "collapsed": {}})
        pol_name = m["spec"]["policy"]

        # A collapsed run carries NO metrics by construction (operator ruling
        # 2026-07-21): numbers accumulated up to a wall-clock cut-off are the
        # right-censored quantity Amendment 2 exists to exclude. Count it as a
        # non-completion for that policy and move on -- never impute a value.
        if m.get("collapsed"):
            cell["collapsed"].setdefault(pol_name, []).append({
                "seed": m["spec"].get("seed"),
                "wall_cap_s": m.get("wall_cap_s"),
                "diagnostics": m.get("diagnostics", {})})
            continue

        pol = cell["policies"].setdefault(pol_name, {})
        for metric in ("goodput_per_gpu_hour", "p95_ttft_s"):
            pol.setdefault(metric, []).append(m["metrics"][metric])
        # Seed identity is needed to PAIR series when a policy is missing a seed
        # (a collapsed run). Without it the paired bootstrap can only compare
        # equal-length series, and silently mispairs if lengths coincide by luck.
        pol.setdefault("seeds", []).append(m["spec"]["seed"])
    return cells


def _paired_on_shared_seeds(a: dict, b: dict, metric: str = "goodput_per_gpu_hour",
                            ) -> tuple[list[float], list[float], list[int]]:
    """Align two policies' series on the seeds BOTH completed.

    Operator ruling 2026-07-21: complete-case pairing. Where a baseline collapsed on
    some seed, the comparison in that cell uses the seeds where both policies have a
    measurement. The paired bootstrap then rests on genuine pairs, and the CI widens
    on its own to reflect the smaller n -- which is the honest consequence of having
    less information, not something to paper over.

    Returns (a_values, b_values, shared_seeds), all ordered by seed.
    """
    a_by = dict(zip(a.get("seeds", []), a[metric]))
    b_by = dict(zip(b.get("seeds", []), b[metric]))
    shared = sorted(set(a_by) & set(b_by))
    return ([a_by[s] for s in shared], [b_by[s] for s in shared], shared)


def cell_verdict(cell_key: str, cell: dict) -> CellVerdict | None:
    """Compute the per-cell verdict, or None if the reservation policy is absent."""
    policies = cell["policies"]
    spec = cell["spec"]
    if OURS not in policies:
        return None

    ours = policies[OURS]["goodput_per_gpu_hour"]

    # Best baseline in THIS cell (strongest opponent per condition, spec Section 3).
    best_name, best_median, best_series = None, float("-inf"), []
    for b in _BASELINES:
        if b not in policies:
            continue
        series = policies[b]["goodput_per_gpu_hour"]
        if _median(series) > best_median:
            best_name, best_median, best_series = b, _median(series), series

    # Complete-case pairing (operator ruling 2026-07-21): a collapsed run leaves a
    # baseline short a seed, so pair on the seeds both policies actually completed.
    ours_paired, base_paired, shared = _paired_on_shared_seeds(
        policies[OURS], policies[best_name])
    if not shared:
        return None     # no seed where both ran; nothing honest to compare
    ci = bootstrap_relative_improvement(ours_paired, base_paired, seed=20260715)
    n_paired = len(shared)

    our_ttft = _median(policies[OURS]["p95_ttft_s"])
    base_ttft = _median(policies[best_name]["p95_ttft_s"])
    ttft_deg = (our_ttft - base_ttft) / base_ttft if base_ttft > 0 else 0.0
    guardrail_ok = ttft_deg <= GUARDRAIL_MAX_TTFT_DEGRADATION

    excluded = corner_exclusion(
        float(spec["mix"]), float(spec["load"]), float(spec["gap_median_s"]))
    supports = (excluded is None
                and ci.point >= SUPPORT_IMPROVEMENT and guardrail_ok)

    return CellVerdict(
        cell_key=cell_key, mix=float(spec["mix"]), load=float(spec["load"]),
        num_devices=int(spec["num_devices"]),
        gap_median_s=float(spec["gap_median_s"]), excluded=excluded,
        best_baseline=best_name, ours_goodput=_median(ours), best_goodput=best_median,
        improvement=ci.point, ci_lo=ci.lo, ci_hi=ci.hi,
        ttft_degradation=ttft_deg, guardrail_ok=guardrail_ok, supports=supports,
        n_paired=n_paired)


def collapse_report(manifests: list[dict]) -> list[dict]:
    """Every run that hit its wall-clock cap, and whether it biases the comparison.

    A collapse is a real result -- the policy failed to make progress -- but it is
    NOT a measured number, so it never enters the aggregate. What it CAN do is
    silently distort a cell: if a policy collapses on some seeds and completes on
    others, that cell records the policy only from the trajectories where it
    survived, flattering it. When the affected policy is the best baseline, that
    bias runs AGAINST the reservation discipline.

    Observed 2026-07-21: niyama_style collapsed on seeds 1 and 3 of
    (mix 0.5, load 0.7, dev 32, gap 20) and completed on seed 2, so only the healthy
    trajectory is in the results. This function makes that visible in the readout
    rather than leaving it to be discovered later.
    """
    cells = _by_cell_policy(manifests)
    rows = []
    for ck, cell in sorted(cells.items()):
        for policy, events in sorted(cell.get("collapsed", {}).items()):
            surviving = len(cell["policies"].get(policy, {})
                            .get("goodput_per_gpu_hour", []))
            rows.append({
                "cell": ck, "policy": policy,
                "collapsed_seeds": sorted(e["seed"] for e in events),
                "surviving_seeds": surviving,
                "partial": surviving > 0,
                "diagnostics": events[0].get("diagnostics", {}),
            })
    return rows


def h1_verdict(manifests: list[dict]) -> H1Verdict:
    """The mechanical H1 verdict over the core operating region (spec Section 3)."""
    cells = _by_cell_policy(manifests)
    verdicts = [cv for ck, cell in sorted(cells.items())
                if (cv := cell_verdict(ck, cell)) is not None]

    core = [c for c in verdicts if c.excluded is None]
    excluded_n = len(verdicts) - len(core)

    if not core:
        return H1Verdict("KILL", "no core cells present", 0, 0, 0.0, 0.0, 0.0,
                         excluded_n, verdicts)

    support_cells = sum(1 for c in core if c.supports)
    support_fraction = support_cells / len(core)
    median_improvement = _median([c.improvement for c in core])
    guardrail_intact_fraction = sum(1 for c in core if c.guardrail_ok) / len(core)

    if guardrail_intact_fraction < GUARDRAIL_REGION_FRACTION:
        verdict, reason = "KILL", (
            f"interactive guardrail violated across the region "
            f"(intact in only {guardrail_intact_fraction:.0%} of core cells)")
    elif support_fraction >= SUPPORT_CELL_FRACTION:
        verdict, reason = "SUPPORT", (
            f"improvement >= {SUPPORT_IMPROVEMENT:.0%} with guardrail intact in "
            f"{support_fraction:.0%} of core cells (>= {SUPPORT_CELL_FRACTION:.0%})")
    elif median_improvement >= WEAK_IMPROVEMENT:
        verdict, reason = "WEAK", (
            f"median improvement {median_improvement:+.1%} in "
            f"[{WEAK_IMPROVEMENT:.0%}, {SUPPORT_IMPROVEMENT:.0%}), guardrail intact, "
            f"but support in only {support_fraction:.0%} of cells")
    else:
        verdict, reason = "KILL", (
            f"median improvement {median_improvement:+.1%} < {WEAK_IMPROVEMENT:.0%}")

    return H1Verdict(verdict, reason, len(core), support_cells, support_fraction,
                     median_improvement, guardrail_intact_fraction, excluded_n,
                     verdicts)


# --- H2 (served-value AUC over the load sweep) ----------------------------------

def h2_auc(manifests: list[dict]) -> dict:
    """Area under served-value-vs-offered-load, per policy (spec Section 2 H2 metric).

    Served value vs offered load, integrated (trapezoid) over the swept loads. A
    higher AUC is a better graceful-degradation curve. Returns per-policy AUC and the
    reservation-vs-best-baseline comparison.
    """
    # policy -> load -> [served_value across seeds]
    by = {}
    for m in manifests:
        pol = m["spec"]["policy"]
        load = float(m["spec"]["load"])
        by.setdefault(pol, {}).setdefault(load, []).append(
            m["metrics"]["served_value_fraction"])

    auc = {}
    for pol, loads in by.items():
        xs = sorted(loads)
        ys = [_median(loads[x]) for x in xs]
        area = 0.0
        for i in range(1, len(xs)):
            area += (xs[i] - xs[i - 1]) * (ys[i] + ys[i - 1]) / 2.0
        span = xs[-1] - xs[0] if len(xs) > 1 else 1.0
        auc[pol] = area / span if span > 0 else 0.0   # normalized (mean served value)

    best_base = max((auc[b] for b in _BASELINES if b in auc), default=0.0)
    ours = auc.get(OURS, 0.0)
    return {"auc": auc, "ours": ours, "best_baseline_auc": best_base,
            "relative": (ours - best_base) / best_base if best_base else 0.0,
            "loads": sorted({float(m["spec"]["load"]) for m in manifests})}


# --- H3 (compliant completion-rate delta under runaway injection) ---------------

def h3_deltas(manifests: list[dict]) -> dict:
    """Compliant-flow completion-rate vs runaway-injection level, per policy.

    Spec Section 2 H3 metric: completion-rate delta for compliant flows at
    0/1/5/10 percent runaway injection. A bounded delta (the discipline's cross-
    invocation budget contains runaways) versus unbounded degradation (per-request
    caps) is the H3 claim.
    """
    # policy -> injection -> [compliant_completion_rate across seeds]
    by = {}
    for m in manifests:
        pol = m["spec"]["policy"]
        inj = float(m["spec"].get("runaway_fraction", 0.0))
        by.setdefault(pol, {}).setdefault(inj, []).append(
            m["compliant_completion_rate"])

    out = {}
    for pol, injs in by.items():
        base = _median(injs.get(0.0, [0.0]))
        curve = {inj: _median(v) for inj, v in sorted(injs.items())}
        deltas = {inj: (rate - base) for inj, rate in curve.items()}
        out[pol] = {"curve": curve, "deltas": deltas,
                    "worst_delta": min(deltas.values()) if deltas else 0.0}
    return out


# --- readout document -----------------------------------------------------------

def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def write_h1_readout(h1: "H1Verdict", h2: dict | None, h3: dict | None,
                     provenance: dict, path) -> "object":
    """Emit docs/H1_READOUT.md: numbers and the threshold comparison only."""
    from pathlib import Path
    path = Path(path)
    final = provenance.get("final", False)
    L: list[str] = []
    L.append("# T-055 H1 Readout (SG7)" + (" -- FINAL" if final else ""))
    L.append("")
    if final:
        L.append("FINAL. Full pre-registered grid at the Amendment 2 horizon. This "
                 "document triggers the D-058 matrix on the strategy surface; no "
                 "measurement correction between launch and this readout (one-fix "
                 "clause). Under the one-fix clause the verdict below stands as "
                 "computed.")
        L.append("")
    L.append("Mechanical verdict against the pre-registered spec Section 3 "
             "thresholds. Numbers and the threshold comparison only (brief Section "
             "6). Generated by `make h1-readout`; a pure function of the manifest "
             "set. Per D-058 the filing executes off the H1 verdict with no "
             "re-decision.")
    L.append("")
    L.append(f"Provenance: {provenance.get('n_manifests', 0)} H1 manifests, "
             f"git {provenance.get('commit', 'n/a')}, "
             f"instances {provenance.get('instances', 'n/a')}. "
             f"Scope: {provenance.get('scope', 'full grid')}.")
    L.append("")

    # --- collapse disclosure (operator ruling 2026-07-21) ---
    collapses = provenance.get("collapses") or []
    if collapses:
        L.append("## Runs that collapsed (wall-clock cap reached)")
        L.append("")
        L.append("These runs did not complete: the policy stopped making progress "
                 "(congestion collapse) and was stopped at its cap. They carry NO "
                 "metrics -- numbers accumulated up to a cut-off are the "
                 "right-censored quantity Amendment 2 exists to exclude -- so they "
                 "are absent from every aggregate below.")
        L.append("")
        L.append("| cell | policy | collapsed seeds | surviving seeds | queue depth |")
        L.append("|---|---|---|---|---|")
        for r in collapses:
            q = r.get("diagnostics", {}).get("queue_depth", "n/a")
            L.append(f"| {r['cell']} | {r['policy']} | "
                     f"{','.join(str(s) for s in r['collapsed_seeds'])} | "
                     f"{r['surviving_seeds']} | {q} |")
        L.append("")
        partial = [r for r in collapses if r["partial"]]
        if partial:
            L.append("> SELECTION EFFECT -- read before using the cells above. In "
                     "these cells the policy collapsed on some seeds and completed "
                     "on others, so its recorded numbers come only from the "
                     "trajectories where it survived. That FLATTERS the policy. "
                     "Where the affected policy is the best baseline, the bias runs "
                     "AGAINST the reservation discipline, i.e. against SUPPORT.")
            for r in partial:
                L.append(f">   * {r['policy']} in {r['cell']}: "
                         f"{len(r['collapsed_seeds'])} collapsed, "
                         f"{r['surviving_seeds']} recorded.")
            L.append("")

    # --- horizon caveat (H3_ZERO_CHECK.md) ---
    hor = provenance.get("h1_horizon_s")
    if hor is not None and hor < 1500:
        L.append(f"> CAVEAT: this H1 verdict was computed at a {hor:.0f}s horizon, "
                 f"shorter than the agentic flow lifetime (median ~863s). That "
                 f"suppresses agentic completions and biases H1 against the "
                 f"reservation discipline (measured: a -22% deficit at 300s becomes "
                 f"-8% at an adequate horizon on one cell). See "
                 f"docs/H3_ZERO_CHECK.md. The final grid must run at >= 1500s.")
        L.append("")

    # --- the verdict ---
    L.append("## H1 VERDICT: " + h1.verdict)
    L.append("")
    L.append(f"- {h1.reason}")
    L.append(f"- core cells: {h1.core_cells} (excluded corner cells: "
             f"{h1.excluded_cells})")
    L.append(f"- cells supporting (improvement >= {SUPPORT_IMPROVEMENT:.0%} AND "
             f"guardrail): {h1.support_cells} / {h1.core_cells} "
             f"({h1.support_fraction:.0%}); support needs "
             f">= {SUPPORT_CELL_FRACTION:.0%}")
    L.append(f"- median improvement across core cells: "
             f"{_pct(h1.median_improvement)}")
    L.append(f"- interactive guardrail intact in {h1.guardrail_intact_fraction:.0%} "
             f"of core cells")
    L.append("")

    # --- per-cell table ---
    L.append("## Per-cell (primary metric: goodput per provisioned GPU-hour)")
    L.append("")
    L.append("Best baseline selected per cell; improvement is reservation vs that "
             "baseline (median across seeds), with a 95% paired bootstrap CI.")
    L.append("")
    L.append("| Cell | Best baseline | Reservation | Improvement (95% CI) | Guardrail | Class |")
    L.append("|------|---------------|------------:|----------------------|-----------|-------|")
    for c in h1.cells:
        cls = (f"CORNER ({c.excluded})" if c.excluded
               else "SUPPORT" if c.supports
               else "no")
        ci = f"{_pct(c.improvement)} [{_pct(c.ci_lo)}, {_pct(c.ci_hi)}]"
        gr = "ok" if c.guardrail_ok else f"FAIL ({_pct(c.ttft_degradation)})"
        L.append(f"| {c.cell_key} | {c.best_baseline} | {c.ours_goodput:.0f} | "
                 f"{ci} | {gr} | {cls} |")
    L.append("")

    # --- H2 ---
    L.append("## H2 - served-value AUC over the load sweep (papers only, not filing)")
    L.append("")
    if h2 and h2.get("auc"):
        L.append(f"Loads swept: {h2['loads']}. Normalized area under served-value-"
                 f"vs-offered-load (higher = better graceful degradation).")
        L.append("")
        L.append("| Policy | Served-value AUC |")
        L.append("|--------|-----------------:|")
        for pol, a in sorted(h2["auc"].items(), key=lambda kv: -kv[1]):
            L.append(f"| {pol} | {a:.3f} |")
        L.append("")
        L.append(f"reservation vs best baseline AUC: {_pct(h2['relative'])}")
    else:
        L.append("_Awaiting the H2 load sweep (configs/grid_h2.yaml)._")
    L.append("")

    # --- H3 ---
    L.append("## H3 - compliant completion-rate under runaway injection (papers only)")
    L.append("")
    if h3:
        # Absolute compliant completion rate at each injection level, alongside the
        # worst delta (ordered: absolute rates make a 0.000 baseline legible -- see
        # docs/H3_ZERO_CHECK.md; some baselines starve compliant flows outright).
        injs = sorted({inj for d in h3.values() for inj in d["curve"]})
        L.append("Absolute compliant-flow completion rate at each runaway-injection "
                 "level, and the worst delta vs the 0% level. A 0.000 is genuine "
                 "starvation (docs/H3_ZERO_CHECK.md), not a missing measurement.")
        L.append("")
        header = "| Policy | " + " | ".join(f"inj {int(i*100)}%" for i in injs) \
                 + " | worst delta |"
        L.append(header)
        L.append("|--------|" + "|".join(["-------:"] * len(injs)) + "|------------:|")
        for pol, d in sorted(h3.items(),
                             key=lambda kv: kv[1]["curve"].get(0.0, 0.0),
                             reverse=True):
            rates = " | ".join(f"{d['curve'].get(i, 0.0):.3f}" for i in injs)
            L.append(f"| {pol} | {rates} | {_pct(d['worst_delta'])} |")
    else:
        L.append("_Awaiting the H3 adversarial sweep (configs/grid_h3.yaml)._")
    L.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")
    return path


def main() -> None:
    import argparse
    from pathlib import Path

    from ..experiments.grid import RESULTS_DIR, load_results

    ap = argparse.ArgumentParser(description="Generate the H1 readout from manifests")
    ap.add_argument("--h1-dir", default=str(RESULTS_DIR),
                    help="results dir for the main H1 grid")
    ap.add_argument("--h2-dir", default=str(RESULTS_DIR / "h2"))
    ap.add_argument("--h3-dir", default=str(RESULTS_DIR / "h3"))
    ap.add_argument("--scope", default="full grid",
                    help="label for the manifest scope (e.g. 'PILOT (9 cells)')")
    ap.add_argument("--final", action="store_true",
                    help="mark FINAL and write docs/H1_READOUT_FINAL.md (triggers "
                         "D-058 on the strategy surface)")
    args = ap.parse_args()

    h1_manifests = load_results(Path(args.h1_dir))
    if not h1_manifests:
        raise SystemExit(f"no manifests in {args.h1_dir} -- run the grid first")
    # Refuse to compute a verdict over cells the grid does not define. This runs
    # BEFORE anything is aggregated, so contamination cannot reach the readout.
    assert_manifests_match_grid(h1_manifests)
    verdict = h1_verdict(h1_manifests)

    h2_manifests = load_results(Path(args.h2_dir))
    h3_manifests = load_results(Path(args.h3_dir))
    h2 = h2_auc(h2_manifests) if h2_manifests else None
    h3 = h3_deltas(h3_manifests) if h3_manifests else None

    commits = {m.get("git_commit") for m in h1_manifests}
    instances = {m.get("instance") for m in h1_manifests}
    horizons = {float(m["spec"]["horizon_s"]) for m in h1_manifests}
    provenance = {"n_manifests": len(h1_manifests),
                  "commit": ",".join(sorted(c for c in commits if c)),
                  "instances": ",".join(sorted(i for i in instances if i)),
                  "h1_horizon_s": min(horizons) if horizons else None,
                  "collapses": collapse_report(h1_manifests),
                  "scope": args.scope}

    provenance["final"] = args.final
    docs = Path(__file__).resolve().parent.parent / "docs"
    out = docs / ("H1_READOUT_FINAL.md" if args.final else "H1_READOUT.md")
    path = write_h1_readout(verdict, h2, h3, provenance, out)
    print(f"H1 VERDICT: {verdict.verdict} -- {verdict.reason}")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
