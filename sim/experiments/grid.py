"""The experiment grid runner (brief Section 3 and Section 8).

Runs the pre-registered experiment matrix and writes results as an APPEND-ONLY set
of per-run manifests. The two properties the brief demands:

  * RESUMABLE. Every (cell x seed x policy) run writes its own manifest file, keyed
    by a stable hash of its parameters. A restarted run skips any manifest that
    already exists, so an interrupted grid (sleep, crash, power loss) costs zero
    completed work and the machine is never required to stay awake.
  * APPEND-ONLY. A manifest is never overwritten. A re-run with changed parameters
    is a new hash and a new file; a re-run with identical parameters is skipped.
    Results accumulate; they are never silently mutated.

Each manifest records the full parameterization, the git commit, the config
fingerprints, and every metric -- enough to reproduce the run and to audit the
verdict later.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import multiprocessing as mp
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core.orchestration import (
    CONFIG_DIR,
    build_cluster,
    build_policy,
    load_degradation_catalog,
    load_policy_config,
    load_service_model,
    load_yaml,
    run_simulation,
)
from ..core.types import RunConfig
from ..tuning.harness import apply_overrides
from ..workload.generator import WorkloadGenerator

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Metrics lifted from RunResult into every manifest.
_METRIC_FIELDS = [
    "goodput_per_gpu_hour", "served_value_fraction", "completed_value",
    "offered_value", "p95_flow_completion_s", "p99_flow_completion_s",
    "p95_ttft_s", "p95_itl_s", "mean_ttft_s", "wasted_computation_fraction",
    "reserved_idle_fraction", "mean_utilization",
    "arrivals", "admissions", "rejections", "completions",
    "preemptions", "reservations", "abandonments", "degradations",
    "total_generated_tokens", "useful_generated_tokens",
]


@dataclass(frozen=True)
class CellSpec:
    """One simulation in the grid. Frozen and hashable -> a stable run id."""

    policy: str
    params_json: str          # tuned params, canonical JSON (hashable)
    mix: float
    load: float               # multiple of measured capacity
    num_devices: int
    slots: int
    hbm_gib: float
    gap_median_s: float
    seed: int
    horizon_s: float
    warmup_s: float
    tick_s: float
    capacity_per_device: float
    runaway_fraction: float = 0.0   # adversarial injection (H3 sweep; 0 for H1/H2)

    @property
    def params(self) -> dict:
        return json.loads(self.params_json)

    def run_id(self) -> str:
        """Stable 16-hex id from the parameters only (provenance excluded)."""
        blob = json.dumps(asdict(self), sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    def cell_key(self) -> str:
        """Identifies the CELL (everything but seed and policy) for aggregation."""
        return (f"mix{self.mix}_load{self.load}_dev{self.num_devices}"
                f"_slots{self.slots}_gap{self.gap_median_s}"
                f"_rw{self.runaway_fraction}")


def _gap_p95_for(median_s: float, base_workload: dict) -> float:
    """Scale the gap p95 with its median to preserve the lognormal shape."""
    g = base_workload["classes"]["agentic"]["gap_s"]
    ratio = g["p95"] / g["median"] if g["median"] else 15.0
    return median_s * ratio


def run_cell(spec: CellSpec, wall_cap_s: float | None = None) -> dict:
    """Execute one cell and return its manifest (does NOT write it).

    `wall_cap_s` bounds a SINGLE run's wall clock. A run that exceeds it is recorded
    as collapsed, with diagnostics and NO metrics (see the handler below).
    """
    service = load_service_model()
    catalog = load_degradation_catalog()
    base_workload = load_yaml(CONFIG_DIR / "workload.yaml")
    base_policy_cfg = load_policy_config()

    workload = apply_overrides(base_workload, {
        "mix.agentic_token_share": spec.mix,
        "arrivals.base_rate_per_s":
            spec.load * spec.capacity_per_device * spec.num_devices,
        "classes.agentic.gap_s.median": spec.gap_median_s,
        "classes.agentic.gap_s.p95": _gap_p95_for(spec.gap_median_s, base_workload),
        "adversarial.runaway_fraction_of_agentic": spec.runaway_fraction,
    })

    policy_cfg = apply_overrides(base_policy_cfg.get(spec.policy, {}), spec.params)
    policy = build_policy(spec.policy, service, {**base_policy_cfg,
                                                 spec.policy: policy_cfg})

    cluster_cfg = {"num_devices": spec.num_devices,
                   "device": {"hbm_capacity_gib": spec.hbm_gib,
                              "max_concurrent_decodes": spec.slots}}

    started = time.time()
    try:
        with _wall_clock_cap(wall_cap_s):
            result = run_simulation(
                build_cluster(cluster_cfg, service),
                policy,
                WorkloadGenerator(workload, seed=spec.seed, horizon_s=spec.horizon_s),
                RunConfig(horizon_s=spec.horizon_s, warmup_s=spec.warmup_s,
                          seed=spec.seed, tick_interval_s=spec.tick_s),
                degradation_catalog=catalog,
            )
    except RunWallClockExceeded:
        # AMENDMENT 3 FOLLOW-UP (operator ruling, 2026-07-21): a run that exceeds the
        # cap records the COLLAPSE as the outcome and NO metrics.
        #
        # Deliberately no metrics: numbers accumulated up to a cut-off are exactly the
        # right-censored quantity Amendment 2 exists to keep out of the verdict.
        # Writing them under a different name would reintroduce that bias. The readout
        # therefore sees "this policy did not complete here", never a truncated number.
        return {"run_id": spec.run_id(), "cell_key": spec.cell_key(),
                "spec": asdict(spec), "collapsed": True,
                "wall_cap_s": wall_cap_s,
                "wall_seconds": time.time() - started,
                "diagnostics": _collapse_diagnostics(policy)}

    manifest = {"run_id": spec.run_id(), "cell_key": spec.cell_key(),
                "spec": asdict(spec), "collapsed": False,
                "wall_seconds": time.time() - started}
    manifest["metrics"] = {f: getattr(result, f) for f in _METRIC_FIELDS}
    manifest["arrivals_by_class"] = result.arrivals_by_class
    manifest["completions_by_class"] = result.completions_by_class
    manifest["compliant_completion_rate"] = result.compliant_completion_rate()
    return manifest


class RunWallClockExceeded(RuntimeError):
    """A single run exceeded its wall-clock cap (congestion collapse, typically)."""


@contextmanager
def _wall_clock_cap(seconds: float | None):
    """Abort a run that exceeds `seconds` of wall time.

    Uses SIGALRM, which fires inside the interpreter loop, so a run spinning in pure
    Python (the observed niyama_style collapse) is interrupted. Disabled when seconds
    is None, and a no-op off the main thread where signals cannot be installed.
    """
    if not seconds or threading.current_thread() is not threading.main_thread():
        yield
        return

    def _fire(signum, frame):
        raise RunWallClockExceeded(f"run exceeded {seconds:.0f}s wall clock")

    prev = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, prev)


def _collapse_diagnostics(policy) -> dict:
    """Evidence of WHY a run collapsed, so the readout can describe it, not just flag it.

    Queue depth is the discriminator observed in the niyama_style investigation: the
    collapsed trajectory reached a backlog of 60,231 while a healthy sibling seed at
    the same cell and horizon sat at 0.
    """
    out: dict = {}
    q = getattr(policy, "_queue", None)
    if q is not None:
        out["queue_depth"] = len(q)
    for name in ("_running", "_active", "_demotions", "_flows"):
        v = getattr(policy, name, None)
        if isinstance(v, (dict, set, list)):
            out[name.lstrip("_")] = len(v)
    return out


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).resolve().parent), text=True).strip()
    except Exception:
        return "unknown"


def _instance_id() -> str:
    """Identity of the machine a run executed on (for the append-only provenance).

    Uses SIM_INSTANCE_ID if set (e.g. the cloud instance id), else the hostname. This
    is what lets results synced back from a rented box be attributed to it."""
    import os
    import socket
    return os.environ.get("SIM_INSTANCE_ID") or socket.gethostname()


# --- grid construction --------------------------------------------------------------

def load_grid_config(name: str) -> dict:
    return load_yaml(CONFIG_DIR / name)


def load_tuned_params(grid_cfg: dict) -> dict:
    """Map (policy, mix-family) -> tuned params, or empty for defaults.

    The grid uses each policy's TUNED parameters for the cell's mix (spec Section 4:
    tuned per workload family). If no tuned-params file exists, cells run on config
    defaults and the manifest records params={} -- honest, but not the fair
    comparison, so the pilot report flags it.
    """
    path = grid_cfg.get("tuned_params")
    if not path:
        return {}
    full = CONFIG_DIR.parent / path if not os.path.isabs(path) else Path(path)
    if not full.exists():
        return {}
    return json.loads(Path(full).read_text(encoding="utf-8"))


def _params_for(policy: str, mix: float, tuned: dict) -> dict:
    """Tuned params for this policy at this mix, if available."""
    family = f"mix_{int(round(mix * 100)):02d}"
    return tuned.get(policy, {}).get(family, {})


def enumerate_cells(grid_cfg: dict) -> list[CellSpec]:
    cell = grid_cfg["cell"]
    axes = grid_cfg["axes"]
    tuned = load_tuned_params(grid_cfg)

    axis_names = ["mix", "load", "num_devices", "gap_median_s", "runaway_fraction"]
    # runaway_fraction defaults to [0.0] so H1/H2 configs need not declare it.
    axis_values = [axes.get(n, [0.0] if n == "runaway_fraction" else None)
                   for n in axis_names]

    specs: list[CellSpec] = []
    for combo in itertools.product(*axis_values):
        mix, load, num_devices, gap, runaway = combo
        for policy in grid_cfg["policies"]:
            params = _params_for(policy, mix, tuned)
            params_json = json.dumps(params, sort_keys=True)
            for seed in grid_cfg["seeds"]:
                specs.append(CellSpec(
                    policy=policy,
                    params_json=params_json,
                    mix=float(mix),
                    load=float(load),
                    num_devices=int(num_devices),
                    slots=int(cell["slots"]),
                    hbm_gib=float(cell["hbm_gib"]),
                    gap_median_s=float(gap),
                    seed=int(seed),
                    horizon_s=float(cell["horizon_s"]),
                    warmup_s=float(cell["warmup_s"]),
                    tick_s=float(cell["tick_s"]),
                    capacity_per_device=float(cell["capacity_per_device"]),
                    runaway_fraction=float(runaway),
                ))
    return specs


# --- execution ----------------------------------------------------------------------

def _manifest_path(results_dir: Path, spec: CellSpec) -> Path:
    return results_dir / f"{spec.run_id()}.json"


def _run_and_write(args) -> tuple[str, bool]:
    """Worker entry: run one cell and write its manifest. Returns (run_id, ran)."""
    spec, results_dir_str, commit, instance, wall_cap_s = args
    results_dir = Path(results_dir_str)
    path = _manifest_path(results_dir, spec)
    if path.exists():
        return (spec.run_id(), False)   # resumability: already done
    manifest = run_cell(spec, wall_cap_s=wall_cap_s)
    manifest["git_commit"] = commit
    manifest["instance"] = instance
    # Write atomically so an interrupted write never leaves a half-file that a resume
    # would mistake for a completed run.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    tmp.rename(path)
    return (spec.run_id(), True)


def _in_shard(spec: "CellSpec", shard: tuple[int, int] | None) -> bool:
    """Whether this cell belongs to shard i of N (disjoint partition by run_id).

    Sharding lets independent boxes run non-overlapping subsets in parallel (the
    directive authorizes parallel instances). Each box passes --shard i/N; the
    partition is by the run_id hash so it is stable and boxes never duplicate work.
    Results sync back append-only and merge with no collisions.
    """
    if shard is None:
        return True
    i, n = shard
    return int(spec.run_id(), 16) % n == i


def run_grid(grid_cfg: dict, results_dir: Path = RESULTS_DIR,
             workers: int | None = None, progress=None,
             enforce_horizon: bool = True,
             shard: tuple[int, int] | None = None,
             devices: set[int] | None = None,
             wall_cap_s: float | None = None) -> dict:
    """Run every cell in the grid, skipping any already on disk. Returns a summary.

    `enforce_horizon` (default True) applies the Amendment 2 structural guard: a
    real sweep may not run at a right-censoring horizon. Unit tests that use tiny
    synthetic grids pass enforce_horizon=False.

    `shard` = (i, n) runs only the i-th of n disjoint partitions (for parallel boxes).

    `devices` runs only cells at those cluster sizes. This exists so the cheap short
    cells can go on spot while the long ones go on on-demand -- a 6h run is worth
    protecting from reclaim, a 19min run is not. It SELECTS, it never modifies: the
    union of disjoint device sets is exactly the full grid, and
    test_amendment3_axes.py pins that so a filter can never silently drop cells.
    """
    results_dir.mkdir(parents=True, exist_ok=True)

    if enforce_horizon:
        # Amendment 2: refuse to run any sweep at a right-censoring horizon.
        # Structural, not a convention -- the bound is derived from the workload.
        from ..workload.lifetimes import assert_horizon_adequate
        base_workload = load_yaml(CONFIG_DIR / "workload.yaml")
        assert_horizon_adequate(float(grid_cfg["cell"]["horizon_s"]), base_workload,
                                load_service_model(), context="grid config")

    if devices is not None:
        grid_sizes = set(grid_cfg["axes"]["num_devices"])
        unknown = set(devices) - grid_sizes
        if unknown:
            raise ValueError(
                f"--devices {sorted(unknown)} are not in the grid's device axis "
                f"{sorted(grid_sizes)}; refusing to run a filter that matches nothing")

    specs = [s for s in enumerate_cells(grid_cfg)
             if _in_shard(s, shard)
             and (devices is None or s.num_devices in devices)]
    commit = _git_commit()
    instance = _instance_id()

    todo = [s for s in specs if not _manifest_path(results_dir, s).exists()]
    if progress:
        shard_note = f" (shard {shard[0]}/{shard[1]})" if shard else ""
        dev_note = f" (devices {sorted(devices)})" if devices else ""
        progress(f"{len(specs)} cells in this run{shard_note}{dev_note}, "
                 f"{len(todo)} to run, {len(specs) - len(todo)} already on disk")

    if workers is None:
        workers = max(1, (os.cpu_count() or 2) - 2)

    ran = 0
    args = [(s, str(results_dir), commit, instance, wall_cap_s) for s in todo]
    if workers == 1 or len(args) < 2:
        for a in args:
            _, did = _run_and_write(a)
            ran += int(did)
    else:
        with mp.get_context("fork").Pool(processes=workers) as pool:
            for _, did in pool.imap_unordered(_run_and_write, args):
                ran += int(did)

    return {"total": len(specs), "ran": ran,
            "skipped": len(specs) - len(todo), "commit": commit,
            "instance": instance}


def load_results(results_dir: Path = RESULTS_DIR) -> list[dict]:
    """Read every run manifest in the results directory.

    Skips any JSON that is not a run manifest (e.g. the cached scaling probe), so
    auxiliary files in results/ never corrupt aggregation.
    """
    if not results_dir.exists():
        return []
    out = []
    for p in sorted(results_dir.glob("*.json")):
        obj = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and "run_id" in obj and "cell_key" in obj:
            out.append(obj)
    return out
