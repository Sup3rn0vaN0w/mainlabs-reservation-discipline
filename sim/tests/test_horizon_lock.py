"""Amendment 2: every shipped sweep config satisfies the horizon adequacy guard.

The horizon must be at least the median agentic flow lifetime derived from the
workload, or the primary metric is right-censored against the reservation discipline
(docs/H3_ZERO_CHECK.md). This is structural: the bound comes from the workload
parameters, and the guard is enforced in run_grid. These tests lock it.
"""

import pytest

from sim.core.orchestration import CONFIG_DIR, load_service_model, load_yaml
from sim.workload.lifetimes import (
    assert_horizon_adequate,
    median_agentic_flow_lifetime,
)

_SWEEP_CONFIGS = ["grid_main.yaml", "grid_h2.yaml", "grid_h3.yaml", "ablations.yaml"]


def test_every_shipped_sweep_horizon_is_adequate():
    service = load_service_model()
    workload = load_yaml(CONFIG_DIR / "workload.yaml")
    median = median_agentic_flow_lifetime(workload, service)
    assert median > 0
    for name in _SWEEP_CONFIGS:
        cfg = load_yaml(CONFIG_DIR / name)
        horizon = float(cfg["cell"]["horizon_s"])
        assert horizon >= median, (
            f"{name}: horizon {horizon:.0f}s < median agentic lifetime "
            f"{median:.0f}s -- Amendment 2 violated")


def test_tuning_cell_horizon_is_adequate():
    """Tuning must optimize at an adequate horizon or it fits a censored objective."""
    service = load_service_model()
    workload = load_yaml(CONFIG_DIR / "workload.yaml")
    median = median_agentic_flow_lifetime(workload, service)
    tuning = load_yaml(CONFIG_DIR / "tuning.yaml")
    assert float(tuning["cell"]["horizon_s"]) >= median


def test_the_guard_rejects_a_censoring_horizon():
    service = load_service_model()
    workload = load_yaml(CONFIG_DIR / "workload.yaml")
    with pytest.raises(ValueError, match="right-censoring|below the median"):
        assert_horizon_adequate(300.0, workload, service, context="test")


def test_the_guard_is_derived_not_hardcoded():
    """A workload with longer gaps must raise the required horizon."""
    service = load_service_model()
    base = load_yaml(CONFIG_DIR / "workload.yaml")
    longer = {**base, "classes": {**base["classes"],
              "agentic": {**base["classes"]["agentic"],
                          "gap_s": {"median": 120, "p95": 1800}}}}
    assert (median_agentic_flow_lifetime(longer, service)
            > median_agentic_flow_lifetime(base, service))
