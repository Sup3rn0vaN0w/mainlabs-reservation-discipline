"""The FINAL readout must refuse cells the pre-registered grid does not define.

Real incident (2026-07-20): `make pilot` writes to the SAME results/ directory as
the main grid and fixes num_devices=4. A pilot run predating Amendment 2 left 189
manifests at horizon 300s / 4 devices in the final grid's evidence directory. The
existing duplicate-cell_key guard did NOT catch them -- out-of-grid cells get
DISTINCT keys, so they would have aggregated silently into the support fraction of
the document that fires D-058.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import pytest

from sim.analysis.h1_readout import assert_manifests_match_grid
from sim.experiments.grid import load_grid_config


def _manifest(**over):
    cfg = load_grid_config("grid_main.yaml")
    spec = {"mix": 0.30, "load": 1.0, "num_devices": cfg["axes"]["num_devices"][0],
            "gap_median_s": 20, "seed": cfg["seeds"][0],
            "horizon_s": cfg["cell"]["horizon_s"], "policy": "reservation"}
    spec.update(over)
    return {"spec": spec, "cell_key": "k", "run_id": "r",
            "metrics": {"goodput_per_gpu_hour": 1.0, "p95_ttft_s": 1.0}}


def test_conforming_manifests_pass():
    assert assert_manifests_match_grid([_manifest(), _manifest(seed=2)]) is None


def test_censored_horizon_is_rejected():
    """The exact 300s contamination found sitting in the live results dir."""
    with pytest.raises(ValueError, match="horizon"):
        assert_manifests_match_grid([_manifest(), _manifest(horizon_s=300.0)])


def test_out_of_grid_cluster_size_is_rejected():
    """num_devices=4 is the pilot's size and is not in the main grid."""
    with pytest.raises(ValueError, match="num_devices"):
        assert_manifests_match_grid([_manifest(num_devices=4)])


def test_dropped_64_device_axis_is_rejected():
    """Amendment 3 removed 64; a stray 64-device manifest must not sneak back in."""
    with pytest.raises(ValueError, match="num_devices"):
        assert_manifests_match_grid([_manifest(num_devices=64)])


def test_out_of_range_seed_is_rejected():
    """Seeds 4/5 predate Amendment 3 and would unbalance the bootstrap."""
    with pytest.raises(ValueError, match="seed"):
        assert_manifests_match_grid([_manifest(seed=5)])


def test_error_names_the_offenders_and_says_do_not_delete():
    """The message has to be actionable -- this fires late at night before a filing."""
    with pytest.raises(ValueError) as ei:
        assert_manifests_match_grid([_manifest(horizon_s=300.0),
                                     _manifest(horizon_s=300.0)])
    msg = str(ei.value)
    assert "2 manifests" in msg
    assert "do not" in msg.lower() and "delete" in msg.lower()
