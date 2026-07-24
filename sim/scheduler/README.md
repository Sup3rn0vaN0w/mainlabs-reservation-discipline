# sim/scheduler

The policy plug-in interface (brief Section 4) and policy implementations.

- `interface.py` - the contract: `Policy.on_event(event, state) -> [action]`. Events (`FlowArrival`, `InvocationReady`, `InvocationComplete`, `Tick`), the fixed action vocabulary (`Admit`, `Reject`, `Schedule`, `Preempt`, `Degrade`, `NoOp`), and the read-only `ClusterStateView` / `DeviceView`. Policies never mutate state and never compute physics.

## The rule

The engine owns ALL physics; a policy only decides. A policy does not know what
preemption costs, how decode throughput varies with batch size, or what a KV page is
worth -- it says `Preempt(inv, mode)` and the engine prices it. The only physics
constant a policy touches is the KV rate (`Policy.kv_gib`), and only to avoid
proposing a placement the engine would reject for lack of HBM; the engine re-checks
capacity on every `Schedule` regardless. This is the fairness guarantee: no policy
can win by getting different mechanics.

Tie-breaks use a deterministic total order (priority/level, then flow id, then step),
so no policy needs a random stream and every run is exactly reproducible.

## Policies

| Module | Spec Section 4 | Preempts? | Summary |
|--------|----------------|-----------|---------|
| `policies/fcfs.py` | baseline 5 (the floor) | no | Admit-all, first-fit placement, never preempts. The work-conserving floor and the basis of the SG1 M/M/1 sanity checks. |
| `policies/vllm_style.py` | baseline 1 | yes | Strict class priority (interactive outranks agentic) with preemption under capacity pressure. Supports both spec variants: `recompute` (evicted KV re-prefilled on resume, counted as wasted computation) and `swap` (KV copied to host and back at the modeled bandwidth). Only a strictly-higher-priority invocation may preempt, so preemption cannot cycle. |
| `policies/fastserve_mlfq.py` | baseline 4 | yes | Multi-level feedback queue with token quanta. Invocations enter at level 0 and demote as they consume each level's quantum, approximating shortest-remaining-first without knowing output length up front. Evicts residents that fall outside the top-ranked set the cluster can hold. A preempted invocation keeps its level -- promoting it back to 0 would let long work starve short. |
| `policies/niyama_style.py` | baseline 2 | yes | SLO classes with a latency target each; earliest-deadline-first within a class. A flow that burns through its SLO budget is demoted **deterministically**, at a fixed threshold. That cliff is the deliberate contrast to our probabilistic headroom-keyed degradation, and it is what ablation A3 and hypothesis H2 are built to compare. |
| `policies/concur_style.py` | baseline 3 | pause/resume | AIMD concurrency window: additive increase on every completed flow, multiplicative decrease when the backlog signals congestion. Admission is gated on the window, so it is the only policy that REFUSES work outright rather than degrading it. Overshoot is PAUSED, not discarded -- a pause preserves state (its KV goes to host and comes back), so the engine always prices it as a swap, never a recompute. |
| `policies/mars_style.py` | Amendment 1 | yes | MARS (arXiv 2604.26963): AIMD admission window + MLFQ priority + opportunistic cost-benefit KV retention. The window may OVERSUBSCRIBE the decode slots (`max_window_fraction`), so the MLFQ half genuinely arbitrates the excess -- without that, MARS collapses to plain AIMD admission (CONCUR). On eviction it chooses swap vs recompute per victim by a tuned context-size threshold (retain the KV when re-prefilling it would waste more than a host round-trip). A strong composite baseline, eligible as the per-cell best baseline the reservation discipline is scored against. |
| `policies/reservation.py` | **OURS** (D-049) | best-effort only | The Reservation Discipline. Bounded reserved subset (K), pinned KV in two embodiments (strict / tiered-with-guaranteed-restore), a non-preemption guarantee the ENGINE enforces, cross-invocation token budgets metered at decode, and headroom-keyed probabilistic degradation. Never preempts a reserved flow; preempts best-effort work only, and only to make a tiered pin's guaranteed restore good. |

Pausing (CONCUR) is state-preserving suspension at the scheduler's discretion. It is
NOT the same thing as a non-preemption guarantee: paused work stops making progress
whenever the scheduler decides it should, which is precisely what a reservation
forbids.

The reservation policy holds its compute slot across tool-time gaps, so it strands
capacity by construction. That is not a bug to be tuned away: the primary metric
(goodput per PROVISIONED GPU-hour) charges for it, and whether the guarantee is
worth the stranding is the question H1 exists to answer -- including by killing it.

All five spec Section 4 baselines are implemented.

## The parameters are TUNED, and tuned fairly

`configs/policies.yaml` holds defaults. The numbers that matter come from the SG5
tuning harness (`sim/tuning/`, `make tune`), which gives every policy -- ours
included -- an equal pre-registered grid, chooses parameters on validation seeds,
and scores them on held-out seeds. See `sim/tuning/README.md`.

## Adding a policy

Subclass `Policy`, implement `on_event`, add a case to `build_policy` in
`core/orchestration.py`, and put its parameters in `configs/policies.yaml`. Nothing
in the engine changes.

## Note on the parameters

`configs/policies.yaml` holds SG3 STARTING POINTS, not tuned values. Spec Section 4
gives every baseline a tuning budget equal to ours (pre-registered grid, tuned on
validation seeds, evaluated on held-out seeds); that harness is SG5. No policy
comparison from this build is fit to report.
