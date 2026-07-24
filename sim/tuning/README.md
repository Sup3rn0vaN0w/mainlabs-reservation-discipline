# sim/tuning

The baseline-fairness tuning harness (spec Section 4).

Spec Section 4, verbatim: *"Each baseline gets a tuning budget equal to ours: a pre-registered grid over its policy parameters, tuned per workload family on validation seeds, evaluated on held-out seeds."*

- `harness.py` - grid enumeration, evaluation, selection, and two self-checks (`check_equal_budget`, `check_seed_split`) that are asserted in the test suite.
- `../configs/tuning.yaml` - the PRE-REGISTERED grid: budget, seed split, workload families, and each policy's axes.
- `report.py` - writes `../docs/TUNING_REPORT.md`: the grid as searched, and the chosen parameters per policy per family.

## Why this exists

The easiest way to manufacture a win for the reservation discipline would be to
compare a lovingly-tuned version of it against baselines left at their defaults.
This module exists to make that impossible:

- **Equal budget.** Every tunable policy gets exactly `budget` grid points. A test
  fails the build if any grid is a different size. We do not get to search harder.
- **Held-out evaluation.** Parameters are chosen on validation seeds; the chosen
  parameters are scored on held-out seeds the tuner never saw. The sets are asserted
  disjoint.
- **Same objective.** Everyone maximizes goodput per PROVISIONED GPU-hour -- the
  metric that already charges us for the capacity our own pins strand.
- **Per family.** No baseline is penalized for a setting that suited one regime.

FCFS is the no-control floor and has no parameters; it is listed in
`untuned_policies` so its absence from the grids is a stated decision, not an
oversight.

Run: `make tune` (from `code/sim`).
