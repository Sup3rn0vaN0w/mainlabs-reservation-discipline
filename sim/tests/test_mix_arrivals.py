"""SG2: traffic-mix calibration and arrival process."""

import numpy as np

from sim.core.orchestration import CONFIG_DIR, load_yaml
from sim.core.seed import SeedManager
from sim.workload.arrivals import (
    ArrivalSpec,
    arrival_times,
    generate_burst_intervals,
    peak_rate,
    rate_at,
)
from sim.workload.generator import WorkloadGenerator
from sim.workload.mix import agentic_flow_probability
from sim.workload.validation import materialize, mix_token_share


def test_mix_probability_formula():
    # Equal per-flow means -> token share equals flow fraction.
    assert abs(agentic_flow_probability(0.3, 100.0, 100.0) - 0.3) < 1e-9
    assert agentic_flow_probability(0.0, 1.0, 5.0) == 0.0
    assert agentic_flow_probability(1.0, 1.0, 5.0) == 1.0


def test_achieved_token_share_hits_target():
    cfg = load_yaml(CONFIG_DIR / "workload.yaml")
    cfg["mix"]["agentic_token_share"] = 0.30
    flows = materialize(WorkloadGenerator(cfg, seed=7, horizon_s=20_000.0))
    share = mix_token_share(flows)["agentic_token_share"]
    # Regression guard (the report validates precision on a much larger run).
    assert abs(share - 0.30) < 0.10, share


def test_arrival_count_and_ordering():
    cfg = load_yaml(CONFIG_DIR / "workload.yaml")
    spec = ArrivalSpec.from_config(cfg["arrivals"])
    sm = SeedManager(5)
    horizon = 50_000.0
    bursts = generate_burst_intervals(spec, sm.stream("burst"), horizon)
    times = arrival_times(spec, sm.stream("arrivals"), horizon, bursts)

    base_expected = spec.base_rate_per_s * horizon  # diurnal averages ~1
    assert 0.9 * base_expected < len(times) < 1.3 * base_expected
    assert times == sorted(times)


def test_rate_is_positive_and_below_peak():
    cfg = load_yaml(CONFIG_DIR / "workload.yaml")
    spec = ArrivalSpec.from_config(cfg["arrivals"])
    lam_max = peak_rate(spec)
    for t in np.linspace(0.0, 86_400.0, 500):
        r = rate_at(t, spec, [])          # no bursts -> diurnal only
        assert 0.0 < r <= lam_max + 1e-9
