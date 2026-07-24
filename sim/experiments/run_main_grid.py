"""Run (or project) the SG7 main grid with the resumable runner (brief Section 8).

Two modes:

  --project --workers N   Re-project the full-grid wall time on N workers and check
                          the 24-hour guard. Runs NOTHING. Use this on the cloud box
                          BEFORE launch (PART D): if it still exceeds 24 hours, STOP
                          and report -- do NOT trim the grid (it is frozen per
                          EVAL_SPEC; trimming is a spec change, not a runtime
                          decision).

  --run [--workers N]     Run the full grid. Resumable and append-only: every
                          completed cell is skipped on restart, so an interruption
                          (sleep, crash, spot reclaim) costs zero completed work.
                          Each manifest records the git commit and the instance id
                          (set SIM_INSTANCE_ID on the box).

House style: hyphens only (D-026).
"""

from __future__ import annotations

import argparse
import os

from .grid import RESULTS_DIR, load_grid_config, run_grid
from .pilot import measure_scaling, project_main_grid

_GRID = "grid_main.yaml"


def _project(workers: int) -> None:
    scaling = measure_scaling()   # cached probe; measures if absent
    proj = project_main_grid(scaling, workers)
    cpu_hours = proj["serial_seconds"] / 3600.0
    print(f"Main grid projection on {workers} workers/box:")
    print(f"  total runs        : {proj['total_runs']}")
    print(f"  serial cpu-hours  : {cpu_hours:.0f} (worst-case upper bound)")
    print(f"  1 box wall        : {proj['projected_wall_hours']:.1f} hours")
    print("  sharded across boxes (Amendment 2 raised the guard; parallel instances "
          "authorized, --shard i/N):")
    for boxes in (1, 2, 4):
        wall = cpu_hours / (boxes * workers)
        # rough spot pricing for a workers-vCPU box (~$0.02/vCPU-hr)
        cost = boxes * wall * workers * 0.02
        flag = "" if wall <= 24 else "  <- still > 24h/box"
        print(f"    {boxes} box(es) x {workers} vCPU: ~{wall:.1f}h/box, "
              f"~${cost:.0f} est spend{flag}")
    print("  Budget ceiling $250. NOTE: worst-case pricing (every run = "
          "fastserve_mlfq @ load 1.3 per cluster size); the real grid is materially "
          "faster. Re-measure the scaling probe on the actual box before launch.")


def main() -> None:
    from pathlib import Path

    ap = argparse.ArgumentParser(description="SG7 grid runner")
    ap.add_argument("--project", action="store_true",
                    help="project runtime and check the 24h guard; run nothing")
    ap.add_argument("--run", action="store_true", help="run the grid")
    ap.add_argument("--config", default=_GRID,
                    help="grid config (default grid_main.yaml; also grid_h2/h3)")
    ap.add_argument("--results-subdir", default=None,
                    help="write manifests to RESULTS_DIR/<subdir> (H2/H3 suites)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2),
                    help="worker processes (default: cpu-2)")
    ap.add_argument("--shard", default=None,
                    help="run shard i/N of the grid (this box), e.g. 0/4")
    ap.add_argument("--devices", default=None,
                    help="run only these cluster sizes, e.g. 8,16 (Amendment 3 split: "
                         "short cells on spot, the 6h 32-device cells on on-demand). "
                         "Selects only -- the union of disjoint sets is the full grid.")
    args = ap.parse_args()

    shard = None
    if args.shard:
        i, n = (int(x) for x in args.shard.split("/"))
        shard = (i, n)

    devices = None
    if args.devices:
        devices = {int(x) for x in args.devices.split(",") if x.strip()}

    results_dir = RESULTS_DIR if not args.results_subdir \
        else RESULTS_DIR / args.results_subdir

    # The 24h guard projection applies to the main grid (the frozen H1 matrix).
    if args.config == _GRID and (args.project or not args.run):
        _project(args.workers)
        if not args.run:
            return

    print(f"\nRunning {args.config} on {args.workers} workers "
          f"(instance {os.environ.get('SIM_INSTANCE_ID') or 'local'}"
          f"{', shard ' + args.shard if args.shard else ''}"
          f"{', devices ' + args.devices if args.devices else ''}) ...")
    summary = run_grid(load_grid_config(args.config), results_dir=results_dir,
                       workers=args.workers, progress=lambda s: print("  " + s),
                       shard=shard, devices=devices)
    print(f"  done: {summary['ran']} ran, {summary['skipped']} skipped, "
          f"commit {summary['commit']}, instance {summary['instance']}")
    print(f"  results in {results_dir} (append-only)")


if __name__ == "__main__":
    main()
