"""SG4: the reservation mechanism's moving parts.

Covers the two pin embodiments (ablation A4), the cross-invocation budget as the
containment for runaway flows (H3), and headroom-keyed probabilistic degradation.
"""

import pytest

from sim.cluster.device import Device
from sim.core.orchestration import (
    CONFIG_DIR,
    build_cluster,
    build_policy,
    load_degradation_catalog,
    load_policy_config,
    load_service_model,
    load_yaml,
    run_simulation,
)
from sim.core.types import Flow, Invocation, RunConfig
from sim.scheduler.policies.reservation import ReservationPolicy
from sim.workload.generator import WorkloadGenerator

_HORIZON_S = 200.0
_GAP_S = 10.0


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


# --- ablation A4: strict vs tiered pinning -----------------------------------------

def test_tiered_pin_releases_hbm_while_spilled():
    """A spilled pin holds its slot but gives its HBM back."""
    service = load_service_model()
    dev = Device(0, hbm_capacity_gib=80.0, max_concurrent_decodes=4, service=service)
    dev.pin_flow(flow_id=1, hbm_gib=10.0)
    assert dev.hbm_used_gib == 10.0

    freed = dev.spill_pin(1)
    assert freed == 10.0
    assert dev.hbm_used_gib == 0.0, "spilled KV occupies no HBM"
    assert dev.slots_used == 1, "but the compute slot is still held -- the guarantee"

    dev.restore_pin(1)
    assert dev.hbm_used_gib == 10.0


def test_spilled_pin_hbm_stays_committed_against_new_pins():
    """A spilled pin's memory is free for best-effort work but NOT for a new pin.

    Regression for a real guaranteed-restore violation: when a tiered pin spilled,
    its HBM was released and a NEW pin was allowed to claim it. New pins are not
    preemptible, so the spilled flow could never get its memory back. The engine's
    invariant caught it; this pins the fix.
    """
    service = load_service_model()
    dev = Device(0, hbm_capacity_gib=20.0, max_concurrent_decodes=8, service=service)
    dev.pin_flow(flow_id=1, hbm_gib=12.0)
    dev.spill_pin(1)                       # 12 GiB physically free now...

    assert dev.hbm_free_gib >= 12.0        # ...and best-effort work may use it,
    assert dev.hbm_committed_gib == 12.0   # ...but it is still owed to flow 1.

    # A second 12 GiB pin would fit in free HBM but NOT against the commitment.
    assert not dev.can_pin(12.0), "a new pin must not claim a spilled pin's memory"
    with pytest.raises(RuntimeError, match="over-commit"):
        dev.pin_flow(flow_id=2, hbm_gib=12.0)

    # A small pin that fits around the commitment is fine (20 - 12 = 8 free).
    assert dev.can_pin(8.0)


def test_oversized_flows_are_not_reserved():
    """A flow needing more than max_pin_hbm_fraction of a device goes best-effort.

    Reserving a flow that wants most of a GPU would strand that GPU under guarantee.
    Such a flow (here a runaway with huge accumulated context) must be contained by
    its budget in the aggregate class, not pinned. Regression for an over-commit the
    engine's invariant caught during tuning.
    """
    service = load_service_model()
    # A flow whose peak KV context needs > 0.5 * 80 = 40 GiB. Context accumulates
    # across steps (as the real Section 5 generator builds it), so context_tokens is
    # the running sum. ~123k tokens at 0.5 MiB/token is ~60 GiB -- a runaway.
    invs = []
    ctx = 0
    for j in range(41):
        ctx += 3000
        invs.append(Invocation(flow_id=0, index=j, prefill_tokens=0,
                               output_tokens=3000, context_tokens=ctx))
    big = Flow(flow_id=0, arrival_time=0.0, cls="agentic", is_runaway=True,
               invocations=invs)
    assert big.peak_context_tokens * service.kv_mib_per_token / 1024.0 > 40.0

    result = run_simulation(
        _cluster(service, slots=8, hbm=80.0),
        _res_policy(service),
        iter([(0.0, big)]),
        RunConfig(horizon_s=5000.0, warmup_s=0.0),
        degradation_catalog=load_degradation_catalog(),
    )
    # It was NOT reserved -- and its budget cut it off instead.
    assert result.reservations == 0, "an oversized flow must not be pinned"
    assert big.reservation is None
    assert result.abandonments == 1, "the aggregate-class budget should contain it"


def test_tiered_pins_always_restore_on_the_section5_workload():
    """End to end: run tiered pinning under load; the guarantee must never break.

    Before the fix this raised 'guaranteed-restore invariant is broken' partway
    through a busy run. It must now complete cleanly.
    """
    service = load_service_model()
    workload_cfg = load_yaml(CONFIG_DIR / "workload.yaml")
    workload_cfg["arrivals"]["base_rate_per_s"] = 40.0     # overloaded
    horizon = 90.0
    result = run_simulation(
        build_cluster({"num_devices": 4,
                       "device": {"hbm_capacity_gib": 80.0,
                                  "max_concurrent_decodes": 16}}, service),
        _res_policy(service, pin_mode="tiered"),
        WorkloadGenerator(workload_cfg, seed=3, horizon_s=horizon),
        RunConfig(horizon_s=horizon, warmup_s=0.0, seed=3, tick_interval_s=0.5),
        degradation_catalog=load_degradation_catalog(),
    )
    assert result.reservations > 0
    assert result.completions > 0


def _big_reserved_flow():
    """A reserved flow whose KV is big enough that spilling it costs real time."""
    i0 = Invocation(flow_id=0, index=0, prefill_tokens=0, output_tokens=20_000,
                    gap_after_s=_GAP_S)
    i1 = Invocation(flow_id=0, index=1, prefill_tokens=0, output_tokens=100)
    return Flow(flow_id=0, arrival_time=0.0, cls="agentic", invocations=[i0, i1])


def _run_pin_mode(pin_mode: str):
    service = load_service_model()
    return run_simulation(
        _cluster(service),
        _res_policy(service, pin_mode=pin_mode),
        iter([(0.0, _big_reserved_flow())]),
        RunConfig(horizon_s=1000.0, warmup_s=0.0),
        degradation_catalog=load_degradation_catalog(),
    )


def test_tiered_pin_pays_a_restore_latency_that_strict_does_not():
    """A4's real trade: tiered gives HBM back across the gap, but pays to get it back."""
    strict = _run_pin_mode("strict")
    tiered = _run_pin_mode("tiered")

    assert strict.completions == 1 and tiered.completions == 1
    assert tiered.mean_sojourn_s > strict.mean_sojourn_s, (
        "tiered must pay a spill-in latency on restore that strict never pays "
        f"(tiered {tiered.mean_sojourn_s:.4f}s vs strict {strict.mean_sojourn_s:.4f}s)")


# --- H3: the cross-invocation budget contains runaway flows --------------------------

def _runaway_and_compliant():
    """One looping flow with a huge token draw, plus a well-behaved one."""
    runaway = Flow(
        flow_id=0, arrival_time=0.0, cls="agentic", is_runaway=True,
        invocations=[Invocation(flow_id=0, index=j, prefill_tokens=0,
                                output_tokens=2000)
                     for j in range(50)],          # 100k tokens if left alone
    )
    compliant = Flow(
        flow_id=1, arrival_time=0.1, cls="agentic",
        invocations=[Invocation(flow_id=1, index=j, prefill_tokens=0,
                                output_tokens=200)
                     for j in range(5)],           # 1k tokens
    )
    return runaway, compliant


def test_budget_contains_the_runaway_and_spares_the_compliant_flow():
    service = load_service_model()
    runaway, compliant = _runaway_and_compliant()
    budget = 30_000.0
    policy = _res_policy(service, budget={
        "enabled": True,
        "tokens_by_class": {"agentic": budget},
        "per_request_cap_tokens": 4000,
    })
    result = run_simulation(
        _cluster(service, slots=8), policy,
        iter([(0.0, runaway), (0.1, compliant)]),
        RunConfig(horizon_s=5000.0, warmup_s=0.0),
        degradation_catalog=load_degradation_catalog(),
    )

    assert runaway.abandoned, "the runaway must be cut off by its budget"
    assert result.abandonments == 1
    drawn = sum(i.generated_tokens for i in runaway.invocations)
    assert abs(drawn - budget) < 1e-6, (
        f"runaway drew {drawn} tokens; the budget was {budget}")

    # And the compliant flow is untouched: it finishes, in full.
    assert compliant.is_complete and not compliant.abandoned
    assert sum(i.generated_tokens for i in compliant.invocations) == 1000


def test_ablation_a1_removes_the_budget_and_the_runaway_is_unbounded():
    """A1: no cross-invocation budget -> nothing stops the loop (the baselines' hole)."""
    service = load_service_model()
    runaway, compliant = _runaway_and_compliant()
    policy = _res_policy(service, budget={
        "enabled": False,
        "tokens_by_class": {"agentic": 30_000.0},
        "per_request_cap_tokens": 4000,
    })
    result = run_simulation(
        _cluster(service, slots=8), policy,
        iter([(0.0, runaway), (0.1, compliant)]),
        RunConfig(horizon_s=5000.0, warmup_s=0.0),
        degradation_catalog=load_degradation_catalog(),
    )

    assert result.abandonments == 0, "with no budget, nothing cuts the runaway off"
    drawn = sum(i.generated_tokens for i in runaway.invocations)
    assert drawn > 30_000.0, (
        f"runaway drew only {drawn} tokens -- it should be unbounded without a budget")


# --- headroom-keyed probabilistic degradation ----------------------------------------

def test_degradation_curve_interpolates_the_pre_registered_thresholds():
    service = load_service_model()
    policy = _res_policy(service)

    class _S:
        headroom = 0.0
    assert policy._degrade_probability(_S()) == 1.0      # no headroom -> always
    _S.headroom = 0.05
    assert abs(policy._degrade_probability(_S()) - 0.8) < 1e-9
    _S.headroom = 0.30
    assert policy._degrade_probability(_S()) == 0.0      # plenty of room -> never
    _S.headroom = 0.50
    assert policy._degrade_probability(_S()) == 0.0
    _S.headroom = 0.10                                    # between 0.05 and 0.15
    p = policy._degrade_probability(_S())
    assert 0.3 < p < 0.8, f"expected interpolation between the knots, got {p}"


def test_degradation_shrinks_remaining_work_and_forfeits_value():
    """The engine applies the action: less work to do, less value earned."""
    service = load_service_model()
    catalog = load_degradation_catalog()
    spec = catalog["shorten"]

    flow = Flow(flow_id=0, arrival_time=0.0, cls="agentic",
                invocations=[Invocation(flow_id=0, index=j, prefill_tokens=0,
                                        output_tokens=1000)
                             for j in range(3)])
    original_weight = flow.value_weight

    # Drive a run in which headroom is zero, so degradation fires with probability 1.
    policy = _res_policy(service)
    run_simulation(
        _cluster(service, slots=1), policy, iter([(0.0, flow)]),
        RunConfig(horizon_s=500.0, warmup_s=0.0, tick_interval_s=0.5),
        degradation_catalog=catalog,
    )

    assert flow.degradations >= 1, "a saturated cluster should have degraded this flow"
    assert flow.value_weight < original_weight, "degradation must cost served value"
    assert abs(flow.value_weight - original_weight * (1 - spec.quality_cost)) < 1e-9


# --- end to end on the real Section 5 workload ----------------------------------------

def test_reservation_policy_runs_the_section5_workload_without_violating_invariants():
    service = load_service_model()
    workload_cfg = load_yaml(CONFIG_DIR / "workload.yaml")
    horizon = 60.0

    result = run_simulation(
        build_cluster({"num_devices": 2,
                       "device": {"hbm_capacity_gib": 80.0,
                                  "max_concurrent_decodes": 8}}, service),
        build_policy("reservation", service),
        WorkloadGenerator(workload_cfg, seed=12345, horizon_s=horizon),
        RunConfig(horizon_s=horizon, warmup_s=0.0, seed=12345, tick_interval_s=0.5),
        degradation_catalog=load_degradation_catalog(),
    )

    # It reserved the long-horizon flows, and held capacity idle across their gaps.
    assert result.reservations > 0, "no reservations were granted on this workload"
    assert result.completions > 0
    assert 0.0 < result.reserved_idle_fraction < 1.0, (
        "stranding must be real and measured, not 0 and not 1: "
        f"{result.reserved_idle_fraction}")
