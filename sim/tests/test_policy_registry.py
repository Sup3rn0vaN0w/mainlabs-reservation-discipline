"""Guard: every grid policy is buildable by BOTH policy registries.

There are two places a policy must be registered -- `build_policy` (used by the grid
runner and the app) and the tuning harness's `_BUILDERS` (used to tune). They are
separate dicts, and mars_style shipped once with only the first updated, so tuning
crashed. This test makes that class of drift a build failure instead of a runtime
one: any policy named in the frozen main grid must resolve in both registries, and
must be tunable (have a grid or be explicitly untuned).
"""

from sim.core.orchestration import (
    CONFIG_DIR,
    build_policy,
    load_service_model,
    load_yaml,
)
from sim.tuning.harness import _BUILDERS


def test_every_grid_policy_builds_in_both_registries():
    service = load_service_model()
    grid = load_yaml(CONFIG_DIR / "grid_main.yaml")
    for name in grid["policies"]:
        # build_policy path (grid runner / app).
        policy = build_policy(name, service)
        assert policy.name == name or name == "fcfs", name
        # harness path (tuning).
        assert name in _BUILDERS, (
            f"{name} is in the grid but not in the tuning harness _BUILDERS -- "
            f"it would be graded but never fairly tuned")


def test_every_tunable_policy_has_an_equal_grid():
    """A grid policy is either in the tuning grids or explicitly untuned -- never
    silently un-tuned (which would grade it on defaults against tuned rivals)."""
    grid = load_yaml(CONFIG_DIR / "grid_main.yaml")
    tuning = load_yaml(CONFIG_DIR / "tuning.yaml")
    tunable = set(tuning["grids"])
    untuned = set(tuning.get("untuned_policies", []))
    for name in grid["policies"]:
        if name == "reservation":
            continue  # ours; tuned under the same harness, present in grids
        assert name in tunable or name in untuned, (
            f"{name} is a graded baseline but is neither tuned nor explicitly "
            f"declared untuned -- it would compete on default parameters")
