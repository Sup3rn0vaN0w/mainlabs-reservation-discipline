"""SG1 sanity test: utilization-vs-offered-load curve (brief Section 6, SG1)."""

from sim.sanity.utilization_curve import run_utilization_curve


def test_utilization_tracks_offered_load():
    for p in run_utilization_curve():
        assert p["utilization"] < 1.0
        rel = abs(p["utilization"] - p["offered_load"]) / p["offered_load"]
        assert rel < 0.05, (
            f"utilization {p['utilization']:.4f} off from rho "
            f"{p['offered_load']:.4f} (rel err {rel:.2%})")


def test_utilization_monotonic_non_decreasing():
    utils = [p["utilization"] for p in run_utilization_curve()]
    assert all(b >= a - 1e-9 for a, b in zip(utils, utils[1:])), \
        f"utilization not monotically non-decreasing: {utils}"
