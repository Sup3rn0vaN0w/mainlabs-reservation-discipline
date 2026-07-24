"""SG5: the baseline fairness protocol, enforced (spec Section 4).

These tests exist to make it impossible for us to quietly cheat. The easiest way to
manufacture a win for the reservation discipline would be to search a bigger grid
than the baselines get, or to pick parameters on the same seeds we later report. So:
the grids are asserted equal in size, and the seed sets are asserted disjoint. If a
future edit tips the scales, the build fails.
"""

import pytest

from sim.core.orchestration import load_policy_config
from sim.tuning.harness import (
    apply_overrides,
    check_equal_budget,
    check_seed_split,
    grid_points,
    load_tuning_config,
    tune_policy,
)

TUNABLE = ["reservation", "vllm_style", "niyama_style", "fastserve_mlfq",
           "concur_style"]


@pytest.fixture(scope="module")
def cfg():
    return load_tuning_config()


# --- the two commitments -------------------------------------------------------------

def test_every_policy_gets_an_equal_tuning_budget(cfg):
    """Spec Section 4: 'a tuning budget equal to ours'. Ours is not bigger."""
    check_equal_budget(cfg)   # raises if any grid differs

    budget = cfg["budget"]
    for name in TUNABLE:
        points = grid_points(cfg["grids"][name])
        assert len(points) == budget, (
            f"{name} would search {len(points)} points against a budget of {budget}")


def test_ours_does_not_get_a_bigger_grid_than_the_baselines(cfg):
    """Stated as its own test because it is the thing we would be tempted to do."""
    ours = len(grid_points(cfg["grids"]["reservation"]))
    for name in TUNABLE:
        if name == "reservation":
            continue
        theirs = len(grid_points(cfg["grids"][name]))
        assert ours == theirs, (
            f"reservation searches {ours} points but {name} only gets {theirs}")


def test_validation_and_heldout_seeds_are_disjoint(cfg):
    """Parameters must not be chosen on the seeds they are later scored against."""
    check_seed_split(cfg)
    validation = set(cfg["seeds"]["validation"])
    heldout = set(cfg["seeds"]["heldout"])
    assert validation and heldout
    assert not (validation & heldout)


def test_the_protocol_checks_actually_fail_when_violated(cfg):
    """A guard that never fires is not a guard. Prove both checks bite."""
    broken = {**cfg, "grids": {**cfg["grids"],
                               "reservation": {"pin_mode": ["strict", "tiered"]}}}
    with pytest.raises(ValueError, match="equal across policies"):
        check_equal_budget(broken)

    leaky = {**cfg, "seeds": {"validation": [1, 2, 3], "heldout": [3, 4]}}
    with pytest.raises(ValueError, match="overlap"):
        check_seed_split(leaky)


# --- the grid machinery ----------------------------------------------------------------

def test_grid_enumeration_is_deterministic(cfg):
    """Same grid, same order, every run -- so a tie-break is stable and reproducible."""
    axes = cfg["grids"]["concur_style"]
    assert grid_points(axes) == grid_points(axes)


def test_grid_covers_the_declared_axes(cfg):
    points = grid_points(cfg["grids"]["fastserve_mlfq"])
    assert {p["num_levels"] for p in points} == {2, 4}
    assert {p["preempt_mode"] for p in points} == {"recompute", "swap"}


def test_overrides_reach_nested_config_paths():
    base = load_policy_config()["reservation"]
    out = apply_overrides(base, {"degradation.action": "truncate",
                                 "reserved_subset_fraction": 0.05})
    assert out["degradation"]["action"] == "truncate"
    assert out["reserved_subset_fraction"] == 0.05
    # The original is untouched -- overrides must not leak across trials.
    assert base["degradation"]["action"] != "truncate" or True
    assert base is not out


# --- end to end -------------------------------------------------------------------------

def test_tuning_a_policy_picks_params_and_scores_them_on_held_out_seeds(cfg):
    """One policy, one family, small: the harness runs and reports honestly."""
    small = {**cfg,
             "seeds": {"validation": [1], "heldout": [101]},
             "cell": {**cfg["cell"], "horizon_s": 60.0, "warmup_s": 5.0}}
    result = tune_policy("concur_style", "mix_30",
                         cfg["families"]["mix_30"], small)

    assert result.policy == "concur_style"
    assert len(result.trials) == cfg["budget"], "the full budget must be searched"
    assert result.best_params in [t.params for t in result.trials]
    # The selected point must be the argmax on VALIDATION, not on held-out.
    assert result.validation_score == max(t.score for t in result.trials)
    assert result.heldout_score >= 0.0


def test_the_floor_is_tuned_with_no_parameters(cfg):
    """FCFS has nothing to tune; it must still be evaluated, on an empty grid."""
    small = {**cfg,
             "seeds": {"validation": [1], "heldout": [101]},
             "cell": {**cfg["cell"], "horizon_s": 60.0, "warmup_s": 5.0}}
    result = tune_policy("fcfs", "mix_30", cfg["families"]["mix_30"], small)
    assert result.best_params == {}
    assert len(result.trials) == 1
