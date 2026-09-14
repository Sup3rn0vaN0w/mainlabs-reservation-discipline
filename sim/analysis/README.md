# sim/analysis

Statistics and the SG7 verdict.

- `stats.py` - percentile-bootstrap confidence intervals. Median aggregation (spec Section 3: "no single-cell heroics"), and paired relative-improvement CIs that resample the (ours, baseline) PAIRS together to preserve the paired comparison the experiment is built on. All bootstraps draw from a seeded generator, so every CI is exactly reproducible.
- `h1_readout.py` - the mechanical H1 verdict (spec Section 3). Consumes the grid manifest set and emits `docs/H1_READOUT.md`: per-cell medians, best-baseline-per-cell, bootstrap CIs, corner-cell exclusions applied, the support/weak/kill classification, plus H2 served-value AUC and H3 compliant-completion deltas. Numbers and the threshold comparison only -- no prose conclusions.

## The verdict is mechanical (and load-bearing)

Per D-058 the filing executes off the H1 verdict with NO re-decision: SUPPORT or WEAK
-> counsel files; KILL -> same-day no-file recorded; both papers publish either way.
So the classification is a pure function of the manifests -- run it twice, get the
same verdict -- and every branch (support / weak / kill, the corner-cell exclusions,
the best-baseline-per-cell rule, the guardrail gate) is pinned by
`tests/test_h1_verdict.py` against hand-computable inputs.

H2 (served-value AUC over the load sweep) and H3 (compliant completion-rate deltas
under runaway injection) shape the papers only, never the filing.

Run: `make h1-readout` (after the grid has produced manifests).

## Figures and tables

- `figures.py` - every figure and table in the paper, each derived from the
  MANIFESTS via this analysis code rather than from readout markdown, with two
  spec'd exceptions carried as data blocks in the module (F5's horizon-deficit
  series and T4's sensitivity rows). Captions are FINAL PROSE and live in the
  LaTeX sources; this module renders images only.

EVERY FIGURE ASSERTS AGREEMENT WITH THE FROZEN PAPER NUMBERS BEFORE IT WRITES.
An assertion failure exits non-zero and is a STOP-and-escalate, never a silent
redraw, and the assertions travel with a figure's CONTENT rather than its
number so a renumbering cannot rebind a check.

| command | figure | assertion |
|---|---|---|
| `make fig-F1` | per-cell improvement vs offered load | computed median improvement == -10.3 percent |
| `make fig-F2a` | interactive p95 TTFT change per cell | 77 guardrail-holding cells at or above nominal, improvement band 63.3 to 97.1, inversion corner 468.6 to 526.5 over 3 cells |
| `make fig-F2b` | reserved-idle fraction per cell | partition (in [93,100], in [88,90], zero-reservation) == (77, 3, 1) |
| `make fig-F3` | served value over the load sweep | AUCs match the frozen values, relative -3.4 percent |
| `make fig-F4` | containment and starvation | rates match the frozen T3 table exactly |
| `make fig-F5` | horizon censoring | population median lifetime in [690, 730] and completable-at-200s in [23, 26] |
| `make tables-all` | T1 to T4 and the 108-row Appendix H table | values recomputed from the manifest set |
| `make figures` | all of the above | all of the above |

Outputs land in `analysis/figures/` as vector PDF plus a PNG preview, and in
`analysis/tables/` as LaTeX fragments the paper `\input`s.
