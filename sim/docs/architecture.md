# T-055 Simulator Architecture

Discrete-event serving simulator for the pre-registered evaluation in
`content/research/plane_separation/EVAL_SPEC_T-052.md` (frozen v1.0, D-052), built
under `SIM_BUILD_BRIEF_T-055.md`. This document is written so a human engineer can
take the codebase over cold (brief Section 7).

House style: hyphens only (D-026).

## SG1 scope (this commit)

SG1 delivers the **core engine + cluster model + FCFS floor policy + smoke test**
with the two required sanity checks. It is the skeleton the rest of the build hangs
off. Everything the brief assigns to SG2-SG7 (Section 5 workload generators, the
four remaining baselines, the reservation policy and its ledger, the grid runner,
the full metric set, bootstrap CIs, the verdict machinery) is explicitly deferred
and marked as such in code.

## Load-bearing design rule: policy/engine separation (brief Section 4)

The single most important invariant. Two halves that never leak into each other:

- **The engine owns all physics and mechanics.** Prefill cost, decode throughput,
  KV footprint, host-spill latency, capacity limits, busy-time accounting - all in
  `sim/cluster/service_model.py` and `sim/core/engine.py`. The physics are
  identical for every policy. This is the fairness guarantee: no policy can win by
  getting different mechanics.
- **A policy is a pure decision module.** `Policy.on_event(event, state) -> [action]`.
  It receives events and a read-only cluster snapshot and returns actions from a
  fixed vocabulary. It never mutates state, never computes a service cost, never
  reads a physics constant.

Because every policy in an experiment cell is driven by the same engine over the
same seeded workload realization, cross-policy comparisons are paired and fair by
construction.

## Control flow

```
workload source (iterator of (arrival_time, flow))
        |
        v
  arrival loop  --FlowArrival-->  policy  --Admit/Reject-->  engine
        |                                                      |
        |                                             (on Admit) make ready
        |                                                      |
        |                                       --InvocationReady--> policy
        |                                                      |
        |                                            --Schedule--> engine
        |                                                      |
        |                                            reserve slot + HBM
        |                                                      |
        |                                     prefill (head latency) -> decode
        |                                                      |
        |                                       device serve loop (processor
        |                                       sharing, batch-size-dependent)
        |                                                      |
        |                                     invocation complete -> release
        |                                                      |
        |                    advance flow (next invocation ready) OR flow done
        |                                                      |
        |                                  --InvocationComplete--> policy
        v                                                      |
     horizon                                        --Schedule queued work-->
```

### The device decode model (the subtle part)

Decode is **piecewise-constant processor sharing**. Each device runs one serve
loop. Within an interval the per-request decode rate is fixed by the current decode
batch size `b`: aggregate `throughput(b) = min(b * single_stream, peak)`, per
request `throughput(b) / b`. When the batch composition changes - an invocation
finishes prefill and joins decode, or one completes - the loop recomputes.

Implementation notes that matter for correctness:

- The serve loop **snapshots** the decode batch at the start of each interval and
  only credits service to that snapshot, so an invocation that joins mid-interval
  is not credited service it did not receive.
- Waking/recomputing a device uses `_poke_device`. It is a **no-op when the current
  process is that device's own serve loop** (a self-interrupt is illegal in SimPy
  and unnecessary - the loop re-snapshots on its next iteration). Otherwise it
  succeeds the idle event (device was passivated) or interrupts the running timeout
  (device was mid-interval).

At decode batch cap 1 this reduces to a single-server constant-rate queue, which is
exactly M/M/1 under Poisson arrivals and exponential service - the basis of the SG1
sanity checks.

### Prefill and KV

Prefill is modeled as a per-invocation head-of-service latency
(`prefill_tokens / prefill_tokens_per_s`) before the invocation joins the decode
set. KV footprint is reserved statically at schedule time for the invocation's
maximum context (`prefill_tokens + output_tokens`). Dynamic mid-decode KV growth,
host-spill under HBM pressure, and prefill/decode compute contention are wired but
not stressed until SG3/SG4; the SG1 sanity configs provision ample HBM so paging
never triggers.

## Determinism (brief Section 4, spec Section 8)

`SeedManager` derives named numpy generators from one global seed via
`SeedSequence([global_seed, crc32(name)])`. The mapping name -> stream is stable
and order-independent, so every policy in a cell can be driven by the identical
workload realization while policy-internal randomness draws from a separate
policy-local stream. Same seed -> byte-identical run (tested).

## Workload model (SG2, spec Section 5)

`sim/workload/` generates a seeded, reproducible stream of `(arrival_time, Flow)`
pairs -- the `WorkloadSource` the engine consumes. Four composable parts:

- **Flow structure** (`flow_structure.py`): per-flow invocation count, per-step
  input/output tokens, accumulated KV context, and inter-invocation tool-time gaps,
  all lognormal(median, p95). Runaway flows amplify count and token draw.
- **Mix** (`mix.py`): the spec expresses the interactive/agentic mix by TOKEN
  VOLUME (90/10, 70/30, 50/50). Since agentic flows carry ~70x the tokens of a
  chat turn, the per-flow agentic probability is solved so the achieved token share
  matches target -- which makes agentic flows only ~0.6 percent of arrivals at
  30 percent token share (expected, validated).
- **Arrivals** (`arrivals.py`): non-homogeneous Poisson (diurnal modulation + burst
  episodes) via thinning.
- **Adversarial** (`generator.py` + `flow_structure.py`): runaway injection into a
  configurable fraction of agentic arrivals (spec sweep 0/1/5/10 percent).

Determinism: `WorkloadGenerator` builds fresh per-stream RNGs from its seed on each
`__iter__`, so re-iteration replays the identical workload -- the mechanism that
lets one generator drive every policy in a cell as a paired comparison. Validated
by `make validate` (report + distribution plots in `docs/validation/`).

The data model carries two SG2 fields the generators populate: `context_tokens`
(accumulated KV context, honored by the KV reservation) and `gap_after_s`
(inter-invocation tool-time, honored by the engine as of SG3 -- see below).

## Inter-invocation tool-time (SG3, spec Section 5)

When an invocation completes, its flow's next invocation becomes ready only after
`gap_after_s` has elapsed. The flow holds no device across the gap. This is the
mechanism behind capacity stranding: a work-conserving policy (FCFS, vLLM, MLFQ)
gives the capacity away during the gap, while a reservation policy will hold it
(SG4) -- and the primary metric, goodput per PROVISIONED GPU-hour, is what prices
that choice. Tested: sojourn spans service + gap, and the device is idle meanwhile.

## Preemption cost (SG3, spec Section 4)

The engine owns what preemption COSTS; a policy only chooses the mode by tagging its
`Preempt` action. Both spec Section 4 variants are implemented:

- **RECOMPUTE** -- the evicted KV is discarded. On resume the invocation re-prefills
  its *materialized context* (`max_context_tokens - decode_remaining`, i.e. prompt
  plus everything generated so far). Those re-prefilled tokens are counted as
  **wasted computation**. Generated tokens are NOT lost -- they are recomputed.
- **SWAP** -- the KV is copied to host memory and back at the modeled interconnect
  bandwidth. Nothing is re-prefilled; the price is paid in bandwidth and latency.

The invariant that matters, and is tested: preemption never loses or invents a
token. Every invocation generates exactly its `output_tokens`, however many times it
is evicted.

### Why the decode loop "settles"

Preemption can land mid-interval. Any change to a device's decode set -- an
invocation joining from prefill, completing, or being evicted -- first SETTLES the
device: elapsed service since `interval_start` is credited, at the interval's rate,
to exactly the invocations that were decoding over it. Only then is the set mutated
and a fresh interval opened. This is what makes a preempted invocation keep credit
for the tokens it really generated, and stops any invocation from being credited
service it never received.

### Deferred re-ready

Evicting an invocation makes it reschedulable. If the engine re-offered it to the
policy immediately, a policy that preempted A to make room for B could see A come
straight back and take the very slot it just freed -- before B was ever scheduled.
So preempted invocations are queued on `_reready` and re-offered as
`InvocationReady` only after the current event's entire action list has executed.

Preemption cannot cycle: vLLM-style only lets a *strictly* higher-priority
invocation preempt, and MLFQ only evicts invocations that fall outside the
top-ranked set the cluster can hold.

## Configuration (brief Section 5: zero magic numbers)

Every parameter lives in `sim/configs/*.yaml`, annotated with its spec section or
literature anchor. Service-model constants are placeholders marked **LOW
CONFIDENCE** (calibrated in Phase 2). Workload parameters are marked either
`spec Section 5` (given in the frozen spec) or `ASSUMPTION` (spec fixes the
family/behavior, not the value -> swept in sensitivity). The SG1 sanity checks are
dimensionless and do not depend on the service constants' values.

## Layout

| Path | Role |
|------|------|
| `sim/core/` | engine, clock, domain types, seeds, orchestration |
| `sim/cluster/` | service physics, device model, N-device fleet |
| `sim/scheduler/` | policy plug-in interface + policies (FCFS, vLLM-style, MLFQ) |
| `sim/workload/` | Section 5 generators + validation (SG2) |
| `sim/metrics/` | collectors + RunResult (SG1 subset + preemption counts) |
| `sim/sanity/` | SG1 degenerate workloads + the two sanity checks |
| `sim/configs/` | YAML configs (service model, cluster, sanity, workload, policies) |
| `sim/tests/` | unit + sanity tests (`make test`) |
| `sim/docs/` | this document; `docs/validation/` = SG2 report + plots |

## Running

From `code/sim/`:

```
make test      # full unit + sanity suite
make sanity    # print the two SG1 sanity tables
make validate  # generate the SG2 workload validation report + plots
```

The project-local virtualenv is `code/sim/.venv` (Python 3.12; SimPy, numpy,
scipy, pyyaml, pytest, matplotlib). It is git-ignored. The importable package is
`sim` (the package root is `code/sim/`; `make` runs from `code/` so `import sim`
resolves).

## The Reservation Discipline (SG4) -- our policy

This is the mechanism the paper and the provisional are about (D-049). One
mechanism, four coupled parts, all enforced by the engine rather than trusted to
the policy:

**1. Bounded reserved subset.** At most K of cluster concurrency may be reserved
(K swept 1-30 percent, ablation A5). Only long-horizon classes are eligible --
reserving a one-second chat turn would burn the bounded subset on work that needs
no guarantee. Everything else is served best-effort from an aggregate class.

**2. Non-preemption guarantee.** A reserved flow is never evicted. The ENGINE
raises if any policy tries, so the guarantee cannot be quietly taken back under
pressure. Ablation A2 admits with the guarantee off.

**3. Pinned KV across tool-time, two embodiments (ablation A4).**

| | HBM across the gap | Restore cost |
|---|---|---|
| STRICT | held (stranded) | none |
| TIERED | spilled to host, given back to best-effort work | pays spill-in on return |

Both hold the COMPUTE slot across the gap. That held-but-idle slot is the
stranding, and the primary metric -- goodput per **provisioned** GPU-hour --
charges us for every second of it. There is no separate fudge factor to argue
about later. Tiered's "guaranteed restore" is made good by evicting best-effort
work to clear room; best-effort work is preemptible, so the restore can never be
blocked.

**4. Cross-invocation token budget**, metered by the engine at decode and exact:
a flow generates its budget and not one token more, then is abandoned. Every flow
the discipline admits gets one, reserved or not -- a cap checked per-request cannot
stop a flow that loops, which is precisely the hole H3 tests. The budget is sized
from the flow's CLASS, never from its own intended draw (that would hand a runaway
flow a runaway budget). Ablation A1 swaps it for a per-request cap -- the baselines'
behavior.

**Plus headroom-keyed probabilistic degradation.** As headroom falls, flows are
degraded with a probability read off a pre-registered piecewise-linear curve.
Probabilistic, so load sheds smoothly instead of cliff-edging a class at a
threshold. A degradation shrinks a flow's remaining work and forfeits part of its
value weight -- which is what the H2 served-value curve measures. Ablation A3 swaps
the headroom key for queue depth. The draw comes from a policy-local seeded stream,
so it never perturbs the workload the baselines see.

### The ledger, and who is trusted

The POLICY maintains the reservation ledger and makes the admission decision. The
ENGINE validates it: a pin that would over-commit compute or HBM is a hard error,
not a silent over-subscription. Tested: over-commit refused, pin accounting exact
across idle gaps, budget metering exact at decode.

## Policies implemented

| Policy | Spec Section 4 | Preempts? | Notes |
|--------|----------------|-----------|-------|
| `fcfs` | baseline 5 (floor) | no | admit-all, first-fit, work-conserving |
| `vllm_style` | baseline 1 | yes | class priority; recompute *and* swap variants |
| `niyama_style` | baseline 2 | yes | SLO classes, EDF within class, **deterministic** demotion at a fixed cliff |
| `concur_style` | baseline 3 | pause/resume | AIMD concurrency window; gates admission (the only policy that REFUSES work); pauses are state-preserving swaps |
| `fastserve_mlfq` | baseline 4 | yes | token-quantum MLFQ, demotion on tick, swap |
| `mars_style` | Amendment 1 | yes | AIMD admission + MLFQ + cost-benefit KV retention; window oversubscribes slots so MLFQ engages |
| `reservation` | OURS (D-049) | best-effort only | ledger, pins, budgets, degradation |

All five spec Section 4 baselines plus the Amendment 1 mars_style baseline are
implemented. Adding a policy means writing one `Policy` subclass and adding a case to
both `build_policy` (app / grid) and the tuning harness `_BUILDERS` (kept in sync by
`tests/test_policy_registry.py`) -- nothing in the engine
changes. That is the plug-in interface doing its job.

## One queue for every policy (SG5)

Every queue-based policy uses the same `PolicyQueue` (`sim/scheduler/queueing.py`),
for the same reason the engine gives every policy the same service physics: a
comparison where a baseline looks bad because of how ITS queue is stored is not a
comparison. Two properties matter:

- **O(log n) push/pop, no per-event re-sort.** The naive "sort the ready list on
  every event" is O(n log n) per event, which becomes O(n^2) over a run once the
  backlog grows under overload. Measured before the fix, a single 300s run took 228s
  for Niyama and 153s for vLLM -- while policies that bound their own backlog ran in
  under 2s. That gap was a data-structure artifact that penalized exactly the
  work-conserving baselines, and it would have blown the SG7 runtime guard. After
  the fix the worst run is ~5s.
- **A scan budget applied identically to all.** A scheduling pass looks at the head
  of the queue, not the whole backlog -- and never rebuilds the queue from only the
  part it looked at. Work that is not placed STAYS queued; a conservation test
  (`test_no_work_lost.py`) asserts across every policy that no admitted flow ever
  silently disappears.

## Guaranteed restore, actually guaranteed (SG5)

Tiered pinning nearly shipped a broken guarantee, and the engine's own invariant
caught it during tuning. When a tiered pin spills, its HBM becomes physically free
and best-effort work may borrow it (best-effort work is preemptible, so it can be
evicted to hand the memory back). The bug: a NEW pin was also allowed to claim that
memory -- but new pins are not preemptible, so the spilled flow could never get its
HBM back, and `restore_pin` raised. The fix distinguishes **free** HBM (what
best-effort work may use, spilled pins included) from **committed** HBM (owed to
every pin, spilled or not). New pins are admitted against committed HBM; best-effort
work against free HBM. That distinction is what makes "guaranteed restore" a
guarantee rather than a hope.

## Baseline fairness: the tuning harness (SG5)

Spec Section 4: *"Each baseline gets a tuning budget equal to ours."* The harness
(`sim/tuning/`, config `configs/tuning.yaml`, run with `make tune`) exists to make
it impossible for us to quietly cheat:

- **Equal budget.** Every tunable policy gets exactly the same number of grid points.
  A test fails the build if any grid differs -- we do not get to search harder than
  the baselines do.
- **Validation / held-out split.** Parameters are chosen on validation seeds and
  scored on held-out seeds the tuner never saw. Asserted disjoint.
- **Same objective.** Everyone maximizes goodput per PROVISIONED GPU-hour -- the
  metric that already charges us for the capacity our own pins strand.
- **Tuned across the load region, not at one load.** This one is load-bearing. These
  policies are strongly regime-dependent: measured on this cluster, the reservation
  discipline *loses* at moderate overload and *wins* at heavy overload. Tuning at a
  single load would let each policy be fitted to the regime that suits it -- and
  would let us pick the load that flatters us. The objective is therefore averaged
  over loads spanning the pre-registered core operating region (0.7-1.3x), all of
  it above the corner-cell exclusion (spec Section 5 declares < 0.6x unrealistic).

**Capacity is calibrated, not guessed.** The floor's sustainable throughput saturates
at ~32.3 completed flows/s on the tuning cluster, so 1.0x load == 32 arrivals/s.
Re-measure if the cluster or the service constants change.

The output is `docs/TUNING_REPORT.md`: the grid as searched, and the parameters it
chose, per policy per workload family.

## Full metric set + grid runner (SG6)

The metrics collector now computes the entire spec Section 2 set: the primary metric
(goodput per provisioned GPU-hour), tail flow-completion (p95/p99, agentic), the
interactive guardrail (p95 TTFT and ITL), wasted-computation fraction (tokens
generated by flows that never complete), served-value fraction (H2), reserved-idle
(stranding), and per-class arrival/completion counts (H3). Latency-model
simplifications (TTFT = admission wait + prefill; ITL = decode span / tokens) are
declared in `metrics/collectors.py` and, being consistent across policies, do not
affect the relative guardrail comparison.

The grid runner (`sim/experiments/grid.py`) runs the experiment matrix and writes an
APPEND-ONLY manifest per run, keyed by a stable hash of its parameters:

- **Resumable (brief Section 8):** a restarted grid skips any manifest already on
  disk, so an interrupted overnight run costs zero completed work. Writes are atomic
  (write-tmp-then-rename), so a crash mid-write never leaves a half-file a resume
  would mistake for done.
- **Manifested:** each result records the full parameterization, git commit, and
  every metric -- enough to reproduce and to audit the eventual verdict.
- **Parallel:** `min(cpu-2, ...)` workers; runs are independent by construction.

`sim/analysis/stats.py` provides the percentile-bootstrap CIs (median aggregation,
paired relative-improvement CIs) the readout reports. `make pilot` runs the pilot
grid (`configs/grid_pilot.yaml`), the service-constant sensitivity check, and the
runtime projection against the 24-hour guard, writing `docs/PILOT_READOUT.md`.

## What is still deferred (post-SG6)

- The FULL grid (`configs/grid_main.yaml`) is authorized at SG7, not run at SG6.
- The mechanical H1_READOUT (verdict vs Section 3 thresholds), the H2 load sweep
  (0.5x-3.0x, AUC), the H3 adversarial sweep, and ablations A1-A5 are SG7.
- **Nothing in this build is the verdict.** `docs/TUNING_REPORT.md` is tuned
  parameters; `docs/PILOT_READOUT.md` is a subset of cells for strategy review. The
  H1 verdict is the full grid with CIs, per-cell best-baseline selection, and
  corner-cell exclusions -- SG7.
