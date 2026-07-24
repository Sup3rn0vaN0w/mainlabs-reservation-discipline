"""SG2: lognormal (median, p95) parameterization."""

import numpy as np

from sim.workload.distributions import LognormalSpec, sample_positive_int


def test_lognormal_recovers_median_and_p95():
    spec = LognormalSpec(median=12, p95=80)
    x = spec.sample(np.random.default_rng(0), size=200_000)
    assert abs(np.percentile(x, 50) - 12) / 12 < 0.05
    assert abs(np.percentile(x, 95) - 80) / 80 < 0.05


def test_mean_matches_closed_form():
    spec = LognormalSpec(median=200, p95=2000)
    x = spec.sample(np.random.default_rng(1), size=500_000)
    assert abs(np.mean(x) - spec.mean()) / spec.mean() < 0.05


def test_degenerate_specs_are_point_masses():
    ones = LognormalSpec(median=1, p95=1)
    assert ones.sigma == 0.0
    assert np.all(ones.sample(np.random.default_rng(0), size=1000) == 1.0)

    zero_gap = LognormalSpec(median=0, p95=0)
    assert zero_gap.sample_scalar(np.random.default_rng(0)) == 0.0


def test_sample_positive_int_floors_at_one():
    spec = LognormalSpec(median=1, p95=1)
    assert sample_positive_int(spec, np.random.default_rng(0)) == 1
