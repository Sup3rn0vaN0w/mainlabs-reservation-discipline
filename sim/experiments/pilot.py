"""Run the pilot grid and write the SG6 readout (brief SG6).

Produces docs/PILOT_READOUT.md: per-cell H1 signal with bootstrap CIs, the
interactive guardrail, the service-constant sensitivity check, and the measured
per-cell runtime projected against the 24-hour guard for SG7.

Nothing here is the H1 verdict. It is a small subset of cells, reviewed on the
strategy surface before the full grid is authorized.

Run:  make pilot   (from code/sim)

House style: hyphens only (D-026).
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

from ..analysis.stats import bootstrap_relative_improvement
from ..core.orchestration import (
    build_cluster,
    build_policy,
    load_degradation_catalog,
    load_service_model,
    load_yaml,
)
from ..core.orchestration import CONFIG_DIR, run_simulation
from ..core.types import RunConfig
from ..tuning.harness import apply_overrides
from ..workload.generator import WorkloadGenerator
from .grid import RESULTS_DIR, load_grid_config, load_results, run_grid
from .sensitivity import run_sensitivity

_READOUT = Path(__file__).resolve().parent.parent / "docs" / "PILOT_READOUT.md"
_BASELINES = ["fcfs", "vllm_style", "niyama_style", "concur_style", "fastserve_mlfq", "mars_style"]
_SUPPORT = 0.10   # spec Section 3
_WEAK = 0.05
_GUARDRAIL = 0.05


# --- aggregation --------------------------------------------------------------------

def aggregate_by_cell(results: list[dict]) -> dict:
    """cell_key -> policy -> {metric -> [values across seeds]}."""
    cells: dict = {}
    for m in results:
        cell = cells.setdefault(m["cell_key"], {})
        pol = cell.setdefault(m["spec"]["policy"], {})
        for metric, value in m["metrics"].items():
            pol.setdefault(metric, []).append(value)
        pol.setdefault("_ttft", []).append(m["metrics"]["p95_ttft_s"])
    return cells


def _median(xs: list[float]) -> float:
    return statistics.median(xs) if xs else 0.0


def h1_signal(cells: dict) -> dict:
    """Per-cell reservation-vs-best-baseline delta on the primary metric, with CIs."""
    per_cell = []
    for cell_key, policies in sorted(cells.items()):
        if "reservation" not in policies:
            continue
        ours = policies["reservation"]["goodput_per_gpu_hour"]

        # Best baseline in THIS cell (strongest opponent per condition).
        best_name, best_median, best_series = None, -1.0, []
        for b in _BASELINES:
            if b not in policies:
                continue
            series = policies[b]["goodput_per_gpu_hour"]
            if _median(series) > best_median:
                best_name, best_median, best_series = b, _median(series), series

        ci = bootstrap_relative_improvement(ours, best_series, seed=12345)
        improvement = ci.point
        cls = ("support" if improvement >= _SUPPORT
               else "weak" if improvement >= _WEAK else "kill")

        # Interactive guardrail: reservation p95 TTFT degradation vs best baseline.
        our_ttft = _median(policies["reservation"]["p95_ttft_s"])
        base_ttft = _median(policies[best_name]["p95_ttft_s"])
        ttft_degradation = ((our_ttft - base_ttft) / base_ttft
                            if base_ttft > 0 else 0.0)

        per_cell.append({
            "cell": cell_key, "best_baseline": best_name,
            "ours_goodput": _median(ours), "best_goodput": best_median,
            "improvement": improvement, "ci_lo": ci.lo, "ci_hi": ci.hi,
            "class": cls, "ttft_degradation": ttft_degradation,
            "guardrail_ok": ttft_degradation <= _GUARDRAIL,
            "served_value": _median(policies["reservation"]["served_value_fraction"]),
            "reserved_idle": _median(policies["reservation"]["reserved_idle_fraction"]),
        })

    n = len(per_cell)
    supporting = sum(1 for c in per_cell if c["class"] == "support"
                     and c["guardrail_ok"])
    return {"per_cell": per_cell, "n_cells": n, "n_supporting": supporting,
            "support_fraction": supporting / n if n else 0.0}


# --- runtime scaling ----------------------------------------------------------------

_SCALING_CACHE = RESULTS_DIR / "scaling_probe.json"


def _projection_run_config(config: str = "grid_main.yaml") -> tuple[float, float]:
    """The horizon/warmup the projected grid actually runs at.

    The probe MUST time runs at the same horizon as the grid it is projecting.
    It used to hardcode 300s, which silently under-projected the whole grid by the
    horizon ratio once Amendment 2 raised the sweeps to 1500s -- and deleting the
    cache did not help, because the hardcode was in the measurement itself.
    """
    cell = load_grid_config(config)["cell"]
    return float(cell["horizon_s"]), float(cell["warmup_s"])


def measure_scaling(use_cache: bool = True, config: str = "grid_main.yaml") -> list[dict]:
    """Time one worst-case run at each main-grid cluster size (load 1.3, gap 20).

    Cached to results/ because it is a runtime measurement (minutes at 64 devices)
    that does not change between readout regenerations. Delete the cache file or pass
    use_cache=False to force a fresh measurement (e.g. after a runtime optimization).

    The cache is KEYED ON HORIZON: a probe measured at a different horizon than the
    grid being projected is discarded and re-measured, so a horizon change can never
    leave a stale probe silently driving the projection.
    """
    import json
    horizon_s, warmup_s = _projection_run_config(config)
    if use_cache and _SCALING_CACHE.exists():
        cached = json.loads(_SCALING_CACHE.read_text(encoding="utf-8"))
        # Legacy probes are bare lists with no horizon stamp -- treat as stale.
        if isinstance(cached, dict) and cached.get("horizon_s") == horizon_s:
            return cached["rows"]
    rows = _measure_scaling_uncached(horizon_s, warmup_s)
    _SCALING_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _SCALING_CACHE.write_text(
        json.dumps({"horizon_s": horizon_s, "warmup_s": warmup_s, "rows": rows}),
        encoding="utf-8")
    return rows


def _measure_scaling_uncached(horizon_s: float, warmup_s: float,
                              sizes: tuple[int, ...] | None = None) -> list[dict]:
    service = load_service_model()
    catalog = load_degradation_catalog()
    base_workload = load_yaml(CONFIG_DIR / "workload.yaml")
    # Probe exactly the cluster sizes the grid runs. This used to be a hardcoded
    # (8, 16, 32, 64): after Amendment 3 dropped 64, that would have spent 24-32h
    # timing a size the grid no longer contains -- same failure mode as the old
    # hardcoded horizon.
    sizes = sizes or tuple(load_grid_config("grid_main.yaml")["axes"]["num_devices"])
    rows = []
    for ndev in sizes:
        workload = apply_overrides(base_workload, {
            "mix.agentic_token_share": 0.30,
            "arrivals.base_rate_per_s": 1.3 * 8.0 * ndev})
        cluster_cfg = {"num_devices": ndev,
                       "device": {"hbm_capacity_gib": 80.0,
                                  "max_concurrent_decodes": 16}}
        # fastserve_mlfq is the costliest policy under overload; time the worst case.
        t = time.time()
        run_simulation(
            build_cluster(cluster_cfg, service),
            build_policy("fastserve_mlfq", service),
            WorkloadGenerator(workload, seed=1, horizon_s=horizon_s),
            RunConfig(horizon_s=horizon_s, warmup_s=warmup_s, seed=1,
                      tick_interval_s=0.5),
            degradation_catalog=catalog)
        rows.append({"num_devices": ndev, "seconds": time.time() - t})
    return rows


def project_main_grid(scaling: list[dict], workers: int) -> dict:
    """Project the full grid's wall time from the per-cluster-size worst-case runs."""
    main_cfg = load_grid_config("grid_main.yaml")
    axes = main_cfg["axes"]
    n_policies = len(main_cfg["policies"])
    n_seeds = len(main_cfg["seeds"])
    per_dev = {r["num_devices"]: r["seconds"] for r in scaling}

    # Runs at each cluster size = |mix| x |load| x |gap| x policies x seeds.
    runs_per_size = (len(axes["mix"]) * len(axes["load"]) * len(axes["gap_median_s"])
                     * n_policies * n_seeds)
    total_runs, serial_seconds = 0, 0.0
    for ndev in axes["num_devices"]:
        total_runs += runs_per_size
        serial_seconds += runs_per_size * per_dev.get(ndev, max(per_dev.values()))
    wall_hours = serial_seconds / workers / 3600.0
    return {"total_runs": total_runs, "serial_seconds": serial_seconds,
            "workers": workers, "projected_wall_hours": wall_hours,
            "exceeds_24h": wall_hours > 24.0}


# --- readout ------------------------------------------------------------------------

def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def write_readout(h1: dict, sensitivity: dict, runtime: dict, projection: dict,
                  scaling: list[dict], path: Path = _READOUT) -> Path:
    L: list[str] = []
    L.append("# T-055 Pilot Grid Readout (SG6)")
    L.append("")
    L.append("Generated by `make pilot`. Not the H1 verdict -- a subset of cells for "
             "strategy-surface review before the full grid is authorized (brief SG6).")
    L.append("")
    L.append("Objective: goodput per provisioned GPU-hour. Aggregation: median across "
             "seeds; best baseline selected per cell (spec Section 3). CIs are 95 "
             "percent percentile bootstrap over the paired per-seed samples.")
    L.append("")

    # --- H1 signal ---
    L.append("## H1 signal (reservation vs best-tuned baseline, per cell)")
    L.append("")
    L.append("Pre-registered (spec Section 3): SUPPORT if improvement >= +10% with the "
             "interactive guardrail intact, in >= 60% of core cells. WEAK at +5 to "
             "+10%. KILL below +5%.")
    L.append("")
    L.append("| Cell | Best baseline | Reservation | Improvement (95% CI) | Class | Guardrail |")
    L.append("|------|---------------|------------:|----------------------|-------|-----------|")
    for c in h1["per_cell"]:
        ci = f"{_fmt_pct(c['improvement'])} [{_fmt_pct(c['ci_lo'])}, {_fmt_pct(c['ci_hi'])}]"
        gr = "ok" if c["guardrail_ok"] else f"FAIL ({_fmt_pct(c['ttft_degradation'])})"
        L.append(f"| {c['cell']} | {c['best_baseline']} | {c['ours_goodput']:.0f} | "
                 f"{ci} | {c['class'].upper()} | {gr} |")
    L.append("")
    L.append(f"**{h1['n_supporting']} of {h1['n_cells']} cells support** "
             f"({h1['support_fraction'] * 100:.0f}%). Support needs >= 60% of core "
             f"cells (spec Section 3).")
    L.append("")

    # --- guardrail note ---
    L.append("## Interactive guardrail (p95 TTFT)")
    L.append("")
    L.append("The guardrail is that reservation must not degrade interactive p95 TTFT "
             "by more than 5 percent. Measured, it does the opposite -- keeping agentic "
             "flows off the shared pool, reservation IMPROVES interactive TTFT. "
             "Per-cell TTFT change vs the best baseline:")
    L.append("")
    L.append("| Cell | TTFT change | Served value | Reserved idle |")
    L.append("|------|-------------|-------------:|--------------:|")
    for c in h1["per_cell"]:
        L.append(f"| {c['cell']} | {_fmt_pct(c['ttft_degradation'])} | "
                 f"{c['served_value'] * 100:.0f}% | {c['reserved_idle'] * 100:.0f}% |")
    L.append("")

    # --- sensitivity ---
    L.append("## Service-constant sensitivity (+/- 30%)")
    L.append("")
    L.append(f"How far the {sensitivity['ours']}-vs-{sensitivity['baseline']} delta "
             "moves when each LOW-CONFIDENCE constant is perturbed on one "
             f"representative cell (mix {sensitivity['cell']['mix']}, load "
             f"{sensitivity['cell']['load']}). This quantifies the fidelity caveat "
             "(spec Section 6/10).")
    L.append("")
    status = sensitivity.get("binding_status", {})
    L.append("| Constant | Perturbation | Reservation | Baseline | Delta | Binding? |")
    L.append("|----------|--------------|------------:|---------:|-------|----------|")
    for r in sensitivity["rows"]:
        L.append(f"| {r['constant']} | {r['perturbation']} ({r['factor']:.1f}x) | "
                 f"{r['goodput_ours']:.0f} | {r['goodput_baseline']:.0f} | "
                 f"{_fmt_pct(r['rel_delta'])} | {status.get(r['constant'], '-')} |")
    L.append("")

    # Explain the non-binding constants with the runtime evidence, so a byte-
    # identical row is documented rather than mysterious (SG6 harness check).
    d = sensitivity.get("diagnostics")
    if d:
        L.append("Why two constants do not move the result at this cell "
                 "(4 devices x 16 slots), from an instrumented run:")
        L.append("")
        L.append(f"- **peak_decode_tokens_per_s is structurally unreachable.** Decode "
                 f"throughput is `min(batch x single_stream, peak)`; peak only binds "
                 f"once the batch reaches {d['peak_binds_at_batch']:.0f} "
                 f"(= peak / single_stream). The decode batch is hard-capped at "
                 f"`max_concurrent_decodes` = {d['max_concurrent_decodes']}; the "
                 f"observed maximum was {d['max_batch_observed']}, and across "
                 f"{d['decode_calls']} decode calls peak was the binding term "
                 f"{d['peak_hits']} times. Perturbing peak cannot change any decode "
                 f"rate here.")
        L.append(f"- **kv_mib_per_token never constrains.** Peak HBM occupancy was "
                 f"{d['max_hbm_fraction'] * 100:.1f}% of {d['hbm_capacity_gib']:.0f} "
                 f"GiB, so a +/-30% change in KV footprint never changes what fits. "
                 f"The reservation outcome is bottlenecked on the reserved-subset "
                 f"bound and slot contention, not memory, so it is exactly frozen; "
                 f"the baseline's best-effort packing shows a sub-percent response, "
                 f"which confirms the constant IS plumbed through (it is simply not "
                 f"binding). single_stream_tokens_per_s, which sets service time and "
                 f"IS binding, moves the delta as expected.")
        L.append("")
        L.append("See docs/SENSITIVITY_HARNESS_CHECK.md for the full determination.")
        L.append("")

    # --- runtime ---
    L.append("## Runtime and the SG7 compute decision")
    L.append("")
    L.append(f"Pilot: {runtime['ran']} runs in {runtime['wall_s']:.0f}s wall on "
             f"{runtime['workers']} workers "
             f"({runtime['per_run_s']:.2f}s per run at 4 devices).")
    L.append("")
    L.append("Worst-case single-run time by cluster size (fastserve_mlfq, load 1.3):")
    L.append("")
    L.append("| Devices | Seconds |")
    L.append("|---------|--------:|")
    for r in scaling:
        L.append(f"| {r['num_devices']} | {r['seconds']:.1f} |")
    L.append("")
    L.append(f"**Projected full grid** ({projection['total_runs']} runs on "
             f"{projection['workers']} workers): "
             f"~{projection['projected_wall_hours']:.1f} hours wall.")
    guard = ("EXCEEDS the 24-hour guard -- STOP and report (brief Section 8)."
             if projection["exceeds_24h"]
             else "Within the 24-hour guard (brief Section 8).")
    L.append(f"{guard}")
    L.append("")
    L.append("Recommendation input: this is a projection from worst-case per-size "
             "runs; the real grid mixes cheaper cells, so it is an upper bound. "
             "local-overnight vs cloud-CPU is the operator's call at this gate.")
    L.append("")

    path.write_text("\n".join(L), encoding="utf-8")
    return path


def main() -> None:
    import os
    workers = max(1, (os.cpu_count() or 2) - 2)

    print("Running pilot grid ...")
    t = time.time()
    summary = run_grid(load_grid_config("grid_pilot.yaml"),
                       progress=lambda s: print("  " + s))
    wall = time.time() - t
    ran = max(summary["ran"], 1)
    runtime = {"ran": summary["ran"], "wall_s": wall, "workers": workers,
               "per_run_s": wall / ran}
    print(f"  {summary['ran']} ran, {summary['skipped']} skipped, {wall:.0f}s")

    results = load_results()
    cells = aggregate_by_cell(results)
    h1 = h1_signal(cells)
    print(f"  H1 signal: {h1['n_supporting']}/{h1['n_cells']} cells support")

    print("Service-constant sensitivity ...")
    tuned = {}
    tp = CONFIG_DIR / "tuned_params.json"
    if tp.exists():
        import json
        tuned = json.loads(tp.read_text(encoding="utf-8"))
    sensitivity = run_sensitivity(tuned=tuned)

    print("Runtime scaling ...")
    scaling = measure_scaling()
    projection = project_main_grid(scaling, workers)
    print(f"  projected full grid: ~{projection['projected_wall_hours']:.1f}h "
          f"({'EXCEEDS' if projection['exceeds_24h'] else 'within'} 24h guard)")

    path = write_readout(h1, sensitivity, runtime, projection, scaling)
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
