"""SG3: inter-invocation tool-time (spec Section 5 gaps) is honored by the engine.

The gap is where a flow sits idle between invocations. It is the mechanism behind
capacity stranding -- a reservation policy holds capacity across it (SG4) while a
work-conserving policy does not. These tests pin the engine's half: the next
invocation does not become ready until the gap has elapsed, and the device is
genuinely idle meanwhile.
"""

from sim.core.orchestration import (
    build_cluster,
    build_policy,
    load_service_model,
    run_simulation,
)
from sim.core.types import Flow, Invocation, RunConfig

# 100 output tokens at the batch-1 decode rate (100 tok/s) = 1.0 s of service.
_SERVICE_S = 1.0
_GAP_S = 10.0
_HORIZON_S = 100.0


def _single_device(service):
    return build_cluster(
        {"num_devices": 1,
         "device": {"hbm_capacity_gib": 80.0, "max_concurrent_decodes": 8}},
        service,
    )


def _two_step_flow_with_gap():
    """One flow: invocation -> 10 s of tool-time -> invocation."""
    inv0 = Invocation(flow_id=0, index=0, prefill_tokens=0, output_tokens=100,
                      gap_after_s=_GAP_S)
    inv1 = Invocation(flow_id=0, index=1, prefill_tokens=0, output_tokens=100)
    return Flow(flow_id=0, arrival_time=0.0, invocations=[inv0, inv1])


def test_gap_delays_the_next_invocation():
    service = load_service_model()
    flow = _two_step_flow_with_gap()
    result = run_simulation(
        _single_device(service),
        build_policy("fcfs", service),
        iter([(0.0, flow)]),
        RunConfig(horizon_s=_HORIZON_S, warmup_s=0.0),
    )

    assert result.completions == 1
    # Sojourn must span both invocations AND the tool-time between them.
    expected = 2 * _SERVICE_S + _GAP_S
    assert abs(result.mean_sojourn_s - expected) < 0.05, (
        f"sojourn {result.mean_sojourn_s:.3f}s, expected ~{expected}s "
        f"(2 x {_SERVICE_S}s service + {_GAP_S}s gap)")
    # The second invocation cannot have started before the gap elapsed.
    assert flow.invocations[1].start_time >= _SERVICE_S + _GAP_S - 1e-6


def test_device_is_idle_during_the_gap():
    """The flow holds no device across its tool-time (work-conserving policy)."""
    service = load_service_model()
    result = run_simulation(
        _single_device(service),
        build_policy("fcfs", service),
        iter([(0.0, _two_step_flow_with_gap())]),
        RunConfig(horizon_s=_HORIZON_S, warmup_s=0.0),
    )

    # Busy only for the two invocations; the 10 s gap is idle time.
    expected_busy = 2 * _SERVICE_S
    busy = result.mean_utilization * _HORIZON_S
    assert abs(busy - expected_busy) < 0.05, (
        f"device busy {busy:.3f}s, expected ~{expected_busy}s -- the gap must be idle")
