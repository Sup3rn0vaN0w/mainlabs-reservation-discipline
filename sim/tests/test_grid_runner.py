"""SG6: the grid runner is resumable, append-only, and correctly manifested.

These are the properties the brief (Section 8) demands and the reason an interrupted
overnight run costs zero completed work. Plus a check on the bootstrap CIs.
"""

import json

import pytest

from sim.analysis.stats import (
    bootstrap_ci,
    bootstrap_relative_improvement,
)
from sim.experiments.grid import (
    CellSpec,
    enumerate_cells,
    load_grid_config,
    run_grid,
)


def _tiny_grid_cfg():
    return {
        "policies": ["fcfs", "reservation"],
        "seeds": [1, 2],
        "cell": {"horizon_s": 30.0, "warmup_s": 5.0, "tick_s": 0.5,
                 "slots": 8, "hbm_gib": 80.0, "capacity_per_device": 8.0},
        "axes": {"mix": [0.3], "load": [1.0], "num_devices": [2],
                 "gap_median_s": [20]},
    }


def test_run_id_is_stable_and_parameter_sensitive():
    a = CellSpec("fcfs", "{}", 0.3, 1.0, 4, 16, 80.0, 20.0, 1, 300.0, 30.0, 0.5, 8.0)
    b = CellSpec("fcfs", "{}", 0.3, 1.0, 4, 16, 80.0, 20.0, 1, 300.0, 30.0, 0.5, 8.0)
    c = CellSpec("fcfs", "{}", 0.3, 1.0, 4, 16, 80.0, 20.0, 2, 300.0, 30.0, 0.5, 8.0)
    assert a.run_id() == b.run_id(), "identical params -> identical id"
    assert a.run_id() != c.run_id(), "different seed -> different id"


def test_enumerate_covers_the_cross_product():
    specs = enumerate_cells(_tiny_grid_cfg())
    # 1 mix x 1 load x 1 dev x 1 gap x 2 policies x 2 seeds = 4.
    assert len(specs) == 4
    assert {s.policy for s in specs} == {"fcfs", "reservation"}
    assert {s.seed for s in specs} == {1, 2}


def test_grid_is_resumable_and_append_only(tmp_path):
    cfg = _tiny_grid_cfg()

    first = run_grid(cfg, results_dir=tmp_path, enforce_horizon=False)
    assert first["ran"] == 4 and first["skipped"] == 0
    written = sorted(tmp_path.glob("*.json"))
    assert len(written) == 4

    # Capture the manifests, then run again: everything must be skipped, and the
    # files must be byte-identical (append-only, never rewritten).
    before = {p.name: p.read_bytes() for p in written}
    second = run_grid(cfg, results_dir=tmp_path, enforce_horizon=False)
    assert second["ran"] == 0, "a completed grid must re-run nothing"
    assert second["skipped"] == 4
    after = {p.name: p.read_bytes() for p in sorted(tmp_path.glob("*.json"))}
    assert before == after, "manifests must never be overwritten"


def test_manifest_has_provenance_and_every_metric(tmp_path):
    run_grid(_tiny_grid_cfg(), results_dir=tmp_path, enforce_horizon=False)
    manifest = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert "git_commit" in manifest
    assert manifest["spec"]["policy"] in ("fcfs", "reservation")
    for required in ("goodput_per_gpu_hour", "p95_ttft_s", "served_value_fraction",
                     "wasted_computation_fraction", "reserved_idle_fraction"):
        assert required in manifest["metrics"], f"manifest missing {required}"


def test_partial_write_is_not_mistaken_for_a_completed_run(tmp_path):
    """A stray .json.tmp must not be read as a finished manifest on resume."""
    cfg = _tiny_grid_cfg()
    specs = enumerate_cells(cfg)
    # Simulate an interrupted write: a tmp file with no final .json.
    (tmp_path / f"{specs[0].run_id()}.json.tmp").write_text("{partial")
    result = run_grid(cfg, results_dir=tmp_path, enforce_horizon=False)
    # All 4 real manifests are produced; the tmp file is ignored.
    assert result["ran"] == 4
    assert len(list(tmp_path.glob("*.json"))) == 4


# --- bootstrap CIs ------------------------------------------------------------------

def test_bootstrap_ci_brackets_the_point_and_is_reproducible():
    values = [10.0, 11.0, 9.0, 10.5, 9.5, 10.2]
    a = bootstrap_ci(values, seed=1)
    b = bootstrap_ci(values, seed=1)
    assert a.as_dict() == b.as_dict(), "same seed -> identical CI"
    assert a.lo <= a.point <= a.hi
    assert a.n == len(values)


def test_bootstrap_ci_degenerate_with_one_sample():
    ci = bootstrap_ci([42.0])
    assert ci.lo == ci.point == ci.hi == 42.0
    assert ci.n == 1


def test_paired_relative_improvement_sign():
    ours = [12.0, 13.0, 11.0]
    baseline = [10.0, 10.0, 10.0]
    ci = bootstrap_relative_improvement(ours, baseline, seed=7)
    assert ci.point > 0, "ours is uniformly higher -> positive improvement"
    with pytest.raises(ValueError):
        bootstrap_relative_improvement([1.0, 2.0], [1.0], seed=7)
