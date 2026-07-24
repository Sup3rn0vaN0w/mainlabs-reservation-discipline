"""Amendment 3 (2026-07-20) pins the main-grid axes. Drift must not be silent.

Amendment 3 dropped num_devices 64 and cut seeds 5 -> 3, because one 64-device run
at the 1500s horizon measured 24-32h and a run is single-threaded -- the 24h guard
was unreachable at any budget on any number of machines. See
content/research/plane_separation/SG7_GRID_FEASIBILITY_2026-07-20.md.

Everything ELSE was explicitly held constant. These tests fail loudly if any of it
moves, because the grid feeds H1_READOUT_FINAL.md, which is the sole trigger for
D-058. A quiet axis change here is a quiet change to a filing decision.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import pytest

from sim.experiments.grid import load_grid_config

MAIN = "grid_main.yaml"


# --- what Amendment 3 changed -------------------------------------------------------

def test_device_axis_drops_64():
    """64 devices is out: a single run exceeded the wall-clock guard."""
    axes = load_grid_config(MAIN)["axes"]
    assert axes["num_devices"] == [8, 16, 32]
    assert 64 not in axes["num_devices"], (
        "64-device cells reinstated: one such run measured 24-32h against a 24h "
        "guard, and runs are single-threaded so no amount of hardware fixes it")


def test_seeds_are_three():
    """3 seeds, not 1 -- the percentile bootstrap resamples ACROSS seeds."""
    seeds = load_grid_config(MAIN)["seeds"]
    assert seeds == [1, 2, 3]
    assert len(seeds) >= 3, (
        "fewer than 3 seeds leaves the bootstrap in h1_readout with no distribution "
        "to resample; Section 3 support/guardrail logic loses its statistical basis")


# --- what Amendment 3 explicitly did NOT change -------------------------------------

def test_horizon_still_1500s():
    """Amendment 2 floor survives Amendment 3."""
    cell = load_grid_config(MAIN)["cell"]
    assert cell["horizon_s"] == 1500.0, "Amendment 2 horizon must not be traded away"
    assert cell["warmup_s"] == 150.0


def test_load_axis_unchanged_including_overload():
    """The overloaded points are the expensive ones -- and they stay."""
    load = load_grid_config(MAIN)["axes"]["load"]
    assert load == [0.7, 1.0, 1.3, 1.5]
    assert 1.3 in load and 1.5 in load, (
        "overload cells dropped: these dominate runtime, which makes them the "
        "tempting cut and exactly the cut that would bias H1")


def test_gap_axis_unchanged():
    gap = load_grid_config(MAIN)["axes"]["gap_median_s"]
    assert gap == [20, 60, 300]


def test_mix_axis_unchanged():
    assert load_grid_config(MAIN)["axes"]["mix"] == [0.10, 0.30, 0.50]


def test_all_seven_policies_present():
    """mars_style (Amendment 1) included; reservation is the mechanism under test."""
    policies = load_grid_config(MAIN)["policies"]
    assert len(policies) == 7
    for required in ("fcfs", "vllm_style", "niyama_style", "concur_style",
                     "fastserve_mlfq", "mars_style", "reservation"):
        assert required in policies, f"{required} missing from the main grid"


# --- the resulting shape ------------------------------------------------------------

def test_total_run_count_is_2268():
    """108 cells x 7 policies x 3 seeds. Pins the whole shape in one number."""
    cfg = load_grid_config(MAIN)
    axes = cfg["axes"]
    cells = (len(axes["mix"]) * len(axes["load"])
             * len(axes["num_devices"]) * len(axes["gap_median_s"]))
    assert cells == 108
    assert cells * len(cfg["policies"]) * len(cfg["seeds"]) == 2268


@pytest.mark.parametrize("config", ["grid_h2.yaml", "grid_h3.yaml"])
def test_secondary_sweeps_stay_within_amended_sizes(config):
    """H2/H3 must not reintroduce a cluster size the main grid just dropped."""
    axes = load_grid_config(config)["axes"]
    assert max(axes["num_devices"]) <= 32, (
        f"{config} runs a cluster size above the Amendment 3 ceiling")


@pytest.mark.parametrize("config", ["grid_main.yaml", "grid_h2.yaml", "grid_h3.yaml"])
def test_every_sweep_holds_the_amendment2_horizon(config):
    """No sweep may quietly revert to a censored horizon."""
    assert load_grid_config(config)["cell"]["horizon_s"] >= 1500.0


def test_probe_measures_only_the_sizes_the_grid_runs(monkeypatch):
    """The scaling probe must not time a cluster size the grid dropped.

    It used to hardcode (8, 16, 32, 64). After Amendment 3 that would burn 24-32h
    measuring 64 devices for a grid that no longer has them -- the same failure
    mode as the old hardcoded horizon, one layer over.
    """
    from sim.experiments import pilot

    seen = {}

    def fake_run(*a, **k):
        raise AssertionError("should not actually simulate in this test")

    monkeypatch.setattr(pilot, "run_simulation",
                        lambda *a, **k: seen.setdefault("ran", True))
    monkeypatch.setattr(pilot, "build_cluster", lambda cfg, svc: seen
                        .setdefault("sizes", []).append(cfg["num_devices"]))
    monkeypatch.setattr(pilot, "build_policy", lambda *a, **k: None)

    pilot._measure_scaling_uncached(1500.0, 150.0)

    grid_sizes = load_grid_config(MAIN)["axes"]["num_devices"]
    assert seen["sizes"] == grid_sizes
    assert 64 not in seen["sizes"]


# --- the spot/on-demand device split must be lossless -------------------------------

def test_device_split_covers_the_whole_grid_exactly_once():
    """8,16 on spot + 32 on on-demand must equal the full grid. No gaps, no overlap.

    This is the property that makes the launch split safe. If it ever fails, some
    cells run twice (wasted spend) or -- far worse -- never run at all, and the
    readout would compute a verdict over a silently incomplete grid.
    """
    from sim.experiments.grid import enumerate_cells

    cfg = load_grid_config(MAIN)
    everything = {s.run_id() for s in enumerate_cells(cfg)}

    spot = {s.run_id() for s in enumerate_cells(cfg) if s.num_devices in {8, 16}}
    ondemand = {s.run_id() for s in enumerate_cells(cfg) if s.num_devices in {32}}

    assert spot & ondemand == set(), "device split overlaps -- cells would run twice"
    assert spot | ondemand == everything, "device split leaves cells unrun"
    assert len(everything) == 2268


def test_unknown_device_filter_is_rejected():
    """A typo like --devices 64 must fail loudly, not run zero cells and succeed."""
    from sim.experiments.grid import run_grid

    with pytest.raises(ValueError, match="not in the grid.s device axis"):
        run_grid(load_grid_config(MAIN), devices={64})
