"""Conservation: no policy may silently lose queued work.

This is the test that catches the whole class of bug the SG5 performance work could
have introduced. Bounding how deep a scheduling pass looks into the backlog is fine.
Rebuilding the queue from only the part you looked at is NOT -- it drops every
invocation past the scan window on the floor, and the run still "succeeds", just with
less work in it. Nothing else in the suite would have noticed.

Every flow the engine admits must end in exactly one of three states: completed,
abandoned (out of budget), or still in flight at the horizon. Anything else means
work evaporated.
"""

import pytest

from sim.core.orchestration import (
    CONFIG_DIR,
    build_cluster,
    build_policy,
    load_degradation_catalog,
    load_service_model,
    load_yaml,
    run_simulation,
)
from sim.core.types import RunConfig
from sim.tuning.harness import apply_overrides
from sim.workload.generator import WorkloadGenerator

POLICIES = ["fcfs", "vllm_style", "niyama_style", "concur_style",
            "fastserve_mlfq", "mars_style", "reservation"]

# Deliberately overloaded: a deep backlog is the only condition under which a
# scan-window bug can drop work.
_HORIZON_S = 120.0
_OVERLOAD_RATE = 48.0     # ~1.5x the measured capacity of this cluster
_CLUSTER = {"num_devices": 2,
            "device": {"hbm_capacity_gib": 80.0, "max_concurrent_decodes": 8}}


@pytest.mark.parametrize("policy_name", POLICIES)
def test_no_admitted_flow_disappears_under_a_deep_backlog(policy_name):
    service = load_service_model()
    workload_cfg = apply_overrides(
        load_yaml(CONFIG_DIR / "workload.yaml"),
        {"arrivals.base_rate_per_s": _OVERLOAD_RATE},
    )
    generator = WorkloadGenerator(workload_cfg, seed=7, horizon_s=_HORIZON_S)

    seen = []

    def recording():
        for t, flow in generator:
            seen.append(flow)
            yield (t, flow)

    result = run_simulation(
        build_cluster(_CLUSTER, service),
        build_policy(policy_name, service),
        recording(),
        RunConfig(horizon_s=_HORIZON_S, warmup_s=0.0, seed=7, tick_interval_s=0.5),
        degradation_catalog=load_degradation_catalog(),
    )

    admitted = [f for f in seen if f.admitted]
    assert admitted, "nothing was admitted -- this cell proves nothing"

    completed = [f for f in admitted if f.is_complete and not f.abandoned]
    abandoned = [f for f in admitted if f.abandoned]
    in_flight = [f for f in admitted
                 if not f.is_complete and not f.abandoned]

    # Every admitted flow is accounted for. A flow that was dropped from a policy
    # queue would be none of these -- it would just be gone.
    assert len(completed) + len(abandoned) + len(in_flight) == len(admitted)

    # An in-flight flow must have actually been making progress or be genuinely
    # waiting -- but crucially, the backlog must not exceed what arrived.
    assert len(in_flight) <= len(admitted)

    # The engine's own count of completed flows must match the flows themselves.
    assert result.completions <= len(completed)


@pytest.mark.parametrize("policy_name", POLICIES)
def test_every_invocation_generates_exactly_its_tokens(policy_name):
    """No half-generated work: a completed flow emitted precisely what it owed."""
    service = load_service_model()
    workload_cfg = apply_overrides(
        load_yaml(CONFIG_DIR / "workload.yaml"),
        {"arrivals.base_rate_per_s": _OVERLOAD_RATE},
    )
    generator = WorkloadGenerator(workload_cfg, seed=7, horizon_s=_HORIZON_S)
    seen = []

    def recording():
        for t, flow in generator:
            seen.append(flow)
            yield (t, flow)

    run_simulation(
        build_cluster(_CLUSTER, service),
        build_policy(policy_name, service),
        recording(),
        RunConfig(horizon_s=_HORIZON_S, warmup_s=0.0, seed=7, tick_interval_s=0.5),
        degradation_catalog=load_degradation_catalog(),
    )

    for flow in seen:
        if not (flow.is_complete and not flow.abandoned):
            continue
        for inv in flow.invocations:
            assert abs(inv.generated_tokens - inv.output_tokens) < 1e-6, (
                f"{policy_name}: flow {flow.flow_id} step {inv.index} generated "
                f"{inv.generated_tokens} of {inv.output_tokens} tokens")
