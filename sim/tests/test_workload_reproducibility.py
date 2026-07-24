"""SG2: workload reproducibility (brief Section 6: two runs, identical hash)."""

from sim.core.orchestration import CONFIG_DIR, load_yaml
from sim.workload.generator import WorkloadGenerator
from sim.workload.validation import materialize, workload_hash


def _cfg():
    return load_yaml(CONFIG_DIR / "workload.yaml")


def test_same_seed_identical_hash():
    h1 = workload_hash(materialize(WorkloadGenerator(_cfg(), seed=11, horizon_s=5_000.0)))
    h2 = workload_hash(materialize(WorkloadGenerator(_cfg(), seed=11, horizon_s=5_000.0)))
    assert h1 == h2


def test_reiteration_is_identical():
    """Re-iterating one generator replays the identical workload (paired comparison)."""
    gen = WorkloadGenerator(_cfg(), seed=11, horizon_s=5_000.0)
    assert workload_hash(materialize(gen)) == workload_hash(materialize(gen))


def test_different_seed_differs():
    h1 = workload_hash(materialize(WorkloadGenerator(_cfg(), seed=11, horizon_s=5_000.0)))
    h2 = workload_hash(materialize(WorkloadGenerator(_cfg(), seed=12, horizon_s=5_000.0)))
    assert h1 != h2
