"""SG6 sensitivity harness check (blocking gate; see docs/SENSITIVITY_HARNESS_CHECK.md).

The pilot sensitivity table showed byte-identical results for peak_decode at +/-30%
and a frozen reservation value across kv perturbations. A perturbation that changes
nothing is either a bug (it never reached the engine) or a provable non-binding.
These tests pin the determination: NON-BINDING, and they guard against a future
regression back into either failure mode.

Two things are asserted:
  1. The perturbation IS observed inside the runtime service model the engine
     consumes (rules out the plumbing-bug branch forever).
  2. peak decode is structurally unreachable at 16 slots, and KV never approaches
     capacity at the representative cell (the two constants are non-binding, and we
     can show why).
"""

import copy

from sim.cluster.service_model import ServiceModel
from sim.core.orchestration import CONFIG_DIR, load_yaml
from sim.experiments.sensitivity import binding_diagnostics


def _service_with(section, key, factor):
    cfg = copy.deepcopy(load_yaml(CONFIG_DIR / "service_model.yaml"))
    cfg[section][key] = cfg[section][key] * factor
    return ServiceModel.from_config(cfg)


def test_perturbation_reaches_the_runtime_service_model():
    """Perturb -> the value the ENGINE consumes at runtime actually changed.

    This is the assertion that forecloses the application-bug branch: whatever the
    downstream effect, the constant is not being silently dropped on the way in.
    """
    nominal = ServiceModel.from_config(load_yaml(CONFIG_DIR / "service_model.yaml"))

    down = _service_with("decode", "peak_decode_tokens_per_s", 0.7)
    up = _service_with("decode", "peak_decode_tokens_per_s", 1.3)
    assert down.peak_decode_tokens_per_s < nominal.peak_decode_tokens_per_s < \
        up.peak_decode_tokens_per_s

    kdown = _service_with("kv_cache", "mib_per_token", 0.7)
    kup = _service_with("kv_cache", "mib_per_token", 1.3)
    assert kdown.kv_mib_per_token < nominal.kv_mib_per_token < kup.kv_mib_per_token


def test_peak_decode_is_structurally_non_binding_at_16_slots():
    """Peak binds only at a batch the slot cap makes unreachable, so it never bites."""
    d = binding_diagnostics("reservation")
    # Peak binds at batch = peak/single = 4000/100 = 40, but the batch is capped.
    assert d["peak_binds_at_batch"] > d["max_concurrent_decodes"], (
        "peak would have to bind within the batch cap for the sensitivity to be "
        "informative here")
    assert d["max_batch_observed"] <= d["max_concurrent_decodes"]
    assert d["peak_reachable"] is False
    assert d["peak_hits"] == 0, (
        f"peak was the binding term {d['peak_hits']} times -- it must never bind "
        f"at this cell for the byte-identical row to be a non-binding, not a bug")


def test_kv_never_constrains_under_the_perturbation():
    """A +/-30% KV change cannot alter feasibility: even the +30% peak stays under
    capacity, so nothing that fit stops fitting. That is the precise non-binding
    condition (peak_HBM x 1.3 < capacity), not an arbitrary threshold."""
    d = binding_diagnostics("reservation")
    assert d["max_hbm_fraction"] * 1.3 < 1.0, (
        f"peak HBM was {d['max_hbm_fraction']:.1%}; a +30% KV bump would reach "
        f"{d['max_hbm_fraction'] * 1.3:.1%} of capacity. If that crossed 100% the "
        f"perturbation WOULD bind and the frozen row would be suspect")


def test_peak_and_kv_DO_bind_at_the_memory_bound_cell():
    """PART B.2: the complement -- at 64 slots (agentic-heavy) peak decode is reached
    and HBM saturates, so both constants bind. This proves the 16-slot non-binding is
    a property of that cell, not a harness that cannot see binding."""
    d = binding_diagnostics("reservation", slots=64, mix=0.5, load=1.5,
                            num_devices=2)
    assert d["peak_reachable"] is True
    assert d["peak_hits"] > 0, "peak decode must be the binding term at 64 slots"
    assert d["max_hbm_fraction"] > 0.9, (
        f"HBM should approach saturation at 64 slots agentic-heavy "
        f"(got {d['max_hbm_fraction']:.1%})")
