# T-055 Ablation Readout (A1-A5)

Per-element effect of the Reservation Discipline: the tuned discipline with one
element removed at a time. Papers and prosecution evidence only -- **this document
does not feed the H1 verdict and does not trigger D-058**. The filing decision rests
solely on `H1_READOUT_FINAL.md` (verdict: KILL).

House style: hyphens only (D-026).

**Two cells, 162 runs.** The follow-up run at a second operating point OVERTURNED the
headline finding of the first. Read section 3 before quoting anything from section 2.

| | cell A (original) | cell B (contended) |
|---|---|---|
| devices / slots | 4 / 16 | 4 / **64** |
| offered load | 1.0 | **1.3** |
| gap median | workload default | **20s** (grid minimum) |
| runs | 81 | 81 |
| results dir | `results/ablations/` | `results/ablations_contended/` |
| `full` goodput | 22,942 | 38,411 |

Both: 9 variants x 3 mixes x 3 seeds, horizon 1500s, warmup 150s, 1500s tuning,
commit 99706eb, all complete, no collapses.

---

## 1. READ THIS BEFORE THE TABLE

**Reserved capacity sits idle 94-95 percent of the run in this cell.** The reserved
subset is therefore almost never contended, and the elements that govern reserved-flow
behaviour never engage:

| element | ablation | why its null result is uninformative here |
|---|---|---|
| cross-invocation token budget | A1 | no flow approaches its budget, so disabling it changes nothing |
| non-preemption guarantee | A2 | reserved flows are not being targeted for preemption, so removing the guarantee changes nothing |
| pinned-KV embodiment | A4 | HBM never gets tight enough to spill, so strict and tiered behave identically |

Their deltas are **0.0 percent to four significant figures across all three mixes and
all three seeds**. Three independent ablations returning byte-identical aggregates is
a statement about the experiment, not about the mechanism. Do NOT report A1, A2 or A4
as evidence that those elements are useless -- the correct reading is **"not exercised
at this operating point"**.

The overrides themselves are applied correctly: `_run_one` calls
`apply_overrides(cfg, override)` after the tuned params, and A3 moves 15-24 percent,
which is only possible if overrides reach the policy.

**If this table is going into the paper it must be re-run at a cell where the
mechanism binds** -- higher offered load, and/or the memory-bound 64-slot
configuration where peak decode and KV are shown to bind
(`docs/SENSITIVITY_HARNESS_CHECK.md`, PART B.2). As it stands, five of nine rows carry
no information.

---

## 2. Effects vs the full tuned discipline

Full tuned discipline = **22,942** goodput per provisioned GPU-hour (median over 9 runs).

| variant | element removed | goodput | vs full | reserved idle | served value |
|---|---|---:|---:|---:|---:|
| `full` | none (the tuned discipline) | 22,942 | -- | 94.5% | 74.6% |
| `A1_no_budget` | cross-invocation token budget | 22,935 | -0.0% | 94.6% | 74.7% |
| `A2_preemptible` | non-preemption guarantee | 22,942 | +0.0% | 94.5% | 74.6% |
| **`A3_queue_depth`** | **headroom keying (-> queue depth)** | **27,739** | **+20.9%** | 94.6% | **85.4%** |
| `A4_strict` | pin mode := strict | 22,941 | -0.0% | 94.9% | 74.6% |
| `A4_tiered` | pin mode := tiered | 22,942 | +0.0% | 94.1% | 74.6% |
| `A5_k0.01` | K := 1% | 23,188 | +1.1% | **0.0%** | 75.0% |
| `A5_k0.15` | K := 15% | 22,626 | -1.4% | 95.5% | 74.2% |
| `A5_k0.30` | K := 30% | 22,564 | -1.6% | 94.9% | 73.8% |

### By workload mix

| variant | mix 0.1 | mix 0.3 | mix 0.5 |
|---|---:|---:|---:|
| `full` | 22,945 | 22,835 | 22,653 |
| `A1_no_budget` | +0.0% | +0.0% | +0.0% |
| `A2_preemptible` | +0.0% | +0.0% | +0.0% |
| **`A3_queue_depth`** | **+23.5%** | **+19.7%** | **+14.7%** |
| `A4_strict` | +0.0% | +0.0% | +0.0% |
| `A4_tiered` | +0.0% | +0.0% | -0.0% |
| `A5_k0.01` | +1.2% | +0.9% | +0.5% |
| `A5_k0.15` | -1.4% | -1.1% | -0.7% |
| `A5_k0.30` | -1.6% | -1.9% | -1.1% |

---

---

## 3. Cell B (contended), and the correction it forces

The follow-up cell quadruples slots to 64 (where peak decode and KV are shown to
bind), raises load to 1.3, and sets gap to the grid minimum of 20s.

| variant | cell A vs full | **cell B vs full** | cell B served value |
|---|---:|---:|---:|
| `full` | -- | -- | 91.8% |
| `A1_no_budget` | -0.0% | **+0.2%** | 90.4% |
| `A2_preemptible` | +0.0% | **+0.0%** | 91.8% |
| `A3_queue_depth` | **+20.9%** | **+0.2%** | 97.8% |
| `A4_strict` | -0.0% | **-0.0%** | 91.8% |
| `A4_tiered` | +0.0% | **+0.0%** | 97.5% |
| `A5_k0.01` | +1.1% | **+0.0%** | 96.1% |
| `A5_k0.15` | -1.4% | **-0.4%** | 87.9% |
| `A5_k0.30` | -1.6% | **-0.5%** | 87.9% |

**Total goodput spread across all nine variants in cell B: 0.68 percent.**

### CORRECTION: the A3 result does not replicate

An earlier version of this document called A3 "a genuine negative result" and
"independent corroboration of the H1 KILL verdict". **That was overstated and is
withdrawn.**

A3's +20.9 percent goodput advantage appears only in cell A. In cell B it is
**+0.2 percent** -- indistinguishable from noise, and inside the 0.68 percent spread
that separates every variant from every other. The effect is a property of one
operating point, not of the degradation-keying choice.

What survives is narrower and stated as such: at the uncontended cell A, queue-depth
keying beat headroom keying on goodput; at the contended cell B it did not. A3 should
not be cited as evidence about the mechanism without both numbers.

A3 does hold a served-value advantage in both cells (85.4 vs 74.6 in A; 97.8 vs 91.8
in B). But `A4_tiered` shows a nearly identical served-value gain in cell B (97.5)
with zero goodput effect, so served value is moving with something these variants
share rather than with the degradation key specifically. Not a basis for a claim.

### The noise floor swallows every one of these differences

Raw per-run data: `docs/ablation_results.csv` (162 rows, both cells).

Comparing the seed-to-seed spread WITHIN a single variant+mix against the spread
BETWEEN variant medians:

| | within variant+mix, 3 seeds | between variants |
|---|---:|---:|
| cell A | median **18.9%**, max 26.0% | 22.9% |
| cell B | median **29.1%**, max 40.9% | **0.68%** |

In cell B the noise between three seeds of the SAME configuration is roughly
**40 times** the difference between the most and least favourable variant. In cell A
they are the same order of magnitude, which means even the 20.9 percent A3 figure sits
inside the seed spread rather than above it.

This is the deeper reason the A3 result did not replicate, and it applies to the whole
table: with n=3 the reported "effect" of an element is a median of three draws from a
distribution whose width exceeds the effect being measured. **No row in section 2 or 3
is separated from any other by more than sampling noise.**

Any per-element claim from this design needs materially more seeds -- and a CI per
variant rather than a bare median -- before it is asserted.

### What both cells agree on

**No element of the Reservation Discipline shows a robust positive contribution at
either operating point.** Removing the cross-invocation token budget (A1), the
non-preemption guarantee (A2), or switching pin embodiment (A4) changes goodput by
less than half a percent in both cells. Sweeping K from 1 to 30 percent moves goodput
by at most 1.6 percent, and larger K is consistently worse.

**Reserved capacity stays 88-95 percent idle in both cells** -- 94.5 percent in A,
90.7 percent in B -- despite cell B having 4x the slots, 1.3x the load and the
shortest gap in the grid. This is structural, not a cell artifact: an agentic flow
runs a short invocation and then waits in tool-time, so its duty cycle is roughly 5
percent by construction. Reserving capacity for a subset that is idle 9 times out of
10 strands it, and no configuration in either cell recovers the loss.

---

## 4. What this does and does not establish

**Establishes:**
- Across two operating points differing in slots, load and gap, **no element of the
  discipline shows an effect distinguishable from seed noise** on the primary metric.
  In cell B the between-variant spread is 0.68 percent against a within-variant seed
  spread of 29 percent.
- Reserved capacity is 88-95 percent stranded in both cells regardless of
  configuration. This one is robust because it is a large, consistent effect
  (roughly 9 parts in 10) rather than a difference between near-identical numbers.

**Does NOT establish:**
- That headroom keying is worse than queue-depth keying. Held in cell A, vanished in
  cell B, and sits inside the seed spread in both.
- That A1, A2 or A4 are inert. Their nulls are consistent with inertness, but this
  design cannot separate "no effect" from "effect smaller than a 29 percent noise
  floor". The honest statement is that no effect was detected, not that none exists.
- Anything about served-value differences, which move without goodput moving and are
  not attributable to specific elements here.

**Method note.** Cell B was run because cell A's five null rows were dismissed as
"not exercised". The re-run confirmed the nulls AND removed the one non-null. The
lesson is the obvious one: a single-cell ablation grid cannot distinguish an inert
element from an unexercised one, and it can manufacture an effect that does not
generalise. Any per-element table published from this work should sweep at least the
load and slot axes rather than reporting one cell.
