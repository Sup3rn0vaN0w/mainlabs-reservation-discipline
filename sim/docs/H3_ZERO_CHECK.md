# H3 Zero-Baseline Check (PART B.1)

# Ordered diagnostic: is niyama_style / vllm_style completing 0.000 compliant flows
# at 0 percent runaway injection genuine policy behavior or a harness artifact?
# House style: hyphens only (D-026). Surface: code/sim.

Date: 2026-07-15. Cell: the H3 configuration (mix 0.5, load 1.2, 4 devices x 16
slots, gap median 20s, horizon 200s), runaway injection 0 percent.

## CONCLUSION

1. The 0.000 is GENUINE policy behavior -- long-flow starvation, NOT a measurement
   artifact. Instrumented below.
2. BUT the diagnostic uncovered a separate, larger problem: the sweep HORIZONS are
   shorter than the agentic flow lifetimes, which suppresses absolute completion
   across ALL policies and materially biases H1. This is a config artifact and it is
   ESCALATED (Section "Horizon adequacy") -- it affects the H1 verdict, not just H3.

## Method

One instrumented run per policy at the H3 cell, counting the fate of every compliant
(non-runaway agentic) flow: did it ever get scheduled (start), did it make progress
(complete >= 1 invocation), did it complete, was it preempted, is it still in flight
at the horizon.

## Evidence -- the 0.000 is genuine starvation

Compliant agentic flows: 107 arrived.

| Policy | started | progressed | completed | preemptions | in flight at horizon |
|--------|--------:|-----------:|----------:|------------:|---------------------:|
| niyama_style | 1 | 1 | 0 | 0 | 107 |
| vllm_style | 1 | 1 | 0 | 0 | 107 |
| reservation | 107 | 106 | 14 | 0 | 93 |
| fastserve_mlfq | 107 | 70 | 8 | 129 | 99 |

niyama_style and vllm_style SCHEDULE only 1 of 107 agentic flows, with ZERO
preemptions. So the 0.000 is not preemption churn and not a counting bug -- it is
admission/scheduling starvation: under interactive-class priority, long agentic flows
never get a device slot while interactive traffic saturates the cluster. That is
exactly the failure mode the reservation discipline exists to prevent, and it is real
policy behavior. reservation, by contrast, schedules all 107 (its reservations hold
slots for agentic flows) and completes 14.

Determination: GENUINE. No code bug; no fix to the metric definition. The metric was
correct -- niyama/vllm genuinely complete zero compliant flows here.

## Horizon adequacy (the escalation -- affects H1)

The same instrumentation showed why even the policies that DO schedule agentic flows
complete so few: the horizon is too short for the workload.

- Agentic flow minimum lifetime (sum of tool-time gaps + decode service, with NO
  queueing): median 863s; only 21.5 percent of agentic flows could complete in under
  200s even on an idle cluster.
- The main grid uses a 300s horizon, H2 uses 150s, H3 uses 200s. All are far below
  863s. So agentic flows overwhelmingly do NOT complete within the measurement window
  under ANY policy.

Why this biases H1 specifically: the reservation discipline holds a device slot idle
across each tool-time gap for an agentic flow. If the horizon ends before that flow
completes, the reservation is pure stranding with ZERO completion payoff -- the worst
possible case for the mechanism, and an artifact of the horizon, not of the mechanism.

Quantified, on one core cell (mix 0.3, load 1.0, 4 devices; reservation vs vllm, the
best baseline there):

| Horizon | reservation goodput | vllm goodput | reservation vs vllm |
|--------:|--------------------:|-------------:|--------------------:|
| 300s | 22141 | 28493 | -22.3% |
| 1500s | 27049 | 29331 | -7.8% |
| 3000s | 26818 | 29411 | -8.8% |
| 6000s | 26926 | 29525 | -8.8% |

The deficit is HALVED (from -22% to -8%) once the horizon is long enough for agentic
flows to complete, and it stabilizes by 1500s. At 6000s the reservation completes
MORE flows in absolute terms than vllm (211,615 vs 177,142) yet still scores lower
goodput (value shed to degradation) -- a real effect, but a very different picture
from the -22% the 300s horizon reports.

The preliminary H1 verdict (KILL, median -12 percent) was computed entirely at the
300s horizon. It is therefore biased against the mechanism by roughly this margin. It
remains a loss at an adequate horizon on this cell (-8 percent, still below the +5
percent WEAK bar), but the magnitude, and possibly the cross-cell median, change
enough that the FINAL grid must not run at 300s.

## Actions

- H3 and H2 sweep horizons lengthened to an adequate value (agentic flows can
  complete); the H3 readout now reports ABSOLUTE compliant completion rates at each
  injection level alongside the deltas (ordered).
- Regression test added: niyama/vllm starve agentic flows under saturating interactive
  load (started << arrived), locking the genuine-starvation finding.
- ESCALATED to the operator, BLOCKING the final grid: the main-grid (H1) horizon must
  be set to an adequate value (>= ~1500s) before the final grid runs, or the H1
  verdict is a horizon artifact. This also multiplies the runtime (~5x per run) and so
  re-opens the 24-hour guard (see PART C re-projection). The horizon is a build-surface
  config choice (the spec did not freeze it), but because it changes the verdict it
  should be ratified before the grid that fires D-058.
