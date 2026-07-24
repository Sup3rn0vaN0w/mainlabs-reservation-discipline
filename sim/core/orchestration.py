"""Run orchestration: assemble a simulation from config and execute it.

Loads YAML configs, builds the service model, cluster, metrics, and run config,
wires them to a policy and a workload source, runs the engine, and returns the
RunResult. Kept free of any specific workload construction -- the caller supplies
the workload iterator (SG1 sanity workloads live in sim/sanity/; the Section 5
generators arrive at SG2).

House style: hyphens only (D-026).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..cluster.cluster import Cluster
from ..cluster.service_model import ServiceModel
from ..metrics.collectors import MetricsCollector, RunResult
from ..scheduler.interface import Policy
from .engine import SimulationEngine, WorkloadSource
from .types import DegradeSpec, RunConfig

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"


def load_yaml(path: str | Path) -> dict:
    """Parse a YAML file into a dict."""
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_service_model(path: str | Path | None = None) -> ServiceModel:
    """Load the service-model constants (configs/service_model.yaml by default)."""
    if path is None:
        path = CONFIG_DIR / "service_model.yaml"
    return ServiceModel.from_config(load_yaml(path))


def build_cluster(cluster_cfg: dict, service: ServiceModel) -> Cluster:
    """Build a Cluster from a resolved `cluster:` config block."""
    return Cluster.from_config(cluster_cfg, service)


def load_policy_config(path: str | Path | None = None) -> dict:
    """Load the policy parameter file (configs/policies.yaml by default)."""
    if path is None:
        path = CONFIG_DIR / "policies.yaml"
    return load_yaml(path)


def build_policy(name: str, service: ServiceModel,
                 policy_cfg: dict | None = None) -> Policy:
    """Construct a policy by name and attach the KV rate it needs for fit checks.

    Policies are plug-ins behind one interface (brief Section 4) -- adding one means
    adding a case here and nothing else in the engine.
    """
    # Imported here so the policy modules can import from core without a cycle.
    from ..scheduler.policies.concur_style import ConcurStylePolicy
    from ..scheduler.policies.fastserve_mlfq import FastServeMLFQPolicy
    from ..scheduler.policies.fcfs import FCFSPolicy
    from ..scheduler.policies.mars_style import MarsStylePolicy
    from ..scheduler.policies.niyama_style import NiyamaStylePolicy
    from ..scheduler.policies.reservation import ReservationPolicy
    from ..scheduler.policies.vllm_style import VLLMStylePolicy

    if policy_cfg is None:
        policy_cfg = load_policy_config()

    if name == "fcfs":
        policy: Policy = FCFSPolicy()
    elif name == "vllm_style":
        policy = VLLMStylePolicy.from_config(policy_cfg["vllm_style"])
    elif name == "fastserve_mlfq":
        policy = FastServeMLFQPolicy.from_config(policy_cfg["fastserve_mlfq"])
    elif name == "niyama_style":
        policy = NiyamaStylePolicy.from_config(policy_cfg["niyama_style"])
    elif name == "concur_style":
        policy = ConcurStylePolicy.from_config(policy_cfg["concur_style"])
    elif name == "mars_style":
        policy = MarsStylePolicy.from_config(policy_cfg["mars_style"])
    elif name == "reservation":
        policy = ReservationPolicy.from_config(policy_cfg["reservation"])
    else:
        raise ValueError(f"unknown policy: {name!r}")

    policy.attach_kv_rate(service.kv_mib_per_token)
    return policy


def load_degradation_catalog(policy_cfg: dict | None = None) -> dict[str, DegradeSpec]:
    """Build the degradation catalog the engine applies (spec Section 10)."""
    if policy_cfg is None:
        policy_cfg = load_policy_config()
    return {
        name: DegradeSpec(name=name,
                          token_scale=float(spec["token_scale"]),
                          quality_cost=float(spec["quality_cost"]))
        for name, spec in policy_cfg.get("degradation_actions", {}).items()
    }


def run_simulation(
    cluster: Cluster,
    policy: Policy,
    workload: WorkloadSource,
    config: RunConfig,
    degradation_catalog: dict[str, DegradeSpec] | None = None,
) -> RunResult:
    """Construct the engine, run to the horizon, and return the RunResult."""
    metrics = MetricsCollector(
        horizon_s=config.horizon_s,
        warmup_s=config.warmup_s,
        num_devices=cluster.num_devices,
    )
    engine = SimulationEngine(cluster, policy, workload, metrics, config,
                              degradation_catalog=degradation_catalog)
    engine.run()
    return metrics.finalize(engine.env.now)
