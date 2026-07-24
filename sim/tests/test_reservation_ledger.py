"""SG4 ledger invariants (brief Section 6).

The three the gate names, each pinned against a hand-computable scenario:

  1. NO OVER-COMMIT -- a pin that does not fit is a hard error, not a silent
     over-subscription. The engine enforces this; the policy is not trusted.
  2. PIN ACCOUNTING EXACT ACROSS IDLE GAPS -- a reservation holds its slot through
     tool-time, and the stranding that produces is measured exactly.
  3. BUDGET METERING EXACT AT DECODE -- a flow generates exactly its budget, not one
     token more, and is then abandoned.

Plus the guarantee itself: the engine refuses to preempt a reserved flow.
"""

import pytest

from sim.cluster.device import Device
from sim.core.orchestration import (
    build_cluster,
    load_degradation_catalog,
    load_policy_config,
    load_service_model,
    run_simulation,
)
from sim.core.types import (
    Flow,
    Invocation,
    PinMode,
    PreemptMode,
    Reservation,
    RunConfig,
)
from sim.scheduler.interface import Admit, NoOp, Policy, Preempt, Schedule
from sim.scheduler.policies.reservation import ReservationPolicy

_SERVICE_S = 1.0     # 100 output tokens at the batch-1 rate (100 tok/s)
_GAP_S = 10.0
_HORIZON_S = 100.0


def _cluster(service, devices=1, slots=8, hbm=80.0):
    return build_cluster(
        {"num_devices": devices,
         "device": {"hbm_capacity_gib": hbm, "max_concurrent_decodes": slots}},
        service,
    )


def _res_policy(service, **overrides):
    cfg = dict(load_policy_config()["reservation"])
    cfg.update(overrides)
    policy = ReservationPolicy.from_config(cfg)
    policy.attach_kv_rate(service.kv_mib_per_token)
    return policy


def _reserved_flow_with_gap(flow_id=0, cls="agentic"):
    """One reserved flow: invocation -> 10 s tool-time -> invocation."""
    i0 = Invocation(flow_id=flow_id, index=0, prefill_tokens=0, output_tokens=100,
                    gap_after_s=_GAP_S)
    i1 = Invocation(flow_id=flow_id, index=1, prefill_tokens=0, output_tokens=100)
    return Flow(flow_id=flow_id, arrival_time=0.0, cls=cls, invocations=[i0, i1])


# --- invariant 1: no over-commit --------------------------------------------------

def test_pin_that_does_not_fit_is_a_hard_error():
    """HBM over-commit is refused, loudly."""
    service = load_service_model()
    dev = Device(0, hbm_capacity_gib=10.0, max_concurrent_decodes=8, service=service)
    dev.pin_flow(flow_id=1, hbm_gib=8.0)
    with pytest.raises(RuntimeError, match="over-commit"):
        dev.pin_flow(flow_id=2, hbm_gib=8.0)   # 16 > 10 GiB


def test_pin_beyond_slot_count_is_a_hard_error():
    """Compute over-commit is refused too: pins hold slots, even while idle."""
    service = load_service_model()
    dev = Device(0, hbm_capacity_gib=80.0, max_concurrent_decodes=2, service=service)
    dev.pin_flow(flow_id=1, hbm_gib=1.0)
    dev.pin_flow(flow_id=2, hbm_gib=1.0)
    with pytest.raises(RuntimeError, match="over-commit"):
        dev.pin_flow(flow_id=3, hbm_gib=1.0)


def test_a_pin_holds_its_slot_even_with_nothing_resident():
    service = load_service_model()
    dev = Device(0, hbm_capacity_gib=80.0, max_concurrent_decodes=4, service=service)
    dev.pin_flow(flow_id=1, hbm_gib=5.0)
    assert dev.slots_used == 1, "an idle pin still occupies its slot -- that is the point"
    assert dev.hbm_used_gib == 5.0
    assert dev.num_resident == 0, "but nothing is physically decoding"


# --- invariant 2: pin accounting exact across idle gaps ---------------------------

def test_reserved_idle_is_measured_exactly_across_the_gap():
    """The pin is held for the whole flow; only the decode seconds are 'used'.

    Timeline: 1 s decode -> 10 s tool-time -> 1 s decode. The reservation is held
    for all 12 s but occupied for only 2 s, so 10/12 of the reserved capacity is
    stranded. That number is the whole economic argument, so it must be exact.
    """
    service = load_service_model()
    result = run_simulation(
        _cluster(service),
        _res_policy(service),
        iter([(0.0, _reserved_flow_with_gap())]),
        RunConfig(horizon_s=_HORIZON_S, warmup_s=0.0),
        degradation_catalog=load_degradation_catalog(),
    )

    assert result.reservations == 1, "the flow should have been reserved"
    assert result.completions == 1

    pin_lifetime = 2 * _SERVICE_S + _GAP_S          # 12 s
    assert abs(result.pinned_slot_seconds - pin_lifetime) < 0.05
    assert abs(result.pinned_active_seconds - 2 * _SERVICE_S) < 0.05

    expected_idle = _GAP_S / pin_lifetime            # 10/12 = 0.8333
    assert abs(result.reserved_idle_fraction - expected_idle) < 0.01, (
        f"reserved-idle {result.reserved_idle_fraction:.4f}, "
        f"expected {expected_idle:.4f}")


# --- invariant 3: budget metering exact at decode ----------------------------------

def test_flow_generates_exactly_its_budget_then_is_abandoned():
    """A 1000-token flow on a 250-token budget must emit 250 tokens. Not 251."""
    service = load_service_model()
    budget = 250.0
    flow = Flow(flow_id=0, arrival_time=0.0, cls="agentic",
                invocations=[Invocation(flow_id=0, index=0, prefill_tokens=0,
                                        output_tokens=1000)])
    policy = _res_policy(service, budget={
        "enabled": True,
        "tokens_by_class": {"agentic": budget},
        "per_request_cap_tokens": 4000,
    })
    result = run_simulation(
        _cluster(service), policy, iter([(0.0, flow)]),
        RunConfig(horizon_s=_HORIZON_S, warmup_s=0.0),
        degradation_catalog=load_degradation_catalog(),
    )

    assert result.abandonments == 1
    assert result.completions == 0, "the flow never finished -- it ran out of budget"
    assert flow.abandoned
    generated = flow.invocations[0].generated_tokens
    assert abs(generated - budget) < 1e-6, (
        f"generated {generated} tokens on a {budget}-token budget -- metering is not exact")
    assert abs(flow.budget_remaining) < 1e-6


def test_budget_spans_invocations_not_requests():
    """The budget is CROSS-invocation: three steps share one pool.

    Three 100-token steps on a 250-token budget: the first two run, the third is cut
    off after 50 tokens. A per-request cap would have let all three run in full --
    which is exactly the hole the discipline closes (H3).
    """
    service = load_service_model()
    invs = [Invocation(flow_id=0, index=j, prefill_tokens=0, output_tokens=100)
            for j in range(3)]
    flow = Flow(flow_id=0, arrival_time=0.0, cls="agentic", invocations=invs)
    policy = _res_policy(service, budget={
        "enabled": True,
        "tokens_by_class": {"agentic": 250.0},
        "per_request_cap_tokens": 4000,
    })
    result = run_simulation(
        _cluster(service), policy, iter([(0.0, flow)]),
        RunConfig(horizon_s=_HORIZON_S, warmup_s=0.0),
        degradation_catalog=load_degradation_catalog(),
    )

    assert result.abandonments == 1
    total = sum(i.generated_tokens for i in invs)
    assert abs(total - 250.0) < 1e-6, f"flow drew {total} tokens against a 250 budget"
    assert abs(invs[0].generated_tokens - 100) < 1e-6
    assert abs(invs[1].generated_tokens - 100) < 1e-6
    assert abs(invs[2].generated_tokens - 50) < 1e-6, "third step cut off mid-flight"


# --- the guarantee ------------------------------------------------------------------

class _PreemptsAReservation(Policy):
    """A policy that admits with a reservation and then tries to evict it."""

    name = "malicious"

    def __init__(self, non_preemptible: bool):
        self.non_preemptible = non_preemptible
        self._victim = None
        self._tried = False   # attempt the eviction exactly once

    def on_event(self, event, state):
        from sim.scheduler.interface import FlowArrival, InvocationReady, Tick

        if isinstance(event, FlowArrival):
            res = Reservation(
                flow_id=event.flow.flow_id,
                hbm_gib=self.kv_gib(event.flow.invocations[0]),
                token_budget=float("inf"),
                pin_mode=PinMode.STRICT,
                non_preemptible=self.non_preemptible,
                device_id=0,
            )
            return [Admit(event.flow, cls="reserved", reservation=res)]
        if isinstance(event, InvocationReady):
            self._victim = event.invocation
            return [Schedule(event.invocation, 0)]
        if isinstance(event, Tick) and self._victim is not None and not self._tried:
            self._tried = True
            victim, self._victim = self._victim, None
            return [Preempt(victim, PreemptMode.RECOMPUTE)]
        return [NoOp()]


def _run_malicious(non_preemptible: bool):
    service = load_service_model()
    policy = _PreemptsAReservation(non_preemptible=non_preemptible)
    policy.attach_kv_rate(service.kv_mib_per_token)
    flow = Flow(flow_id=0, arrival_time=0.0, cls="agentic",
                invocations=[Invocation(flow_id=0, index=0, prefill_tokens=0,
                                        output_tokens=1000)])
    return run_simulation(
        _cluster(service), policy, iter([(0.0, flow)]),
        RunConfig(horizon_s=50.0, warmup_s=0.0, tick_interval_s=1.0),
        degradation_catalog=load_degradation_catalog(),
    )


def test_engine_refuses_to_preempt_a_reserved_flow():
    """The non-preemption guarantee is enforced by the ENGINE, not trusted to policy."""
    with pytest.raises(RuntimeError, match="non-preemption guarantee"):
        _run_malicious(non_preemptible=True)


def test_ablation_a2_makes_reservations_preemptible():
    """A2 turns the guarantee off -- and then the same preemption is allowed."""
    result = _run_malicious(non_preemptible=False)
    assert result.preemptions == 1
