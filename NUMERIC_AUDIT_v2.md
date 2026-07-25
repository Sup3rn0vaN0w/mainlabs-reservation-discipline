# NUMERIC AUDIT v2 - MERGED PAPER v1.0 + PFC-1/PFC-2 - PKG1 RE-AUDIT (2026-07-24)

Re-run of the full PKG1 audit against the corrected paper. Final state audited:
paper with PFC-1 + PFC-2 applied
(SHA-256 2ffce908e0af2e8a2695946d7ba74caad515a590847b6d31ce90e04668018f94),
FIGURE_SPEC with the PFC-2 F2 assertion (41c42356...), FREEZE_GATES with
Sections 7 (PFC-1) and 8 (PFC-2) logs (24da424a...). Same method as v1:
scripted against the released analysis module over the released manifest set;
nothing eyeballed. House style: hyphens only (D-026).

VERDICT: **ALL-PASS (69 of 69 rows). GATE CLEARED.** The interim v2 run
against the PFC-1-only paper found one MARGINAL (N1, the reserved-idle band,
tracing to an imprecision in audit v1 itself) and one PASS-with-note (N2);
both were ruled and corrected as PFC-2 (FREEZE_GATES Section 8), re-verified
below. Word count 10,810; PFC-2 checklist: "77 of 81" x1, "9.5 to 23.6" x1,
all PFC-1 counts unchanged, all stale-string greps zero.

## 1. THE SEVEN v1 FAILS - CORRECTED TEXT VERIFIED

| v1 # | corrected claim (PFC-1) | paper | measured | verdict |
|---|---|---|---|---|
| F1 | "improve interactive tail latency by 63 to 97 percent... above 90 percent in most such cells" | Abstract, S1, S7.2, S9 | 63.3 to 97.1 percent; 77 of 81 load>=1.0 cells improve; 55 of 81 above 91 | PASS |
| F2 | "reserved-idle fraction runs 93 to 100 percent in 77 of 81 such cells" + named exceptions (PFC-2) | S7.2, F2 caption | 77 in [93,100]; three load-1.5 cells at 88.9; one zero-reservation cell - exact match (row N1) | PASS |
| F2a | "stranded at least 88 percent" (Abstract, S1) | Abstract, S1 | grid per-cell medians >= 88.9 in every cell where reservations were granted; ablation floor 88 (canonical readout; per-run min 87.3, per-mix medians 89.0-95.6) | PASS (note) |
| F3 | "reservation ahead by 0.8 to 3.2 percent in all 24 non-guardrail-failing cells" | S7.1 | 24 of 24 positive, +0.8 to +3.2 | PASS |
| F4 | "H2 carries only corrected-horizon data: an earlier censored-horizon reading was ruled unverified and excluded from the released record" | S7.3 | matches the record exactly (preliminary readout carries no H2 value; +6.5 nowhere in the record) | PASS |
| F5 | "default horizons ran 150 to 300 seconds; even at a 200-second window only 21.5 percent... could complete" (S8); "78.5 percent incompletable within the 200-second window" (S5); F5 caption re-attributed (horizon figure; PFC-4) | S5, S8, FIGURE_SPEC F5 | zero-check: grid 300s / H2 150s / H3 200s; 21.5 percent at 200s | PASS |
| F6 | "141 tests" (x2) | S6, Artifacts | pytest collects 141 | PASS |
| F7 | "projected 39,600 to 49,700 cpu-hours, a single 64-device run estimated at 24 to 32 wall-clock hours, unparallelizable" | S5 | SG7_GRID_FEASIBILITY: projection 39,600-49,700; 64-device run est. 24-32 h, unsettled at 16.2 h; matches as estimate, no over-claim | PASS |
| M1 | "at 1.3x it recovers to 9.5 to 11.5 percent" | S7.1 | measured 9.5 to 11.5 | PASS |

## 2. STRUCTURAL PROBE (new, permanent per the resolution order)

Section ladder asserted present and contiguous: Abstract; 1; 2; 3; 4 (4.1-4.4);
5; 6; 7 (7.1-7.6); 8; 9; 10; 11; 12; Artifacts; Appendices A, B, C, D - all
present, in order, no gaps. Word count 10,780. **PASS.** This probe runs in
every future audit of this paper.

## 3. GREPS (scope per the standing PKG1 amendment)

| check | scope | result |
|---|---|---|
| Old title | corrected paper .md | 0 - PASS |
| Old title | staged reading-copy PDF (pre-PFC-1 build; see note in S5) | 0 - PASS |
| Stale strings: "6.5 percent", "15 of 18", "131 tests", "70 to 91", "87 to 96" | corrected paper | 0 each - PASS |
| Em-dash | corrected paper, FIGURE_SPEC, FREEZE_GATES | 0 - PASS |
| Excluded by amendment | sources/ batches; FREEZE_GATES S3 tournament record | historical text retained by design |

## 4. ALL PREVIOUSLY PASSING ROWS

Re-verified against the corrected text: every v1 PASS row's claim appears
unchanged and still matches its source (verdict chain -10.3 / 0-of-108 /
guardrail 96 percent; run counts 2,268 / 252 / 84 / 162; H2 and H3 tables;
ablation numbers incl. 0.68 percent vs 29 percent, +20.9 / +0.2, 88-95
ablation idle, duty cycle ~5 percent; sensitivity 21.5->4.4 / 13.0, 0 of
19,342, 39 percent, 43 percent; horizon table -22.3 / -7.8 stable through
6000s; tuning K-to-floor both horizons, quantum flip; amendments and dates;
189 quarantined; Little's law 0.00 percent, utilization <= 0.95 percent;
collapse disclosure). Figure assertions (PFC-4 numbering): F1 computed median = -10.3 PASS;
F4 rates = T3 PASS; F5 points = zero-check table PASS (caption now correctly
attributes 21.5 percent to 200s).

## 5. NEW ROWS FOUND IN v2

### N1 - RESOLVED PASS via PFC-2: reserved-idle band (S7.2; F2 caption band)

RESOLUTION (PFC-2, option (a) exactness): S7.2 now reads "93 to 100 percent
in 77 of 81 such cells" with the three 88.9-percent load-1.5 cells and the
single zero-reservation degenerate cell named in-line. Verified against the
manifest computation below: exact match (77 in [93,100]; medians 88.9 x3;
one 0.0 with zero granted reservations on 2 of 3 seeds). Abstract/S1 "at
least 88 percent" stands per the ruling (quantifies over granted reserved
capacity). F2 build assertion now defined in FIGURE_SPEC: 77 cells in
[93,100], three in [88,90], exactly one zero-reservation cell, rendered
distinctly. **PASS.** The pre-ruling analysis is retained below as the record.

Measured, per-cell median of metrics.reserved_idle_fraction (reservation
policy) over the 81 at-or-above-nominal cells: **77 of 81 cells lie in
[93, 100]**. The four exceptions, all at load 1.5:

| cell | per-cell median | cause |
|---|---|---|
| mix0.1_load1.5_dev16_gap20 | 88.9 percent | few reservations granted (1-3 per seed) |
| mix0.1_load1.5_dev32_gap20 | 88.9 percent | few reservations granted (0-4 per seed) |
| mix0.3_load1.5_dev16_gap20 | 88.9 percent | few reservations granted (2-3 per seed) |
| mix0.1_load1.5_dev8_gap60 | 0.0 percent (degenerate) | 2 of 3 seeds granted ZERO reservations; the metric reports 0.0 with no reserved capacity in existence |

AUDIT v1 CORRECTION, owned here: v1's F2 row stated the grid floor as "93.2
to 99.8 (p5 = 93.3)". The p5 figure was correct; the floor was not - the
true non-degenerate floor is 88.9 percent, and one cell is degenerate-zero.
PFC-1's 93-100 band inherited that imprecision from the audit itself. The
band is right for 95 percent of the region and both ends are right for the
bulk; the sentence as written names no exceptions.

Escalated wording options (operator ruling; no edit made):
  (a) "runs 93 to 100 percent in all but four top-of-load cells, where
      admission grants few or no reservations (three cells at 89 percent,
      one with none granted)" - exact;
  (b) extend the band: "runs 89 to 100 percent (one degenerate cell aside)";
  (c) stand on 93-100 as the bulk band and let Figure 2's panel (b) show the
      four outliers - requires the F2 build assertion to be defined as
      "all cells with granted reservations >= 88.9" rather than ">= 93".
FIGURE_SPEC F2's PKG3 assertion must match whichever wording is ratified.

### N2 - RESOLVED PASS via PFC-2: "the 9.5 to 23.6 percent goodput deficit" (S7.2)

Pre-existing sentence, unaudited in v1 (missed row, recorded here
permanently); read "10 to 23" at v2 interim against a measured 9.5 to 23.6.
PFC-2 folded the exact figures in. Corrected text verified: "9.5 to 23.6
percent" x1, "10 to 23" x0. **PASS.**

## 6. NOTES CARRIED FORWARD

- Reading-copy PDF in merged_paper/ remains the pre-PFC-1 build (new title,
  old body text; no old-title string). RULED (PFC-2 order): superseded by
  the PKG3 built PDF; no interim rebuild.
- Source-doc internal variances noted in v1 stand (19,342 vs 19,345;
  39.4 vs 63.3 percent HBM across the two harness runs; collapse-table
  column semantics).

## 7. PFC-4 FIGURE RENUMBERING (2026-07-24)

Figures were renumbered to first-citation order; caption text, figure
content, assertions, and every underlying number are unchanged. This audit's
own row identifiers (F1-F7, F2a, M1, N1, N2) are audit rows, NOT figure
numbers, and did not move.

| content | was | is | first cited |
|---|---|---|---|
| improvement vs offered load | Figure 1 | Figure 1 | S7.1 |
| isolation and stranding | Figure 2 | Figure 2 | S7.2 |
| served value (H2 load sweep) | Figure 5 | **Figure 3** | S7.3 |
| containment and starvation | Figure 3 | **Figure 4** | S7.4 |
| horizon censoring | Figure 4 | **Figure 5** | S8 |

Assertions travel with figure CONTENT, not number: the served-value AUC check
now runs as F3, the H3-rate check as F4, the horizon check as F5, each still
bound to the same data and the same frozen values.
