# NUMERIC AUDIT - MERGED PAPER v1.0 - PKG1 (2026-07-24)

Scripted re-verification of every numeric claim in MERGED_PAPER_v1.0_FROZEN.md
against the released readouts, manifests, and spec. Audit code: scripted against
the released analysis module (sim.analysis.h1_readout) over the released manifest
set (2,268 H1 + 252 H2 + 84 H3 manifests, 162 ablation rows); nothing eyeballed.
House style: hyphens only (D-026).

VERDICT: **7 FAIL rows. PKG1 is BLOCKED; escalated to the strategy surface.**
Zero edits made to the frozen paper. All other rows PASS (58 of 67).

Evidence base: H1_READOUT_FINAL.md; corrected ABLATION_READOUT.md (canonical) +
docs/ablation_results.csv; H3_ZERO_CHECK.md; SENSITIVITY_HARNESS_CHECK.md;
TUNING_REPORT.md (300s + 1500s); EVAL_SPEC_T-052.md + Amendments 1-3;
SG7_GRID_FEASIBILITY_2026-07-20.md; VALIDATION_REPORT.md; live sanity runs;
manifest set at code/sim/experiments/results/ (verified: rerunning the released
readout code on it reproduces H1_READOUT_FINAL verbatim up to the header).

## 1. FAILED ROWS (verbatim claim | paper location | source | matched value | verdict)

| # | claim (verbatim) | paper | source | matched value | verdict |
|---|---|---|---|---|---|
| F1 | "interactive tail-latency isolation of 70 to 91 percent" / "improve interactive tail latency by 70 to 91 percent across most of the region" | Abstract; S1; S7.2; S9 | FINAL manifests via released readout code | At load >= 1.0: 77 of 81 cells improve; improvement range 63.3 to 97.1 percent; 55 of 81 cells improve by MORE than 91 percent; only 20 of 81 fall inside [70, 91]. Per-load medians 88.6 / 93.3 / 93.0 | **FAIL** |
| F2 | "reserved-idle fraction runs 87 to 96 percent" (S7.2); "stranded 88 to 96 percent regardless of configuration" (Abstract, S1) | Abstract; S1; S7.2 | FINAL manifests, metrics.reserved_idle_fraction, reservation policy, per-cell median | Per-cell medians run 93.2 to 99.8 across the grid (p5 = 93.3, 35 cells at ~100); only 31 of 108 cells fall inside [87, 96]. Also internally inconsistent: Abstract says 88-96, S7.2 says 87-96. FIGURE_SPEC F2 caption carries the same 87-96 band and its panel (b) assertion will fail at PKG3 as spec'd | **FAIL** |
| F3 | "reservation ahead by 1 to 3 percent in 15 of 18 non-guardrail-failing cells" | S7.1 | H1_READOUT_FINAL per-cell table (recomputed identically from manifests) | Load 1.5 has 27 cells; 3 fail the guardrail; **24 of 24** non-failing cells are positive, range +0.8 to +3.2 percent. No reading of the table produces 15 of 18 | **FAIL** |
| F4 | "an early local sweep at a censored horizon showed the discipline ahead by 6.5 percent" (and the adjacent "The preliminary readout is released with its superseded banner" as it applies to H2) | S7.3 | H1_READOUT_PRELIMINARY_2026-07-15.md; MEMORY 2026-07-20 ruling | The released preliminary readout contains NO H2 value ("Awaiting the H2 load sweep"). No document in the record carries +6.5. The 2026-07-20 record states any off-surface "+6.5 preliminary" is unverified and NOT recorded | **FAIL** |
| F5 | "the build's default horizon was 300 seconds, under which only 21.5 percent of agentic flows could complete on an idle cluster" (also "right-censoring 78.5 percent" in S5/Amendment 2 text; also the F4 figure caption) | S8; S5; FIGURE_SPEC F4 | H3_ZERO_CHECK.md | The zero-check measures 21.5 percent completable "in under **200s**" (the H3 cell horizon), not 300s. The 300s attribution entered via Amendment 2's adopting text and propagated. At 300s the completable fraction would be higher than 21.5; no released measurement of it exists | **FAIL** |
| F6 | "The full test suite (131 tests)" / "131 tests" | S6; Artifacts | pytest --collect-only on the released tree | **141 tests** collected at the current tree (131 was correct at freeze time 2026-07-21; the corrected-ablation commits added 10). The released artifact will contradict the paper | **FAIL** |
| F7 | "direct measurement showed the frozen grid infeasible at the corrected horizon (a single 64-device run exceeding 24 wall-clock hours, unparallelizable)" | S5 (Amendment 3 narrative) | probe_1500s_measured.jsonl; SG7_GRID_FEASIBILITY_2026-07-20.md; EVAL_SPEC Amendment 3 | The released probe file has rows for 8/16/32 devices ONLY. The feasibility memo records the 64-device run "still running at 16.2 h", estimated 24-32 h, explicitly unsettled. Amendment 3 says "at or above 24 hours" (projection). No released measurement shows a 64-device run exceeding 24 h. Grid-total infeasibility (39,600-49,700 cpu-hours) IS measured and stands | **FAIL** |

## 2. MARGINAL (rounding judgment; operator ruling requested)

| # | claim | paper | source | matched value | verdict |
|---|---|---|---|---|---|
| M1 | "at 1.3x it recovers to 10 to 11 percent" | S7.1 | FINAL per-cell table | Measured range 9.5 to 11.5 percent (median -10.6). "10 to 11" understates both ends by 0.5 | MARGINAL |
| M2 | "at 0.7x the deficit runs 7 to 16 percent" | S7.1 | FINAL per-cell table | Measured 6.6 to 16.1; rounds to 7-16 | PASS (note) |
| M3 | "roughly 470 to 530 percent" (inversion corner) | S7.2 | FINAL table | +468.6 / +501.7 / +526.5; "roughly" covers 468.6 | PASS (note) |

## 3. PASSED ROWS (claim | paper | source | matched value)

| claim | paper | source | matched value | verdict |
|---|---|---|---|---|
| Median improvement -10.3 percent | Abstract, S1, S7.1, S9, S12 | FINAL readout + recomputation | -10.3 percent | PASS |
| 0 of 108 cells supporting; 60 percent required | Abstract, S1, S7.1 | FINAL + recomputation | 0/108; threshold 60 percent | PASS |
| Guardrail intact in 96 percent of cells | S7.1 | FINAL + recomputation | 96.3 percent (4 FAIL cells) | PASS |
| 108 cells; six baselines; seven policies; identical simulated hardware | Abstract, S1, S5, S7 | FINAL provenance; spec S4 + A1 | 108; 6; 7 | PASS |
| 2,268 H1 runs; 252 H2; 84 H3; 162 ablation runs over two operating points | S7 | manifest counts | 2,268 (incl. 2 collapsed markers); 252; 84; 81+81=162 | PASS |
| Two baseline runs reached congestion collapse and are excluded; selection effect flatters the affected baselines | S6, S7 | FINAL collapse table (collapsed-seeds column = seed IDs) + manifests | niyama_style seed 1 (dev32 cell), vllm_style seed 2 (dev8 cell): 2 runs | PASS |
| U-shape: deepest at 1.0x, "22 to 24 percent in every cell" | S7.1, S9 | recomputation | load-1.0 range -22.4 to -23.6, all 27 cells | PASS |
| Sign flips at 1.5x, "+1 to +3 percent" magnitude | Abstract, S1, S7.1, S9 | recomputation | non-failing cells +0.8 to +3.2, median +1.9 | PASS (range note) |
| Cluster size "agree within roughly a point" | S7.1, S10 | recomputation | per-device medians -10.0 / -10.6 / -10.6 (spread 0.6 pt) | PASS |
| Gap effect: 50/50 at 0.7x: 16 percent at 20s gaps vs 9.5 percent at 300s | S7.1 | recomputation | medians -16.0 and -9.5 | PASS |
| Guardrail-fail corner at 50/50, 1.5x, 20s gaps, all three cluster sizes | S7.2 | FINAL table | 3 cells, +468.6/+501.7/+526.5 | PASS |
| Isolated failure 90/10, nominal, 8 devices, 300s gaps, FastServe, +77 percent | S7.2 | FINAL table | +77.2 percent, no neighbor repeats | PASS |
| H2 AUC 0.626 vs 0.648 (Niyama), 3.4 percent deficit; FastServe and vLLM ahead; MARS 0.411; CONCUR 0.227 | S7.3, F5 | FINAL H2 + recomputation | 0.626 / 0.648 / -3.4 percent / 0.639 / 0.633 / 0.411 / 0.227 | PASS |
| H2 load sweep 0.5x-3.0x | S7.3, F5 | manifests | loads {0.5, 0.75, 1.0, 1.5, 2.0, 3.0} | PASS |
| H3 rates 0.503 / 0.475 / 0.499 / 0.471; worst delta 3.1 points | Abstract, S7.4, T3, F3 | FINAL H3 + recomputation | exact match | PASS |
| vLLM and Niyama complete 0.000 at every level; FCFS 0.332, degrades 5.0 points | S7.4 | FINAL H3 + recomputation | exact match | PASS |
| H3 config 50/50 mix, 1.2x load; 1 of 107 compliant flows scheduled; zero preemptions | S7.4, F3 | H3_ZERO_CHECK.md | mix 0.5, load 1.2; 1/107; 0 preemptions | PASS |
| Only policy above 0.3 absolute and below 3.5 points degradation | S7.4 | FINAL H3 | reservation 0.471-0.503, worst 3.1; next candidate fcfs degrades 5.0 | PASS |
| Ablations: nine variants x three mixes x three seeds, two operating points; contended cell = 4x slots, 1.3x load, shortest gaps | S7.5 | ABLATION_READOUT (corrected) | 9x3x3x2 = 162; cell B = 64 slots, 1.3, 20s | PASS |
| Contended-cell spread 0.68 percent vs within-variant median 29 percent | S7.5 | recomputation from ablation_results.csv | 0.68 percent; 29.0 percent (readout: 29.1) | PASS |
| Nominal cell: two spreads same order of magnitude | S7.5 | recomputation | 22.6 vs 18.8 percent | PASS |
| A3 queue-depth +20.9 percent at nominal, +0.2 percent contended; withdrawn in the corrected document | S7.5 | corrected ABLATION_READOUT + CSV | +20.9 / +0.2; withdrawal present in canonical doc | PASS |
| "reserved capacity sits 88 to 95 percent idle at both operating points" | S7.5 | corrected ABLATION_READOUT | source states 88-95, cell medians 94.5 / 90.7 (full-variant per-mix medians 89.0-95.6) | PASS vs source |
| Duty cycle roughly 5 percent | Abstract, S1, S7.5, S12 | ABLATION_READOUT | "duty cycle is roughly 5 percent by construction" | PASS |
| K-to-floor: smallest reserved subset chosen in every family at both horizons | S7.5, S9 | TUNING_REPORT 300s + 1500s | reserved_subset_fraction = 0.05 (grid minimum) in all 3 families, both reports | PASS |
| Sensitivity: 21.5 -> 4.4 percent at 0.7x anchor; 13.0 percent at 1.3x | S7.6, S10 | SENSITIVITY_HARNESS_CHECK | -21.5 -> -4.4 (0.7x), -13.0 (1.3x) | PASS |
| Peak decode binds in 0 of 19,342 decode calls; peak memory near 39 percent | S7.6 | SENSITIVITY_HARNESS_CHECK Step 2 | 0/19,342; 39.4 percent. (Doc's PART B.2 table separately shows 0/19,345 and 63.3 percent from a different instrumented run; source-internal variance noted, paper matches Step 2) | PASS (note) |
| Memory-bound config: 64 slots, agentic-heavy, 1.5x load; peak decode governs 43 percent of calls; memory saturates | S7.6 | SENSITIVITY_HARNESS_CHECK PART B.2 | 64 slots, mix 0.5, load 1.5; 6,329/14,652 = 43.2 percent; HBM 100.0 percent | PASS |
| Median agentic flow lifetime roughly 863s | S5, S8, F4 | H3_ZERO_CHECK | median 863s | PASS |
| Horizon quantification: -22.3 percent at 300s, -7.8 at 1500s, stable through 6000s; more absolute completions at longest horizon | S8, F4 | H3_ZERO_CHECK table | -22.3 / -7.8 / -8.8 / -8.8; 211,615 vs 177,142 flows at 6000s | PASS |
| Censored tuning: FastServe quantum flipped shortest-to-longest in every family | S8 | TUNING_REPORT pair + SG7_GRID_FEASIBILITY S5 | quantum_scale 0.5 -> 2.0, all 3 families | PASS |
| Spec frozen 2026-07-06; thresholds 10 / 5 percent, 60 percent of cells, 5 percent TTFT guardrail; median across cells; best baseline per cell | S5 | EVAL_SPEC S3, S12 | exact match | PASS |
| Region: mixes 90/10, 70/30, 50/50; loads 0.7-1.5; gap medians 20/60/300s; devices 8-32; corner cells pre-declared, none excluded | S5 | EVAL_SPEC S5 + A3; FINAL readout | exact match; excluded corner cells: 0 | PASS |
| Amendment 1 (2026-07-14) MARS baseline added | S5 | EVAL_SPEC A1 | date + content match | PASS |
| Amendment 2 (2026-07-15) horizons >= 1500s; one-fix clause | S5, S8 | EVAL_SPEC A2 | date + content match | PASS |
| Amendment 3 (2026-07-20) devices 8/16/32, three seeds | S5, S10 | EVAL_SPEC A3 | date + content match ("3 seeds" = seed set {1,2,3} per notation note) | PASS |
| Integrity ruling (2026-07-20): 189 pre-amendment manifests quarantined; input-domain guard | S5, S6 | quarantine dir (189 files counted); commit 1a6b50e dated 2026-07-20; guard code in analysis/h1_readout.py | 189; date match | PASS |
| Contamination direction: censored-horizon cells at an excluded cluster size, biased against the mechanism | S6 | preliminary readout provenance (189 manifests, 300s, dev4) | 4-device, 300s pilot manifests | PASS |
| Equal-budget tuning: 8-point grids, build-failing equality test, validation/held-out split | S5 | TUNING_REPORT + spec S4 | 8 points every policy; seeds {1,2,3} vs {101-105} disjoint | PASS |
| Little's law to 0.00 percent; utilization within 1 percent | S6 | live rerun of released sanity code | rel err 0.00 percent; max util err 0.95 percent | PASS |
| Three construction defects caught by invariants, one flattering the mechanism (O(n^2) path); tiered-pin restore defect; oversized-pin defect | S6 | integrity record carried from empirical freeze (matrix rows 1-13) | consistent with freeze record | PASS (narrative) |
| FIGURE assertions: F1 computed median = -10.3 | FIGURE_SPEC | manifests via readout code | -10.3, PASS |  PASS |
| FIGURE assertions: F3 rates = T3 table | FIGURE_SPEC | H3 recomputation | exact match | PASS |
| FIGURE assertions: F4 points = zero-check table | FIGURE_SPEC | H3_ZERO_CHECK | -22.3/-7.8/-8.8/-8.8 match; caption's "21.5 percent at 300s" element carries FAIL F5 | PASS except F5 |
| Dates: freeze 2026-07-06; verdict git 99706eb; FINAL 2026-07-20; merge 2026-07-21 | header, S5 | git log, readout provenance | match | PASS |

## 4. TITLE + STYLE GREPS (scope per the PKG1 amendment)

| check | scope | result |
|---|---|---|
| Old title "What Completion Guarantees Actually Cost" | MERGED_PAPER_v1.0_FROZEN.md | 0 occurrences - PASS |
| Old title | MERGED_PAPER_v1.0_FROZEN.pdf (text-extracted) | 0 occurrences - PASS (title page carries the ratified title) |
| Old title | this audit, excluding quoted-record rows | quoted only as the grep target - PASS |
| Excluded by amendment (not grepped for enforcement) | merged_paper/sources/ batches; FREEZE_GATES_MERGED.md S3 record | historical occurrences retained by design |
| Em-dash grep | MERGED_PAPER_v1.0_FROZEN.md | 0 - PASS |

## 5. NOTES FOR THE RECORD (no action, no edits)

- The FINAL readout's collapse table column "collapsed seeds" lists seed IDs, not
  counts; read as niyama seed 1, vllm seed 2. The paper's "two baseline runs" is
  the correct reading and matches the manifest set.
- SENSITIVITY_HARNESS_CHECK carries two instrumented runs of the representative
  cell (19,342-call and 19,345-call; peak HBM 39.4 vs 63.3 percent). The paper's
  numbers match the Step 2 run (tuned parameters). The source doc's internal
  variance is noted, not resolved.
- The reading-copy PDF was built from the pre-header-fix export (line 2 renders
  the assembly note); the paper of record is the .md. No old-title occurrence.
- F1/F3/F4 figure assertions were evaluated against manifests here; the PKG3
  implementation must re-assert them at build time per FIGURE_SPEC.
