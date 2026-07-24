# SG7 Cloud Runbook (PART D)

# How to run the frozen main grid on a rented CPU box and produce the H1 readout.
# THE RENTAL IS THE OPERATOR'S: this runbook does not provision or spend anything.
# House style: hyphens only (D-026).

## Prerequisites (all must hold before launch)

1. PART B PASS: `docs/SENSITIVITY_HARNESS_CHECK.md` reads PASS.
2. mars_style tuned under the equal-budget harness (fairness test green;
   `configs/tuned_params.json` contains mars_style).
3. AMENDMENT 2 (ratified 2026-07-15): every sweep horizon >= the median agentic
   flow lifetime (derived from the workload; ~666-863s), so the primary metric is
   not right-censored (docs/H3_ZERO_CHECK.md). All sweep configs are set to 1500s
   and `run_grid` refuses a shorter horizon structurally (test_horizon_lock.py).
4. `tuned_params.json` regenerated AT the 1500s horizon (`make tune`) -- params
   tuned at 300s optimize a censored objective and must not drive the final grid.

The grid is FROZEN per EVAL_SPEC. Nothing here trims it. Amendment 2 RAISED the
24-hour guard for this authorized cloud run and permits parallel instances: shard
cells across boxes (`--shard i/N`) to keep wall time under ~24h per box. Spend
ceiling: 250 USD.

## 1. Provision (operator owns the transaction)

- A 32 to 64 vCPU instance, CPU-only, ~1 day. Any Linux box with Python 3.12.
- Reference math: the local worst-case projection is ~44 hours on 6 workers (an
  upper bound -- it prices every run at the slowest policy). On 32 workers that is
  ~8 hours; on 64, ~4. The real grid is faster than the bound.
- The operator provides credentials / approves the spend, then hands back the box.

## 2. Deploy (git; the repo is CPU-only)

```
git clone <repo> && cd 03_main_labs
git checkout <the commit under test>      # recorded in every manifest
/usr/bin/python3.12 -m venv code/sim/.venv    # or the box's 3.12
code/sim/.venv/bin/python -m pip install simpy numpy scipy pyyaml pytest matplotlib
export SIM_INSTANCE_ID=<instance-id>       # stamped into every manifest
cd code/sim && make test                    # sanity: all green before a long run
```

## 3. Re-project and check the 24-hour guard BEFORE launch

```
cd code && code/sim/.venv/bin/python -m sim.experiments.run_main_grid \
    --project --workers <N>
```

If it prints EXCEEDED: STOP and report. Do not trim.

## 4. Run the grid (resumable, append-only, sharded)

Single box:

```
cd code && code/sim/.venv/bin/python -m sim.experiments.run_main_grid \
    --run --workers <N>
```

Parallel boxes (authorized) -- each box runs a disjoint shard i of N:

```
# box 0:  SIM_INSTANCE_ID=box0 ... run_main_grid --run --workers <N> --shard 0/4
# box 1:  SIM_INSTANCE_ID=box1 ... run_main_grid --run --workers <N> --shard 1/4
# box 2, 3: --shard 2/4, --shard 3/4
```

Shards partition cells by run_id hash, so boxes never duplicate work. Worst-case
projection: the main grid is ~1533 cpu-hours; 4 boxes x 32 vCPU is ~12h/box at
~$31 (well under the $250 ceiling; the real grid is faster). Re-measure the scaling
probe on the actual box first: delete `experiments/results/scaling_probe.json` so it
re-measures, then `make grid-project WORKERS=<N>`.

Also run the secondary sweeps (same pattern, own result dirs):

```
run_main_grid --run --config grid_h2.yaml --results-subdir h2 --workers <N>
run_main_grid --run --config grid_h3.yaml --results-subdir h3 --workers <N>
make ablations-run          # A1-A5
```

- Resumable: every completed cell writes its own manifest and is skipped on
  restart. A crash, a spot reclaim, or a reboot costs zero completed work.
- Append-only: `experiments/results/` accumulates one JSON per run, each carrying
  the git commit and SIM_INSTANCE_ID. Nothing is overwritten.
- Start from an EMPTY results dir (a fresh clone is empty). If the grid config or the
  cell schema ever changes, do not run into an old results dir -- stale manifests
  from a superseded schema would pollute the set. The H1 readout guards against this
  (it hard-errors if two cell keys describe the same workload), but a clean dir is
  the norm.

## 5. Sync results back

Copy `code/sim/experiments/results/*.json` back to the repo host (rsync/scp). They
are append-only and idempotent: re-syncing the same manifests changes nothing, and
the H1 readout is a pure function of the manifest set. Commit the results (or archive
them) as the durable evidence behind the verdict.

## 6. Produce the FINAL H1 readout (SG7 deliverable)

After all shards and the H2/H3/ablation sweeps have synced back to one
`experiments/results/` tree:

```
cd code && code/sim/.venv/bin/python -m sim.analysis.h1_readout \
    --final --scope "full grid, <horizon>s, instances <ids>"
```

Writes `docs/H1_READOUT_FINAL.md`: the mechanical verdict against the unchanged spec
Section 3 thresholds -- per-cell medians, best-baseline-per-cell (mars_style
eligible), bootstrap CIs, corner-cell exclusions, H2 AUC, H3 absolute rates + deltas.
The stale-manifest guard hard-errors if the result set mixes schemas or horizons, so
a clean tree is required (start from empty; see the note in step 4).

## 7. What the verdict triggers (D-058, mechanical -- no re-decision)

- SUPPORT or WEAK -> counsel files the provisional.
- KILL -> a same-day no-file is recorded.
- Either branch: both papers still publish. H2/H3 shape the papers only, never the
  filing.
