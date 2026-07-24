"""SG3 gate test: same cell, same seed -> every policy sees a byte-identical workload.

This is the fairness guarantee made checkable (brief Section 4). Cross-policy
comparisons in the experiment matrix are PAIRED: within a cell, each policy must be
driven by the identical workload realization, so any difference in outcome is
attributable to the policy and not to a different draw of the workload.

The test runs the real spec Section 5 workload (not a sanity stub) through every
policy, records the exact stream each engine consumed, and hashes it. All hashes
must match. It also checks the corollary that makes the test meaningful: the
policies must actually BEHAVE differently on that identical stream.
"""

import hashlib

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
from sim.workload.generator import WorkloadGenerator

# Our policy sits in the same list as the baselines on purpose: the paired
# comparison is only sound if the reservation discipline is driven by the SAME
# workload realization the baselines see, with no special treatment.
POLICIES = ["fcfs", "vllm_style", "niyama_style", "concur_style",
            "fastserve_mlfq", "mars_style", "reservation"]

_SEED = 12345
_HORIZON_S = 60.0
_TICK_S = 0.5

# Deliberately tight: contention is what makes the preemptive policies diverge.
_CLUSTER = {"num_devices": 2,
            "device": {"hbm_capacity_gib": 80.0, "max_concurrent_decodes": 8}}


def _snapshot(arrival_time, flow):
    """Freeze a flow's DEMAND at the moment the engine consumed it.

    This must be taken at consumption time, not after the run: a policy is allowed
    to change a flow's future work (the reservation discipline degrades flows, which
    shrinks their remaining output tokens). Hashing the objects afterwards would
    compare post-policy state and report a difference that is a legitimate policy
    effect, not a workload difference. What must be identical across policies is the
    stream the engine was HANDED.
    """
    return (
        flow.flow_id,
        flow.cls,
        flow.is_runaway,
        round(arrival_time, 6),
        tuple((inv.index, inv.prefill_tokens, inv.output_tokens,
               inv.context_tokens, round(inv.gap_after_s, 6))
              for inv in flow.invocations),
    )


def _digest(snapshots):
    h = hashlib.sha256()
    for snap in snapshots:
        h.update(repr(snap).encode("utf-8"))
    return h.hexdigest()


def _recording_source(generator, sink):
    """Yield the workload through, recording exactly what the engine consumed."""
    for arrival_time, flow in generator:
        sink.append(_snapshot(arrival_time, flow))
        yield (arrival_time, flow)


def _run_policy(name, service, workload_cfg):
    # A fresh generator per policy, same seed -> it must replay the same workload.
    generator = WorkloadGenerator(workload_cfg, seed=_SEED, horizon_s=_HORIZON_S)
    consumed = []
    result = run_simulation(
        build_cluster(_CLUSTER, service),
        build_policy(name, service),
        _recording_source(generator, consumed),
        RunConfig(horizon_s=_HORIZON_S, warmup_s=0.0, seed=_SEED,
                  tick_interval_s=_TICK_S),
        degradation_catalog=load_degradation_catalog(),
    )
    return result, consumed


@pytest.fixture(scope="module")
def runs():
    service = load_service_model()
    workload_cfg = load_yaml(CONFIG_DIR / "workload.yaml")
    return {name: _run_policy(name, service, workload_cfg) for name in POLICIES}


def test_every_policy_saw_the_identical_workload(runs):
    digests = {}
    for name, (_, consumed) in runs.items():
        assert consumed, f"{name} consumed no flows -- the test proves nothing"
        digests[name] = _digest(consumed)

    distinct = set(digests.values())
    assert len(distinct) == 1, (
        "policies were driven by DIFFERENT workloads -- the paired comparison is "
        f"broken: {digests}")


def test_all_policies_saw_the_same_flow_count(runs):
    counts = {name: len(consumed) for name, (_, consumed) in runs.items()}
    assert len(set(counts.values())) == 1, f"flow counts diverged: {counts}"


def test_policies_actually_differ_on_that_workload(runs):
    """Guards against a vacuous pass: identical input must still drive distinct behavior."""
    # The work-conserving floor never preempts.
    fcfs_result, _ = runs["fcfs"]
    assert fcfs_result.preemptions == 0, "FCFS must never preempt"

    # At least one preemptive policy must actually preempt under this contention,
    # otherwise the preemption mechanics are untested by this cell.
    preemptive = [runs[n][0].preemptions for n in ("vllm_style", "fastserve_mlfq")]
    assert any(p > 0 for p in preemptive), (
        "no preemptive policy preempted -- raise the load or shrink the cluster, "
        "this cell does not exercise preemption")

    # Only the reservation discipline pins capacity.
    assert runs["reservation"][0].reservations > 0
    for name in ("fcfs", "vllm_style", "niyama_style", "concur_style",
                 "fastserve_mlfq"):
        assert runs[name][0].reservations == 0, f"{name} must not reserve"

    # Only CONCUR gates admission: it is the one policy that refuses work outright.
    assert runs["concur_style"][0].rejections > 0, (
        "CONCUR's AIMD window should have refused some work under this load")
