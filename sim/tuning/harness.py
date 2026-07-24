"""The baseline-fairness tuning harness (spec Section 4).

Every policy -- ours included -- gets the same deal:

  * the same tuning BUDGET (a pre-registered grid of identical size),
  * parameters chosen on VALIDATION seeds,
  * the chosen parameters then scored on HELD-OUT seeds the tuner never saw,
  * tuned separately per WORKLOAD FAMILY,
  * all maximizing the same objective: goodput per provisioned GPU-hour.

The point of this module is adversarial against ourselves. The easiest way to
manufacture a win for the reservation discipline would be to compare a
lovingly-tuned version of it against baselines left at their defaults. The grid
sizes are asserted equal, the seed split is asserted disjoint, and the objective is
the metric that already charges us for the capacity our own pins strand.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import copy
import itertools
import multiprocessing as mp
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..core.orchestration import (
    CONFIG_DIR,
    build_cluster,
    load_degradation_catalog,
    load_policy_config,
    load_service_model,
    load_yaml,
    run_simulation,
)
from ..core.types import RunConfig
from ..scheduler.policies.concur_style import ConcurStylePolicy
from ..scheduler.policies.fastserve_mlfq import FastServeMLFQPolicy
from ..scheduler.policies.fcfs import FCFSPolicy
from ..scheduler.policies.mars_style import MarsStylePolicy
from ..scheduler.policies.niyama_style import NiyamaStylePolicy
from ..scheduler.policies.reservation import ReservationPolicy
from ..scheduler.policies.vllm_style import VLLMStylePolicy
from ..workload.generator import WorkloadGenerator

_BUILDERS = {
    "fcfs": lambda cfg: FCFSPolicy(),
    "vllm_style": VLLMStylePolicy.from_config,
    "niyama_style": NiyamaStylePolicy.from_config,
    "concur_style": ConcurStylePolicy.from_config,
    "fastserve_mlfq": FastServeMLFQPolicy.from_config,
    "mars_style": MarsStylePolicy.from_config,
    "reservation": ReservationPolicy.from_config,
}


# --- config plumbing ----------------------------------------------------------------

def load_tuning_config(path: str | Path | None = None) -> dict:
    if path is None:
        path = CONFIG_DIR / "tuning.yaml"
    return load_yaml(path)


def set_dotted(cfg: dict, path: str, value) -> None:
    """Set `a.b.c` in a nested dict, in place."""
    keys = path.split(".")
    node = cfg
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value


def apply_overrides(base: dict, overrides: dict) -> dict:
    """Return a deep copy of `base` with dotted-path overrides applied."""
    out = copy.deepcopy(base)
    for path, value in overrides.items():
        set_dotted(out, path, value)
    return out


def grid_points(axes: dict[str, list]) -> list[dict]:
    """Cross product of the axes, in a deterministic order.

    Axis order follows the config file, and values follow their listed order, so the
    same grid always enumerates the same points in the same sequence -- which is what
    makes a tuning run reproducible and a tie-break stable.
    """
    names = list(axes)
    combos = itertools.product(*(axes[name] for name in names))
    return [dict(zip(names, combo)) for combo in combos]


# --- evaluation -----------------------------------------------------------------------

@dataclass
class TrialScore:
    params: dict
    score: float          # mean goodput per provisioned GPU-hour across seeds
    per_seed: list[float] = field(default_factory=list)


@dataclass
class TuningResult:
    policy: str
    family: str
    best_params: dict
    validation_score: float
    heldout_score: float
    trials: list[TrialScore]


@dataclass(frozen=True)
class RunSpec:
    """One simulation: a policy at one parameterization, on one seed at one load."""

    policy_name: str
    policy_params: tuple            # (key, value) pairs -- hashable and picklable
    workload_cfg: dict
    seed: int
    load: float
    cell: dict
    base_policy_cfg: dict

    @property
    def params(self) -> dict:
        return dict(self.policy_params)


def run_one(spec: RunSpec) -> float:
    """Execute one simulation and return the primary metric. Must be top-level
    (picklable) so the worker pool can call it."""
    service = load_service_model()
    catalog = load_degradation_catalog(spec.base_policy_cfg)
    cell = spec.cell

    policy_cfg = apply_overrides(
        spec.base_policy_cfg.get(spec.policy_name, {}), spec.params)
    policy = _BUILDERS[spec.policy_name](policy_cfg)
    policy.attach_kv_rate(service.kv_mib_per_token)

    # Offered load is set by the arrival rate, relative to MEASURED capacity.
    workload = apply_overrides(
        spec.workload_cfg,
        {"arrivals.base_rate_per_s": spec.load
                                     * cell["capacity_arrival_rate_per_s"]},
    )

    result = run_simulation(
        build_cluster(cell["cluster"], service),
        policy,
        WorkloadGenerator(workload, seed=spec.seed, horizon_s=cell["horizon_s"]),
        RunConfig(horizon_s=cell["horizon_s"], warmup_s=cell["warmup_s"],
                  seed=spec.seed, tick_interval_s=cell["tick_interval_s"]),
        degradation_catalog=catalog,
    )
    return result.goodput_per_gpu_hour


def execute(specs: list[RunSpec], workers: int | None = None) -> list[float]:
    """Run many simulations, in parallel when it is worth it.

    Runs are independent by construction (each builds its own cluster, policy and
    seeded workload), so this is embarrassingly parallel. Results come back in the
    order the specs were given, so downstream aggregation stays deterministic.
    """
    if workers is None:
        workers = max(1, (os.cpu_count() or 2) - 2)
    if workers == 1 or len(specs) < 2:
        return [run_one(s) for s in specs]
    with mp.get_context("fork").Pool(processes=workers) as pool:
        return pool.map(run_one, specs)


def build_specs(policy_name: str, points: list[dict], workload_cfg: dict,
                seeds: list[int], tuning_cfg: dict,
                base_policy_cfg: dict) -> list[RunSpec]:
    """Every (grid point x seed x load) run for one policy on one workload family.

    The objective is averaged over BOTH seeds and the load region. Averaging over
    loads is not a detail: these policies are strongly regime-dependent, so tuning at
    a single load would let each be fitted to the regime that suits it -- and would
    let us pick the load that flatters the reservation discipline. The loads span the
    pre-registered core operating region, so nothing is tuned in a corner cell that
    could not count toward support anyway.
    """
    cell = tuning_cfg["cell"]
    return [
        RunSpec(
            policy_name=policy_name,
            policy_params=tuple(sorted(params.items())),
            workload_cfg=workload_cfg,
            seed=seed,
            load=load,
            cell=cell,
            base_policy_cfg=base_policy_cfg,
        )
        for params in points
        for seed in seeds
        for load in cell["loads"]
    ]


def tune_policy(policy_name: str, family: str, family_overrides: dict,
                tuning_cfg: dict, workers: int | None = None) -> TuningResult:
    """Pick parameters on validation seeds; score the pick on held-out seeds."""
    base_policy_cfg = load_policy_config()
    base_workload = load_yaml(CONFIG_DIR / "workload.yaml")
    workload_cfg = apply_overrides(base_workload, family_overrides)

    validation = tuning_cfg["seeds"]["validation"]
    heldout = tuning_cfg["seeds"]["heldout"]

    if policy_name in tuning_cfg.get("untuned_policies", []):
        points = [{}]     # the floor: nothing to tune
    else:
        points = grid_points(tuning_cfg["grids"][policy_name])

    # --- selection: validation seeds only ---
    specs = build_specs(policy_name, points, workload_cfg, validation,
                        tuning_cfg, base_policy_cfg)
    scores = execute(specs, workers=workers)

    runs_per_point = len(validation) * len(tuning_cfg["cell"]["loads"])
    trials: list[TrialScore] = []
    for i, params in enumerate(points):
        chunk = scores[i * runs_per_point:(i + 1) * runs_per_point]
        trials.append(TrialScore(
            params=params,
            score=sum(chunk) / len(chunk) if chunk else 0.0,
            per_seed=chunk,
        ))

    # Best on VALIDATION only. Ties break on the grid's deterministic order.
    best = max(trials, key=lambda t: t.score)

    # --- and only now, the held-out seeds ---
    heldout_specs = build_specs(policy_name, [best.params], workload_cfg,
                                heldout, tuning_cfg, base_policy_cfg)
    heldout_scores = execute(heldout_specs, workers=workers)
    heldout_score = (sum(heldout_scores) / len(heldout_scores)
                     if heldout_scores else 0.0)

    return TuningResult(
        policy=policy_name,
        family=family,
        best_params=best.params,
        validation_score=best.score,
        heldout_score=heldout_score,
        trials=trials,
    )


def run_tuning(tuning_cfg: dict | None = None,
               policies: list[str] | None = None) -> list[TuningResult]:
    """Tune every policy on every workload family."""
    if tuning_cfg is None:
        tuning_cfg = load_tuning_config()
    if policies is None:
        policies = list(tuning_cfg["grids"]) + list(
            tuning_cfg.get("untuned_policies", []))

    results: list[TuningResult] = []
    for family, overrides in tuning_cfg["families"].items():
        for policy_name in policies:
            results.append(tune_policy(policy_name, family, overrides, tuning_cfg))
    return results


# --- validation of the protocol itself --------------------------------------------------

def check_equal_budget(tuning_cfg: dict) -> None:
    """Every tunable policy must get exactly the same number of grid points."""
    budget = tuning_cfg["budget"]
    sizes = {name: len(grid_points(axes))
             for name, axes in tuning_cfg["grids"].items()}
    wrong = {n: s for n, s in sizes.items() if s != budget}
    if wrong:
        raise ValueError(
            f"tuning budget must be equal across policies (budget={budget}); "
            f"these grids do not comply: {wrong}")


def check_seed_split(tuning_cfg: dict) -> None:
    """Validation and held-out seeds must be disjoint."""
    validation = set(tuning_cfg["seeds"]["validation"])
    heldout = set(tuning_cfg["seeds"]["heldout"])
    overlap = validation & heldout
    if overlap:
        raise ValueError(
            f"validation and held-out seeds overlap: {sorted(overlap)} -- the tuner "
            f"would be selecting parameters on the seeds it is later scored against")
