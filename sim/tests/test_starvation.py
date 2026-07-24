"""Regression: interactive-priority baselines starve long agentic flows (H3 zero).

Locks the finding in docs/H3_ZERO_CHECK.md: niyama_style and vllm_style completing
0.000 compliant flows under runaway injection is GENUINE -- under saturating
interactive load those policies never schedule long agentic flows, so they starve at
admission, not through preemption. This is real policy behavior, not a measurement
bug, and it is exactly the failure mode the reservation discipline prevents.
"""

from sim.core.engine import SimulationEngine
from sim.core.orchestration import (
    build_cluster,
    build_policy,
    load_degradation_catalog,
    load_service_model,
    load_yaml,
)
from sim.core.orchestration import CONFIG_DIR
from sim.core.types import RunConfig
from sim.metrics.collectors import MetricsCollector
from sim.tuning.harness import apply_overrides
from sim.workload.generator import WorkloadGenerator

_MIX = 0.5          # agentic-heavy, so there ARE long flows to starve
_LOAD = 1.2         # saturating: interactive traffic fills the cluster
_HORIZON = 200.0


def _agentic_fates(policy_name: str):
    service = load_service_model()
    workload = apply_overrides(load_yaml(CONFIG_DIR / "workload.yaml"), {
        "mix.agentic_token_share": _MIX,
        "arrivals.base_rate_per_s": _LOAD * 8.0 * 4})
    cluster = build_cluster(
        {"num_devices": 4, "device": {"hbm_capacity_gib": 80.0,
                                      "max_concurrent_decodes": 16}}, service)
    metrics = MetricsCollector(_HORIZON, 20.0, 4)
    engine = SimulationEngine(
        cluster, build_policy(policy_name, service),
        iter(WorkloadGenerator(workload, seed=1, horizon_s=_HORIZON)),
        metrics, RunConfig(horizon_s=_HORIZON, warmup_s=20.0, seed=1,
                           tick_interval_s=0.5),
        degradation_catalog=load_degradation_catalog())
    engine.run()
    agentic = [f for f in engine._flows.values() if f.cls == "agentic"]
    started = sum(1 for f in agentic if f.invocations[0].start_time is not None)
    return len(agentic), started


def test_interactive_priority_baselines_starve_agentic_flows():
    """niyama and vllm schedule almost no agentic flows under saturating load."""
    for pol in ("niyama_style", "vllm_style"):
        arrived, started = _agentic_fates(pol)
        assert arrived > 20, f"{pol}: need agentic flows present ({arrived})"
        # Genuine starvation: the vast majority never get a slot. (The exact fraction
        # varies with tuned params -- 1/107 tuned, ~11/107 at defaults -- but it is
        # always a small minority, versus reservation's ~100 percent.)
        assert started <= 0.15 * arrived, (
            f"{pol} scheduled {started}/{arrived} agentic flows -- the 0.000 "
            f"compliant-completion finding requires near-total scheduling starvation")


def test_reservation_does_not_starve_agentic_flows():
    """The discipline schedules the agentic flows the baselines starve."""
    arrived, started = _agentic_fates("reservation")
    assert started >= 0.9 * arrived, (
        f"reservation scheduled only {started}/{arrived} agentic flows -- its "
        f"reservations are supposed to protect exactly these from starvation")
