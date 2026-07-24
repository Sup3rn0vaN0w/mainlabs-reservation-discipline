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
