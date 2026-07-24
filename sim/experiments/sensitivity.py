"""Service-constant sensitivity check (brief Section 5, run at SG6).

Every service-model constant is LOW CONFIDENCE until Phase 2 calibration. This
check quantifies that caveat instead of just declaring it: it perturbs the three
most influential constants by +/- 30 percent on one representative cell and reports
how much the CONCLUSION moves -- specifically the reservation-vs-best-baseline
relative delta on the primary metric, which is what H1 turns on.

The three constants:
  * single_stream_tokens_per_s -- per-request decode rate (sets service time),
  * peak_decode_tokens_per_s   -- device decode saturation (sets batch economics),
  * kv_mib_per_token           -- KV footprint (sets how much fits, hence stranding).

House style: hyphens only (D-026).
"""

from __future__ import annotations

import copy

from ..cluster.service_model import ServiceModel
from ..core.orchestration import (
    CONFIG_DIR,
    build_cluster,
    build_policy,
    load_degradation_catalog,
    load_policy_config,
    load_yaml,
    run_simulation,
)
from ..core.types import RunConfig
from ..tuning.harness import apply_overrides
from ..workload.generator import WorkloadGenerator

# One representative core-region cell.
_MIX = 0.30
_LOAD = 1.0
_NUM_DEVICES = 4
_CAP_PER_DEVICE = 8.0
_HORIZON = 300.0
_WARMUP = 30.0
_TICK = 0.5
_SEEDS = [1, 2, 3]

_PERTURBATIONS = {
    "single_stream_tokens_per_s": ("decode", "single_stream_tokens_per_s"),
    "peak_decode_tokens_per_s": ("decode", "peak_decode_tokens_per_s"),
    "kv_mib_per_token": ("kv_cache", "mib_per_token"),
}
_FACTORS = {"nominal": 1.0, "minus30": 0.7, "plus30": 1.3}


def _goodput(policy_name: str, service: ServiceModel, tuned_params: dict) -> float:
    """Median goodput across seeds for one policy under a given service model."""
    base_workload = load_yaml(CONFIG_DIR / "workload.yaml")
    base_policy_cfg = load_policy_config()
    catalog = load_degradation_catalog()
    workload = apply_overrides(base_workload, {
        "mix.agentic_token_share": _MIX,
        "arrivals.base_rate_per_s": _LOAD * _CAP_PER_DEVICE * _NUM_DEVICES,
    })
    policy_cfg = apply_overrides(base_policy_cfg.get(policy_name, {}), tuned_params)
    cluster_cfg = {"num_devices": _NUM_DEVICES,
                   "device": {"hbm_capacity_gib": 80.0, "max_concurrent_decodes": 16}}

    scores = []
    for seed in _SEEDS:
        policy = build_policy(policy_name, service,
                              {**base_policy_cfg, policy_name: policy_cfg})
        r = run_simulation(
            build_cluster(cluster_cfg, service), policy,
            WorkloadGenerator(workload, seed=seed, horizon_s=_HORIZON),
            RunConfig(horizon_s=_HORIZON, warmup_s=_WARMUP, seed=seed,
                      tick_interval_s=_TICK),
            degradation_catalog=catalog)
        scores.append(r.goodput_per_gpu_hour)
    scores.sort()
    return scores[len(scores) // 2]


def _service_with(cfg: dict, section: str, key: str, factor: float) -> ServiceModel:
    perturbed = copy.deepcopy(cfg)
    perturbed[section][key] = perturbed[section][key] * factor
    return ServiceModel.from_config(perturbed)


class _InstrumentedService:
    """Wraps a ServiceModel and records how its constants are consumed at runtime.

    A byte-identical sensitivity row is only trustworthy if we can show WHY the
    constant does not bite. This shim records, at the exact points the engine calls
    the service model, whether peak decode is ever the binding term and how large the
    decode batch grows -- the evidence a reviewer will demand.
    """

    def __init__(self, service: ServiceModel) -> None:
        self._svc = service
        self.max_batch = 0
        self.peak_hits = 0
        self.decode_calls = 0

    def __getattr__(self, name):
        return getattr(self._svc, name)

    def decode_throughput(self, batch_size: int) -> float:
        self.decode_calls += 1
        self.max_batch = max(self.max_batch, batch_size)
        if batch_size * self._svc.single_stream_tokens_per_s \
                >= self._svc.peak_decode_tokens_per_s:
            self.peak_hits += 1        # peak is the binding term this call
        return self._svc.decode_throughput(batch_size)

    def decode_rate_per_request(self, batch_size: int) -> float:
        if batch_size <= 0:
            return 0.0
        return self.decode_throughput(batch_size) / batch_size


def binding_diagnostics(policy_name: str = "reservation",
                        tuned: dict | None = None, *, slots: int = 16,
                        mix: float = _MIX, load: float = _LOAD,
                        num_devices: int = _NUM_DEVICES, hbm_gib: float = 80.0) -> dict:
    """Instrument one run and report whether peak decode and KV can bind at all.

    Returns the runtime-observed evidence for a cell: the largest decode batch
    reached, whether peak was ever the binding term, the batch at which peak WOULD
    bind, and the peak HBM occupancy. At the default (16-slot) cell peak and KV
    cannot bind; the memory-bound cell (64 slots, agentic-heavy) is where they do,
    which is what proves the harness detects binding when it exists (PART B.2).
    """
    tuned = tuned or {}
    base_cfg = load_yaml(CONFIG_DIR / "service_model.yaml")
    service = ServiceModel.from_config(base_cfg)
    shim = _InstrumentedService(service)

    base_workload = load_yaml(CONFIG_DIR / "workload.yaml")
    base_policy_cfg = load_policy_config()
    catalog = load_degradation_catalog()
    workload = apply_overrides(base_workload, {
        "mix.agentic_token_share": mix,
        "arrivals.base_rate_per_s": load * _CAP_PER_DEVICE * num_devices})
    cluster_cfg = {"num_devices": num_devices,
                   "device": {"hbm_capacity_gib": hbm_gib,
                              "max_concurrent_decodes": slots}}
    cluster = build_cluster(cluster_cfg, shim)

    # Track peak HBM occupancy across the run by sampling on every residency change.
    max_hbm_frac = {"v": 0.0}
    for dev in cluster.devices:
        o_res, o_rel = dev.reserve, dev.release

        def wrap(orig, d):
            def f(*a, **k):
                r = orig(*a, **k)
                max_hbm_frac["v"] = max(max_hbm_frac["v"],
                                        d.hbm_used_gib / d.hbm_capacity_gib)
                return r
            return f
        dev.reserve, dev.release = wrap(o_res, dev), wrap(o_rel, dev)

    params = tuned.get(policy_name, {}).get("mix_30", {})
    policy_cfg = apply_overrides(base_policy_cfg.get(policy_name, {}), params)
    policy = build_policy(policy_name, shim,
                          {**base_policy_cfg, policy_name: policy_cfg})
    run_simulation(cluster, policy,
                   WorkloadGenerator(workload, seed=1, horizon_s=_HORIZON),
                   RunConfig(horizon_s=_HORIZON, warmup_s=_WARMUP, seed=1,
                             tick_interval_s=_TICK),
                   degradation_catalog=catalog)

    peak_binds_at = (service.peak_decode_tokens_per_s
                     / service.single_stream_tokens_per_s)
    return {
        "policy": policy_name,
        "max_batch_observed": shim.max_batch,
        "max_concurrent_decodes": slots,
        "peak_binds_at_batch": peak_binds_at,
        "peak_hits": shim.peak_hits,
        "decode_calls": shim.decode_calls,
        "peak_reachable": shim.max_batch >= peak_binds_at,
        "max_hbm_fraction": max_hbm_frac["v"],
        "hbm_capacity_gib": hbm_gib,
    }


def run_sensitivity(ours: str = "reservation", baseline: str = "niyama_style",
                    tuned: dict | None = None) -> dict:
    """Perturb each constant +/-30 percent; report the H1 delta's movement."""
    tuned = tuned or {}
    base_cfg = load_yaml(CONFIG_DIR / "service_model.yaml")
    ours_params = tuned.get(ours, {}).get("mix_30", {})
    base_params = tuned.get(baseline, {}).get("mix_30", {})

    rows = []
    per_constant: dict = {}
    for const, (section, key) in _PERTURBATIONS.items():
        for label, factor in _FACTORS.items():
            if label == "nominal" and const != "single_stream_tokens_per_s":
                continue  # nominal is the same for all constants; compute once
            service = _service_with(base_cfg, section, key, factor)
            g_ours = _goodput(ours, service, ours_params)
            g_base = _goodput(baseline, service, base_params)
            delta = (g_ours - g_base) / g_base if g_base else 0.0
            rows.append({"constant": const, "perturbation": label, "factor": factor,
                         "goodput_ours": g_ours, "goodput_baseline": g_base,
                         "rel_delta": delta})
            per_constant.setdefault(const, []).append((g_ours, g_base))

    # Classify each constant by how much perturbing it moves EITHER policy's goodput.
    # A byte-identical constant is non-binding; a sub-percent move is negligible
    # (the constant is plumbed through but barely bites); anything larger binds.
    # This is what stops a byte-identical or frozen row from looking like a bug
    # (per the SG6 harness check).
    binding = binding_diagnostics(ours, tuned)
    status = {}
    for const, pairs in per_constant.items():
        span = 0.0
        for vals in (([o for o, _ in pairs]), ([b for _, b in pairs])):
            lo, hi = min(vals), max(vals)
            if hi > 0:
                span = max(span, (hi - lo) / hi)
        status[const] = ("non-binding" if span == 0.0
                         else "negligible" if span < 0.01
                         else "binding")

    return {"ours": ours, "baseline": baseline, "cell":
            {"mix": _MIX, "load": _LOAD, "num_devices": _NUM_DEVICES},
            "rows": rows, "binding_status": status, "diagnostics": binding}
