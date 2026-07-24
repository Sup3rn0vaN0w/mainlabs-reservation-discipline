"""SG1 sanity check: utilization vs offered load.

Sweeps offered load on the single-server M/M/1-degenerate configuration and
reports device utilization at each point. Expected behaviour: utilization rises
monotically with offered load, stays below 1, and tracks rho (for an M/M/1 server,
utilization = rho).

Run:  make util   (from code/sim)
"""

from __future__ import annotations

from pathlib import Path

from ..core.orchestration import CONFIG_DIR, build_cluster, load_service_model, load_yaml, run_simulation
from ..core.seed import SeedManager
from ..core.types import RunConfig
from ..scheduler.policies.fcfs import FCFSPolicy
from .workloads import mm1_workload


def run_utilization_curve(config_path: Path | None = None) -> list[dict]:
    """Run the utilization sweep and return one dict per offered-load point."""
    if config_path is None:
        config_path = CONFIG_DIR / "sanity_util_curve.yaml"
    cfg = load_yaml(config_path)

    service = load_service_model()
    wcfg = cfg["workload"]
    e_service = wcfg["mean_output_tokens"] / service.single_stream_tokens_per_s

    points: list[dict] = []
    for rho in cfg["sweep"]["offered_loads"]:
        arrival_rate = rho / e_service
        # Fresh cluster and an independent seed per offered-load point.
        point_seed = cfg["seed"] + int(round(rho * 1000))
        cluster = build_cluster(cfg["cluster"], service)
        seed_mgr = SeedManager(point_seed)
        run_cfg = RunConfig(
            horizon_s=cfg["run"]["horizon_s"],
            warmup_s=cfg["run"]["warmup_s"],
            seed=point_seed,
        )
        policy = FCFSPolicy()
        policy.attach_kv_rate(service.kv_mib_per_token)
        workload = mm1_workload(
            seed_mgr=seed_mgr,
            arrival_rate_per_s=arrival_rate,
            mean_output_tokens=wcfg["mean_output_tokens"],
            prefill_tokens=wcfg["prefill_tokens"],
            horizon_s=run_cfg.horizon_s,
        )
        result = run_simulation(cluster, policy, workload, run_cfg)
        points.append({
            "offered_load": rho,
            "utilization": result.mean_utilization,
            "throughput": result.throughput_per_s,
            "completions": result.completions,
        })
    return points


def main() -> None:
    points = run_utilization_curve()
    print("=== SG1 sanity: utilization vs offered load (M/M/1) ===")
    print(f"  {'offered rho':>12} {'utilization':>12} {'rel err':>10} {'completions':>12}")
    for p in points:
        rel = abs(p["utilization"] - p["offered_load"]) / p["offered_load"]
        print(f"  {p['offered_load']:>12.3f} {p['utilization']:>12.4f} "
              f"{rel:>9.2%} {p['completions']:>12}")
    utils = [p["utilization"] for p in points]
    monotonic = all(b >= a - 1e-9 for a, b in zip(utils, utils[1:]))
    print(f"  monotic non-decreasing    : {monotonic}")
    print(f"  all below 1.0             : {all(u < 1.0 for u in utils)}")


if __name__ == "__main__":
    main()
