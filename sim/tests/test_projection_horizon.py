"""The runtime projection must be measured at the horizon it is projecting.

Amendment 2 raised every sweep to 1500s, but the scaling probe hardcoded 300s --
so the whole main-grid projection came out ~5x low, and deleting the cache did not
help because the hardcode was inside the measurement. That mis-sizes the rented
cluster and mis-answers the 24-hour guard, which is a launch decision.

These tests pin the two properties that failure needed:
  1. the probe reads horizon/warmup from the grid config it projects, and
  2. a cached probe measured at a DIFFERENT horizon is discarded, not reused.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import json

import pytest

from sim.experiments import pilot
from sim.experiments.grid import load_grid_config


def test_projection_run_config_tracks_the_grid_config():
    """The probe's horizon is the grid's horizon -- not a literal."""
    horizon_s, warmup_s = pilot._projection_run_config("grid_main.yaml")
    run = load_grid_config("grid_main.yaml")["cell"]
    assert horizon_s == float(run["horizon_s"])
    assert warmup_s == float(run["warmup_s"])
    # Amendment 2 floor: the grid never runs a censored horizon.
    assert horizon_s >= 1500.0


def test_probe_is_measured_at_the_grid_horizon(monkeypatch):
    """_measure_scaling_uncached receives the grid horizon, not a hardcoded 300s."""
    seen: dict = {}

    def fake_measure(horizon_s, warmup_s):
        seen["horizon_s"] = horizon_s
        seen["warmup_s"] = warmup_s
        return [{"num_devices": 8, "seconds": 1.0}]

    monkeypatch.setattr(pilot, "_measure_scaling_uncached", fake_measure)
    monkeypatch.setattr(pilot, "_SCALING_CACHE",
                        pilot._SCALING_CACHE.parent / "scaling_probe_test.json")
    try:
        pilot.measure_scaling(use_cache=False)
    finally:
        pilot._SCALING_CACHE.unlink(missing_ok=True)

    expected, expected_warmup = pilot._projection_run_config("grid_main.yaml")
    assert seen["horizon_s"] == expected
    assert seen["warmup_s"] == expected_warmup


@pytest.mark.parametrize("stale", [
    [{"num_devices": 8, "seconds": 9.9}],                       # legacy bare list
    {"horizon_s": 300.0, "rows": [{"num_devices": 8, "seconds": 9.9}]},
])
def test_stale_horizon_cache_is_discarded(monkeypatch, tmp_path, stale):
    """A probe from a different horizon must never drive the projection."""
    cache = tmp_path / "scaling_probe.json"
    cache.write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setattr(pilot, "_SCALING_CACHE", cache)

    called = {"n": 0}

    def fake_measure(horizon_s, warmup_s):
        called["n"] += 1
        return [{"num_devices": 8, "seconds": 50.0}]

    monkeypatch.setattr(pilot, "_measure_scaling_uncached", fake_measure)
    rows = pilot.measure_scaling(use_cache=True)

    assert called["n"] == 1, "stale-horizon probe was reused instead of re-measured"
    assert rows[0]["seconds"] == 50.0
    assert json.loads(cache.read_text(encoding="utf-8"))["horizon_s"] >= 1500.0


def test_matching_horizon_cache_is_reused(monkeypatch, tmp_path):
    """The cache still works -- this is a correctness guard, not a perf regression."""
    horizon_s, warmup_s = pilot._projection_run_config("grid_main.yaml")
    cache = tmp_path / "scaling_probe.json"
    cache.write_text(json.dumps({
        "horizon_s": horizon_s, "warmup_s": warmup_s,
        "rows": [{"num_devices": 8, "seconds": 42.0}]}), encoding="utf-8")
    monkeypatch.setattr(pilot, "_SCALING_CACHE", cache)

    def boom(*a, **k):
        raise AssertionError("re-measured despite a matching-horizon cache")

    monkeypatch.setattr(pilot, "_measure_scaling_uncached", boom)
    assert pilot.measure_scaling(use_cache=True)[0]["seconds"] == 42.0
