# sim/experiments

The experiment grid runner (brief Section 3 and Section 8).

- `grid.py` - enumerates the experiment matrix from a grid config, runs each
  `(cell x seed x policy)` in parallel, and writes an APPEND-ONLY manifest per run
  (keyed by a stable hash of its parameters). Resumable by construction: a restarted
  grid skips any manifest already on disk, so an interrupted overnight run costs zero
  completed work. Writes are atomic (tmp-then-rename), so a crash mid-write never
  leaves a partial file a resume would mistake for done.
- `sensitivity.py` - the service-constant sensitivity check (brief Section 5):
  perturb the three most influential LOW-CONFIDENCE constants +/- 30 percent on one
  representative cell and report how far the reservation-vs-baseline delta moves.
  Quantifies the fidelity caveat instead of just declaring it.
- `pilot.py` - runs the pilot grid, aggregates per cell (median across seeds, best
  baseline per cell, bootstrap CIs), classifies each cell support/weak/kill against
  the spec Section 3 thresholds, checks the interactive guardrail, runs the
  sensitivity check, measures per-cluster-size runtime, projects the full grid
  against the 24-hour guard, and writes `docs/PILOT_READOUT.md`.

- `run_main_grid.py` - the SG7 CLI. `--project --workers N` re-checks the 24-hour
  guard on the box's worker count (runs nothing; never trims the frozen grid).
  `--run` runs a grid, resumable and append-only; each manifest records the git
  commit and the instance id (`SIM_INSTANCE_ID`). `--config`/`--results-subdir`
  drive the H2 and H3 sweeps into their own result dirs.

Configs: `configs/grid_pilot.yaml` (SG6, small subset for strategy review),
`configs/grid_main.yaml` (SG7 H1, the full matrix), `configs/grid_h2.yaml` (H2 load
sweep 0.5x-3.0x), `configs/grid_h3.yaml` (H3 runaway injection 0/1/5/10 percent).

## Results

Manifests are written to `results/` (git-ignored -- run outputs are regenerable, not
source). Each is a self-contained JSON: the full parameterization, git commit, and
every metric. A re-run with identical parameters is skipped; a re-run with changed
parameters is a new hash and a new file. Nothing is ever overwritten.

## The H1 readout

`make h1-readout` (in `sim/analysis/h1_readout.py`) consumes the manifest set and
emits `docs/H1_READOUT.md` -- the mechanical verdict against the spec Section 3
thresholds. Still to come at SG7: the ablation sweeps A1-A5 (all reachable by
reservation config; the sweep harness is the remaining build item).

Run: `make pilot`, then `make h2-run h3-run`, then `make h1-readout` (from
`code/sim`). The full H1 verdict is the main grid run on the cloud box per
`docs/SG7_CLOUD_RUNBOOK.md`.
