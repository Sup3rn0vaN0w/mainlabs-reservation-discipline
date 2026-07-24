# sim/sanity

SG1 sanity harness - NOT the spec Section 5 workload generators (those are SG2, in `sim/workload/`).

- `workloads.py` - `mm1_workload`: single-invocation Poisson/exponential source that reduces the simulator to M/M/1.
- `littles_law.py` - runs the M/M/1-degenerate config; checks L == lambda*W and L == rho/(1-rho).
- `utilization_curve.py` - sweeps offered load; checks utilization tracks rho, is monotonic, stays below 1.

Run via `make sanity` from `sim/`.
