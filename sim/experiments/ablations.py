"""Ablation sweep A1-A5 (spec Section 7): one reservation element removed at a time.

Dual-use per the spec: the ablation table is both the paper's per-element effect and
prosecution evidence of each claim element's technical contribution. Each ablation
takes the TUNED reservation discipline and removes exactly one element, so the delta
vs the full discipline is that element's contribution to the primary metric.

  A1 - minus the cross-invocation budget (per-request cap instead).
  A2 - minus the non-preemption guarantee (reservations preemptible).
  A3 - minus the headroom key (queue-depth-keyed degradation instead).
  A4 - strict-pin vs tiered-pin (both embodiments, run explicitly).
  A5 - K sensitivity (reserved-subset bound swept).

Runs the reservation policy with (tuned params for the cell's mix) + (the ablation
override), so an ablation is a change to ONE element of the tuned discipline, nothing
else. Append-only manifests under results/ablations/.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import copy
import json

from ..core.orchestration import (
    CONFIG_DIR,
    build_cluster,
    load_degradation_catalog,
    load_policy_config,
    load_service_model,
    load_yaml,
    run_simulation,
)
from ..core.types import RunConfig
from ..scheduler.policies.reservation import ReservationPolicy
from ..tuning.harness import apply_overrides
from ..workload.generator import WorkloadGenerator
from .grid import RESULTS_DIR, load_results

# variant -> dotted-path override on the reservation config (removing one element).
ABLATIONS: dict[str, dict] = {
    "full": {},                                            # the tuned discipline
    "A1_no_budget": {"budget.enabled": False},             # per-request cap instead
    "A2_preemptible": {"non_preemptible": False},          # guarantee off
    "A3_queue_depth": {"degradation.key": "queue_depth"},  # not headroom-keyed
    "A4_strict": {"pin_mode": "strict"},                   # embodiment (A4)
    "A4_tiered": {"pin_mode": "tiered"},                   # embodiment (A4)
    "A5_k0.01": {"reserved_subset_fraction": 0.01},        # K sweep (A5)
    "A5_k0.15": {"reserved_subset_fraction": 0.15},
    "A5_k0.30": {"reserved_subset_fraction": 0.30},
}


def _run_one(variant: str, override: dict, mix: float, load: float,
             seed: int, cell: dict, tuned: dict) -> dict:
    service = load_service_model()
    catalog = load_degradation_catalog()
    base_policy = load_policy_config()["reservation"]
    overrides = {
        "mix.agentic_token_share": mix,
        "arrivals.base_rate_per_s": load * cell["capacity_per_device"]
                                    * cell["num_devices"]}
    # Gap decides whether the reserved subset is used AT ALL: long tool-time leaves it
    # idle (94-95 percent in the original ablation cell), and every reserved-flow
    # element then measures as inert. Honour a cell-specified gap instead of silently
    # keeping the workload default -- otherwise a contended cell is not contended.
    if cell.get("gap_median_s") is not None:
        gap = float(cell["gap_median_s"])
        overrides["classes.agentic.gap_s.median"] = gap
        overrides["classes.agentic.gap_s.p95"] = max(300.0, gap * 15.0)
    workload = apply_overrides(load_yaml(CONFIG_DIR / "workload.yaml"), overrides)

    family = f"mix_{int(round(mix * 100)):02d}"
    tuned_res = tuned.get("reservation", {}).get(family, {})
    cfg = apply_overrides(copy.deepcopy(base_policy), tuned_res)   # tuned discipline
    cfg = apply_overrides(cfg, override)                            # minus one element
    policy = ReservationPolicy.from_config(cfg)
    policy.attach_kv_rate(service.kv_mib_per_token)

    cluster_cfg = {"num_devices": cell["num_devices"],
                   "device": {"hbm_capacity_gib": cell["hbm_gib"],
                              "max_concurrent_decodes": cell["slots"]}}
    r = run_simulation(
        build_cluster(cluster_cfg, service), policy,
        WorkloadGenerator(workload, seed=seed, horizon_s=cell["horizon_s"]),
        RunConfig(horizon_s=cell["horizon_s"], warmup_s=cell["warmup_s"],
                  seed=seed, tick_interval_s=cell["tick_s"]),
        degradation_catalog=catalog)
    return {"variant": variant, "mix": mix, "load": load, "seed": seed,
            "goodput_per_gpu_hour": r.goodput_per_gpu_hour,
            "served_value_fraction": r.served_value_fraction,
            "reserved_idle_fraction": r.reserved_idle_fraction,
            "abandonments": r.abandonments,
            # The run id hashes only (variant, mix, load, seed), so two cells that
            # differ ONLY in slots/devices/gap would collide in one directory. Record
            # the cell so a mixed directory is detectable rather than silently averaged.
            "cell": {k: cell.get(k) for k in
                     ("num_devices", "slots", "hbm_gib", "horizon_s", "warmup_s",
                      "gap_median_s", "capacity_per_device")}}


def run_ablations(cfg: dict, results_dir=None) -> int:
    """Run every ablation variant across the configured cells. Append-only."""
    import hashlib
    results_dir = results_dir or (RESULTS_DIR / "ablations")
    results_dir.mkdir(parents=True, exist_ok=True)
    tuned = json.loads((CONFIG_DIR / "tuned_params.json").read_text())
    cell = cfg["cell"]
    ran = 0
    for variant, override in ABLATIONS.items():
        for mix in cfg["axes"]["mix"]:
            for load in cfg["axes"]["load"]:
                for seed in cfg["seeds"]:
                    key = f"{variant}_{mix}_{load}_{seed}"
                    rid = hashlib.sha256(key.encode()).hexdigest()[:16]
                    path = results_dir / f"{rid}.json"
                    if path.exists():
                        continue
                    m = _run_one(variant, override, mix, load, seed, cell, tuned)
                    tmp = path.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(m, sort_keys=True), encoding="utf-8")
                    tmp.rename(path)
                    ran += 1
    return ran


def ablation_effects(results_dir=None) -> dict:
    """Per-element effect: each ablation's median goodput vs the full discipline."""
    import statistics
    results_dir = results_dir or (RESULTS_DIR / "ablations")
    manifests = [m for m in load_results(results_dir) if "variant" in m] \
        if results_dir.exists() else []
    # Fall back to reading raw (ablation manifests lack run_id/cell_key).
    if not manifests and results_dir.exists():
        manifests = [json.loads(p.read_text())
                     for p in results_dir.glob("*.json")
                     if p.name != "scaling_probe.json"]

    by_variant: dict = {}
    for m in manifests:
        by_variant.setdefault(m["variant"], []).append(m["goodput_per_gpu_hour"])
    med = {v: statistics.median(g) for v, g in by_variant.items() if g}
    full = med.get("full", 0.0)
    effects = {v: {"goodput": g, "vs_full": (g - full) / full if full else 0.0}
               for v, g in med.items()}
    return {"full": full, "effects": effects}
