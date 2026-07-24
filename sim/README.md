# code/sim/ - T-055 reservation-discipline serving simulator

Discrete-event, invocation-granularity serving simulator for the pre-registered
empirical evaluation in `../../content/research/plane_separation/EVAL_SPEC_T-052.md`
(frozen v1.0, D-052). Built under `../../content/research/plane_separation/SIM_BUILD_BRIEF_T-055.md`.
Lives under `code/` but is kept git-tracked via a `.gitignore` exception
(`!code/sim/`), since it is pre-registered research infra that ships with the
paper. CPU-only, no network dependencies.

House style: hyphens only (D-026).

## Status: SG6 (full metric set + grid runner + pilot)

SG1-SG6 are complete: core engine and sanity checks (SG1), Section 5 workload
generators (SG2), preemption mechanics + vLLM/MLFQ baselines (SG3), the Reservation
Discipline with ledger/pins/budgets/degradation (SG4), the Niyama/CONCUR baselines +
equal-budget tuning harness (SG5), and the full spec Section 2 metric set + resumable
grid runner + pilot readout (SG6). SG7 (full grid + H1/H2/H3 verdict) is next.

See `docs/architecture.md` for the design and what each gate delivered.
Key reports: `docs/TUNING_REPORT.md` (tuned parameters, SG5), `docs/PILOT_READOUT.md`
(pilot H1 signal + runtime projection, SG6). Neither is the H1 verdict -- that is SG7.

## Setup

The project-local virtualenv (git-ignored) is `code/sim/.venv`, Python 3.12.
From the repo root:

```
/opt/homebrew/bin/python3.12 -m venv code/sim/.venv
code/sim/.venv/bin/python -m pip install simpy numpy scipy pyyaml pytest matplotlib
```

## Run

From this directory:

```
make test      # unit + sanity test suite (should be all green)
make sanity     # print the two SG1 sanity tables (Little's law, utilization)
make validate   # generate the SG2 workload validation report + plots
make tune       # SG5 equal-budget tuning harness -> docs/TUNING_REPORT.md + tuned_params.json
make pilot      # SG6 pilot grid + sensitivity + runtime projection -> docs/PILOT_READOUT.md
```

## SG1 sanity evidence

- **Little's law on an M/M/1-degenerate config**: `L == lambda * W` (accounting)
  and `L == rho / (1 - rho)` (physics vs closed form).
- **Utilization vs offered load**: utilization tracks `rho`, is monotically
  non-decreasing, and stays below 1.
