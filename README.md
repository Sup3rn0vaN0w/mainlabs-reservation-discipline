# An Evaluation of Reservation-Based Serving for Long-Horizon Agentic Workloads

Complete evaluation artifact for the paper "An Evaluation of Reservation-Based
Serving for Long-Horizon Agentic Workloads" (arXiv: ARXIV-ID-PLACEHOLDER;
under review at TMLR). Every number in the paper derives from the manifests,
code, and frozen specification in this repository; nothing reported depends on
an unreleased artifact.

## What this is

A pre-registered, negative-result evaluation of per-flow completion guarantees
(compute + cross-invocation token budget + pinned KV cache, with a hard
non-preemption guarantee) for long-horizon agentic LLM serving, against six
equally tuned baselines on a discrete-event simulator. The evaluation
specification, kill thresholds, and baseline tuning discipline were frozen
before any evaluation code existed; every subsequent change is a numbered
amendment released here.

## Layout

| path | contents |
|---|---|
| `EVALSPEC_RELEASE.md` | the citable release: frozen spec v1.0 (2026-07-06) + Amendments 1-3 + integrity rulings |
| `NUMERIC_AUDIT.md`, `NUMERIC_AUDIT_v2.md` | the packaging audit: every paper number re-verified against this artifact (v1 = trigger record, v2 = all-PASS) |
| `sim/` | the simulator: engine (`core/`, `cluster/`, `scheduler/`), 7 policies behind one interface, workload generators (`workload/`), 141 tests (`tests/`), sanity harness (`sanity/`) |
| `sim/configs/` | frozen grid, sweep, tuning, and service-model configs; tuned parameters |
| `sim/experiments/results/` | ALL run manifests: 2,268 H1 (108 cells x 7 policies x 3 seeds, incl. 2 collapsed-run records), 252 H2, 84 H3, 162 ablation runs |
| `sim/experiments/quarantine_pre_amendment3/` | the 189 quarantined pre-amendment manifests (integrity ruling 1; retained as evidence, excluded from every verdict) |
| `sim/docs/` | the readouts: H1_READOUT_FINAL.md (the verdict), the preliminary readout with its superseded banner, the corrected ABLATION_READOUT.md, H3_ZERO_CHECK.md, SENSITIVITY_HARNESS_CHECK.md, both tuning campaigns (TUNING_REPORT.md at the corrected horizon; the censored-horizon campaign in the paper trail), validation report, cloud runbook, collapse diagnostics |
| `sim/analysis/` | verdict code (`h1_readout.py`: the H1/H2/H3 verdict as a pure function of the manifest set) and figure/table generation (`figures.py`) with per-figure assertions |
| `paper/` | the paper source: one shared LaTeX tree, two build targets (named arXiv preprint, anonymous TMLR), verified `references.bib`, unmodified `tmlr.sty`/`tmlr.bst`, built PDFs |

## Reproduction

Requirements: Python 3.12, `matplotlib`; no GPU, no network.

```
cd sim
make test          # full suite, 141 tests
make sanity        # Little's law + utilization closed-form checks
make h1-readout    # regenerate the H1/H2/H3 verdict from the manifests
```

Per-figure reproduction (each command regenerates one figure from manifests
and asserts agreement with the paper's frozen numbers; an assertion failure
exits nonzero):

```
python -m sim.analysis.figures F1   # primary comparison; asserts median == -10.3
python -m sim.analysis.figures F2   # isolation/stranding; asserts the 77/3/1 partition
python -m sim.analysis.figures F3   # served value; asserts the seven AUCs
python -m sim.analysis.figures F4   # containment; asserts rates == Table 3
python -m sim.analysis.figures F5   # horizon lesson; asserts population median in [690,730]
python -m sim.analysis.figures tables   # Tables 1-4 + Appendix D to LaTeX
```

### Figure numbering (changed 2026-07-24)

Figures were renumbered to first-citation order. If you are reproducing from
a copy of this artifact taken before that date, or from an earlier draft of
the paper, use this mapping. Figure CONTENT, captions, assertions, and every
underlying number are unchanged; only the identifiers moved.

| content | old number | current number | first cited |
|---|---|---|---|
| improvement vs offered load | Figure 1 | Figure 1 | Section 7.1 |
| isolation and stranding | Figure 2 | Figure 2 | Section 7.2 |
| served value (H2 load sweep) | Figure 5 | Figure 3 | Section 7.3 |
| containment and starvation | Figure 3 | Figure 4 | Section 7.4 |
| horizon censoring | Figure 4 | Figure 5 | Section 8 |

Paper build (four-pass, TeX Live with pdflatex/bibtex):

```
cd paper
pdflatex main_arxiv && bibtex main_arxiv && pdflatex main_arxiv && pdflatex main_arxiv
pdflatex main_tmlr  && bibtex main_tmlr  && pdflatex main_tmlr  && pdflatex main_tmlr
```

To re-run simulation cells rather than reuse the released manifests, see
`sim/docs/SG7_CLOUD_RUNBOOK.md`; the full grid is compute-intensive (the
released manifests are the campaign of record).

## Integrity notes for reviewers

- The verdict is a pure function of the manifest set; the readout hard-errors
  on any manifest outside the pre-registered grid (input-domain guard).
- Superseded preliminary readouts are released with their banners intact.
- Two baseline runs reached congestion collapse at the wall-clock cap and are
  excluded with the selection effect disclosed (it flatters the baselines).
- The packaging audit (NUMERIC_AUDIT*.md) lists every claim-to-source check,
  including the two that failed and were corrected post-freeze (logged as
  PFC-1..3 in the paper's freeze record).

## Licenses

- Code (`sim/`, `paper/` build tooling): Apache License 2.0 (`LICENSE`).
- Documents and data (readouts, manifests, specification, paper text,
  figures): Creative Commons Attribution 4.0 (`LICENSE-DOCS`).

## Citation

BibTeX for the paper is in `paper/references.bib` form; a CITATION entry will
be added when the arXiv identifier exists (placeholder above).
