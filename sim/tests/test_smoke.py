"""SG1 smoke test: multi-invocation flows and batched (batch > 1) decode.

The sanity checks exercise only the single-server (batch cap 1) path. This drives
multi-invocation flows across a multi-device cluster with a decode batch larger
than one, so the flow-advance path and processor-sharing decode at batch > 1 are
covered end to end.
"""

from sim.core.orchestration import build_cluster, load_service_model, run_simulation
from sim.core.types import Flow, Invocation, RunConfig
from sim.scheduler.policies.fcfs import FCFSPolicy


# 20 flows arriving in a tight burst, 2 sequential invocations each. The burst
# puts more than one device's decode-batch-cap of invocations in flight at once, so
# first-fit FCFS is forced to spill onto the second device -- exercising multi-
# device placement AND processor-sharing decode at batch > 1.
_BURST_FLOWS = 20
_INVS_PER_FLOW = 2
_DECODE_BATCH_CAP = 16


def _burst_workload(num_flows: int = _BURST_FLOWS, invs_per_flow: int = _INVS_PER_FLOW):
    t = 0.0
    for i in range(num_flows):
        t += 0.001  # near-simultaneous arrivals
        invs = [
            Invocation(flow_id=i, index=j, prefill_tokens=8, output_tokens=300)
            for j in range(invs_per_flow)
        ]
        yield (t, Flow(flow_id=i, arrival_time=t, invocations=invs))


def test_smoke_multi_invocation_batched():
    service = load_service_model()
    cluster = build_cluster(
        {"num_devices": 2,
         "device": {"hbm_capacity_gib": 80.0,
                    "max_concurrent_decodes": _DECODE_BATCH_CAP}},
        service,
    )
    policy = FCFSPolicy()
    policy.attach_kv_rate(service.kv_mib_per_token)
    cfg = RunConfig(horizon_s=10_000.0, warmup_s=0.0, seed=1)

    result = run_simulation(cluster, policy, _burst_workload(), cfg)

    # Every flow arrives, is admitted, and completes within the generous horizon.
    assert result.arrivals == _BURST_FLOWS
    assert result.admissions == _BURST_FLOWS
    assert result.completions == _BURST_FLOWS
    # The burst (20 invocations) exceeds one device's batch cap (16), so FCFS
    # first-fit must use both devices.
    assert all(u > 0.0 for u in result.per_device_utilization)


def test_determinism_same_seed_reproducible():
    """Same seed -> byte-identical result (brief Section 4: exact reproducibility)."""
    from sim.sanity.littles_law import run_littles_law

    r1 = run_littles_law()
    r2 = run_littles_law()
    assert r1["L"] == r2["L"]
    assert r1["W"] == r2["W"]
    assert r1["completions"] == r2["completions"]
