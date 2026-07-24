"""SG1 sanity check: Little's law on an M/M/1-degenerate configuration.

Runs the sanity_mm1.yaml configuration under the FCFS floor policy and compares:
  * L (time-average number in system) vs lambda * W (Little's law -- validates the
    engine's accounting), and
  * L vs rho / (1 - rho) (validates the M/M/1 physics against closed form).

Run:  make littles   (from code/sim)
"""

from __future__ import annotations

from pathlib import Path

from ..core.orchestration import CONFIG_DIR, build_cluster, load_service_model, load_yaml, run_simulation
from ..core.seed import SeedManager
from ..core.types import RunConfig
from ..scheduler.policies.fcfs import FCFSPolicy
from .workloads import mm1_workload


def run_littles_law(config_path: Path | None = None) -> dict:
    """Run the M/M/1 sanity check and return a result dict."""
    if config_path is None:
        config_path = CONFIG_DIR / "sanity_mm1.yaml"
    cfg = load_yaml(config_path)

    service = load_service_model()
    cluster = build_cluster(cfg["cluster"], service)

    wcfg = cfg["workload"]
    seed_mgr = SeedManager(cfg["seed"])
    run_cfg = RunConfig(
        horizon_s=cfg["run"]["horizon_s"],
        warmup_s=cfg["run"]["warmup_s"],
        seed=cfg["seed"],
    )

    policy = FCFSPolicy()
    policy.attach_kv_rate(service.kv_mib_per_token)

    workload = mm1_workload(
        seed_mgr=seed_mgr,
        arrival_rate_per_s=wcfg["arrival_rate_per_s"],
        mean_output_tokens=wcfg["mean_output_tokens"],
        prefill_tokens=wcfg["prefill_tokens"],
        horizon_s=run_cfg.horizon_s,
    )
    result = run_simulation(cluster, policy, workload, run_cfg)

    # Closed-form M/M/1 reference.
    e_service = wcfg["mean_output_tokens"] / service.single_stream_tokens_per_s
    rho = wcfg["arrival_rate_per_s"] * e_service
    l_theory = rho / (1.0 - rho)

    return {
        "rho": rho,
        "L": result.littles_law_lhs,
        "lambda_W": result.littles_law_rhs,
        "lambda": result.throughput_per_s,
        "W": result.mean_sojourn_s,
        "L_theory_mm1": l_theory,
        "utilization": result.mean_utilization,
        "completions": result.completions,
        "result": result,
    }


def main() -> None:
    r = run_littles_law()
    print("=== SG1 sanity: Little's law (M/M/1-degenerate) ===")
    print(f"  offered load rho          : {r['rho']:.4f}")
    print(f"  completions (post-warmup) : {r['completions']}")
    print(f"  throughput lambda (1/s)   : {r['lambda']:.4f}")
    print(f"  mean sojourn W (s)        : {r['W']:.4f}")
    print(f"  L (time-avg in system)    : {r['L']:.4f}")
    print(f"  lambda * W                : {r['lambda_W']:.4f}   "
          f"(rel err {abs(r['L'] - r['lambda_W']) / r['L']:.2%})")
    print(f"  M/M/1 theory rho/(1-rho)  : {r['L_theory_mm1']:.4f}   "
          f"(rel err {abs(r['L'] - r['L_theory_mm1']) / r['L_theory_mm1']:.2%})")
    print(f"  device utilization        : {r['utilization']:.4f}   "
          f"(theory {r['rho']:.4f})")


if __name__ == "__main__":
    main()
