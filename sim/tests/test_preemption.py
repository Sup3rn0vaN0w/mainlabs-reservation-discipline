"""SG3: preemption-cost mechanics (spec Section 4).

The engine owns what preemption COSTS; the policy only chooses the mode. These
tests pin both variants against a hand-computable scenario, and pin the invariant
that matters most: preemption must never lose or invent generated tokens.

Scenario (single decode slot, so contention is forced):
  * flow A, agentic, 1000 output tokens -> 10 s of decode, arrives t=0
  * flow B, interactive, 100 output tokens, arrives t=1.0 -- mid-flight for A

vLLM-style ranks interactive above agentic, so B preempts A. At t=1.0 A has
generated 100 of its 1000 tokens, so its materialized context is 100 tokens.
  * RECOMPUTE -> that 100-token context is re-prefilled on resume (wasted work)
  * SWAP      -> nothing is re-prefilled; the KV is copied out and back instead
"""

from sim.core.orchestration import (
    build_cluster,
    load_service_model,
    run_simulation,
)
from sim.core.types import Flow, Invocation, PreemptMode, RunConfig
from sim.scheduler.policies.vllm_style import VLLMStylePolicy

_CLASS_PRIORITY = {"interactive": 0, "agentic": 1}
_A_TOKENS = 1000          # 10 s of decode at 100 tok/s
_B_TOKENS = 100           # 1 s
_B_ARRIVAL_S = 1.0        # A has generated 100 tokens by now
_EXPECTED_MATERIALIZED = 100


def _one_slot_cluster(service):
    """Single device, single decode slot -> B can only run if A is evicted."""
    return build_cluster(
        {"num_devices": 1,
         "device": {"hbm_capacity_gib": 80.0, "max_concurrent_decodes": 1}},
        service,
    )


def _contending_flows():
    a = Flow(flow_id=0, arrival_time=0.0, cls="agentic",
             invocations=[Invocation(flow_id=0, index=0, prefill_tokens=0,
                                     output_tokens=_A_TOKENS)])
    b = Flow(flow_id=1, arrival_time=_B_ARRIVAL_S, cls="interactive",
             invocations=[Invocation(flow_id=1, index=0, prefill_tokens=0,
                                     output_tokens=_B_TOKENS)])
    return a, b


def _run(mode: PreemptMode):
    service = load_service_model()
    policy = VLLMStylePolicy(preempt_mode=mode, class_priority=_CLASS_PRIORITY)
    policy.attach_kv_rate(service.kv_mib_per_token)
    a, b = _contending_flows()
    result = run_simulation(
        _one_slot_cluster(service),
        policy,
        iter([(0.0, a), (_B_ARRIVAL_S, b)]),
        RunConfig(horizon_s=200.0, warmup_s=0.0),
    )
    return result, a, b


def test_higher_priority_preempts_lower():
    result, a, b = _run(PreemptMode.RECOMPUTE)
    assert result.preemptions == 1, "interactive B should evict agentic A exactly once"
    assert a.invocations[0].preemptions == 1
    assert b.invocations[0].preemptions == 0, "the preemptor must not be preempted"


def test_recompute_reprefills_the_materialized_context():
    result, _, _ = _run(PreemptMode.RECOMPUTE)
    assert result.recompute_preemptions == 1
    assert result.swap_preemptions == 0
    # A had generated 100 of 1000 tokens when evicted -> 100 tokens re-prefilled.
    assert abs(result.wasted_prefill_tokens - _EXPECTED_MATERIALIZED) < 1e-6, (
        f"re-prefilled {result.wasted_prefill_tokens} tokens, "
        f"expected {_EXPECTED_MATERIALIZED}")


def test_swap_pays_bandwidth_not_recompute():
    result, _, _ = _run(PreemptMode.SWAP)
    assert result.swap_preemptions == 1
    assert result.recompute_preemptions == 0
    assert result.wasted_prefill_tokens == 0.0, (
        "swap copies the KV to host and back -- it must not re-prefill anything")


def test_preemption_never_loses_or_invents_tokens():
    """The load-bearing invariant: every token is generated exactly once."""
    for mode in (PreemptMode.RECOMPUTE, PreemptMode.SWAP):
        result, a, b = _run(mode)
        assert result.completions == 2, f"{mode}: both flows must finish"
        for flow, expected in ((a, _A_TOKENS), (b, _B_TOKENS)):
            inv = flow.invocations[0]
            assert inv.decode_remaining == 0.0
            assert abs(inv.generated_tokens - expected) < 1e-6, (
                f"{mode}: flow {flow.flow_id} generated {inv.generated_tokens} "
                f"tokens, expected exactly {expected}")


def test_recompute_costs_more_time_than_swap_here():
    """With these constants, re-prefilling beats a host round-trip -- or does not.

    This does not assert which mode is faster in general (that is exactly what the
    experiment is for). It asserts only that the two modes are priced DIFFERENTLY by
    the engine, i.e. the variant choice actually changes the physics.
    """
    recompute, _, _ = _run(PreemptMode.RECOMPUTE)
    swap, _, _ = _run(PreemptMode.SWAP)
    assert recompute.mean_sojourn_s != swap.mean_sojourn_s, (
        "recompute and swap must not price preemption identically")
