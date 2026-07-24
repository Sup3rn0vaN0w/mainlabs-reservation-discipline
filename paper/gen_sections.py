"""Generate LaTeX section files from MERGED_PAPER_v1.0_FROZEN.md (PKG3).

VERBATIM conversion - zero prose changes. The only transformations are
mechanical LaTeX form: bracket citations -> \\citep, " -> " arrows -> $\\to$,
straight-quote pairs -> LaTeX quotes, appendix A/B pseudocode -> verbatim,
and float insertion per FIGURE_SPEC_MERGED PLACEMENT with the spec's FINAL
captions. Any other character transformation is a bug.

Run:  python3 gen_sections.py   (from merged_paper/latex/)
House style: hyphens only (D-026).
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
MD = HERE.parent / "MERGED_PAPER_v1.0_FROZEN.md"

KEY = r"[a-z][a-z_0-9]*"
CITE_RE = re.compile(rf"\[({KEY}(?:\s*,\s*{KEY})*)\]")

# FINAL captions from FIGURE_SPEC_MERGED.md (PFC-3 state), minus the
# auto-generated "Figure N: " prefix. Never edited here.
CAPTIONS = {
    "F1": ("Per-cell improvement of the reservation discipline over the best "
           "per-cell tuned baseline, across offered load. Each point is one of "
           "108 pre-registered core cells; the heavy line traces the per-load "
           "median. The deficit is U-shaped, deepest at nominal provisioning "
           "(22 to 24 percent at 1.0x), and the sign flips past saturation "
           "(+1 to +3 percent at 1.5x). Dashed lines mark the pre-registered "
           "WEAK (+5 percent) and SUPPORT (+10 percent) thresholds; no cell "
           "reaches SUPPORT. Open symbols mark the three cells failing the "
           "interactive guardrail (Section 7.2)."),
    "F2": ("What the stranding buys. (a) Interactive p95 time-to-first-token "
           "change versus the best baseline per cell: at and above nominal "
           "load, reservations improve interactive tail latency by 63 to 97 "
           "percent by keeping agentic flows off the shared pool; the effect "
           "inverts only in the heavy-agentic, overloaded, short-gap corner, "
           "where the guardrail fails (Section 7.2). (b) Reserved-idle "
           "fraction per cell: the isolation of panel (a) is priced in "
           "capacity held idle, 93 to 100 percent of the reserved pool in 77 "
           "of 81 cells, with three load-1.5 cells at 88.9 percent and one "
           "cell granted no reservations (marked), the structural duty-cycle "
           "cost of Section 7.5."),
    "F3": ("Compliant-flow completion under adversarial runaway injection, "
           "absolute rates. The reservation discipline holds near 0.5 with a "
           "worst-case degradation of 3.1 points; FCFS completes 0.33 with no "
           "containment; the two strongest request-oriented baselines "
           "complete zero compliant flows at every level including no "
           "injection, instrumented admission starvation (one of 107 "
           "compliant flows ever scheduled, zero preemptions; Section 7.4). "
           "Deltas against zero are undefined, which is why rates are "
           "absolute."),
    "F4": ("Horizon censoring on one core cell. The population median agentic "
           "flow lifetime, derived from the released workload generators, is "
           "roughly 711 seconds (first measured at 863 seconds on the "
           "diagnostic realization that surfaced the artifact, open markers); "
           "at a 200-second window only about a quarter of agentic flows can "
           "complete even on an idle cluster (24.5 percent of the population; "
           "21.5 percent of the diagnostic realization), while the campaign's "
           "default horizons ran 150 to 300 seconds. The measured deficit is "
           "22.3 percent at a 300-second horizon and halves to 7.8 percent at "
           "adequate horizons, stable through 6000 seconds. Horizons shorter "
           "than the workload's completable lifetimes charge "
           "guarantee-holders full stranding cost against zero completion "
           "payoff (Section 8)."),
    "F5": ("Served value across the load sweep. The reservation discipline's "
           "area under curve (0.626) trails deterministic demotion (0.648), "
           "killing the graceful-degradation hypothesis; the "
           "admission-throttling baselines collapse under overload "
           "(Section 7.3)."),
}

TABLE_CAPTIONS = {
    "T1": "Policy roster and tuned parameters per workload family "
          "(corrected-horizon tuning campaign; 8-point equal-budget grids).",
    "T2": "Pre-registered verdict summary (thresholds fixed 2026-07-06).",
    "T3": "H3 absolute compliant-flow completion rates at each "
          "runaway-injection level, with the worst delta versus the "
          "no-injection condition (percentage points).",
    "T4": "Service-model sensitivity: measured effect and binding "
          "classification per constant.",
}

FIG_FILES = {
    "F1": "F1_improvement_vs_load.pdf",
    "F2": "F2_isolation_and_stranding.pdf",
    "F3": "F3_containment_starvation.pdf",
    "F4": "F4_horizon_lesson.pdf",
    "F5": "F5_served_value.pdf",
}


def fig_block(name: str, double: bool, label: str) -> str:
    env = "figure*" if double else "figure"
    width = r"\textwidth" if double else "3.4in"
    return "\n".join([
        rf"\begin{{{env}}}[t]", r"\centering",
        rf"\includegraphics[width={width}]{{figures/{FIG_FILES[name]}}}",
        rf"\caption{{{CAPTIONS[name]}}}",
        rf"\label{{{label}}}",
        rf"\end{{{env}}}", ""])


def tab_block(name: str, fname: str, label: str, small: bool = True) -> str:
    size = r"\small" if small else r"\footnotesize"
    return "\n".join([
        r"\begin{table}[t]", r"\centering", size,
        rf"\caption{{{TABLE_CAPTIONS[name]}}}",
        rf"\label{{{label}}}",
        rf"\input{{tables/{fname}}}",
        r"\end{table}", ""])


# Float insertions keyed by the exact md heading they follow.
INSERTS = {
    "## 5. Pre-registered design": tab_block("T1", "T1_roster", "tab:roster"),
    "### 7.1 H1: the primary comparison":
        fig_block("F1", True, "fig:f1") + tab_block("T2", "T2_verdict", "tab:verdict"),
    "### 7.2 What the stranding buys: isolation, and where it inverts":
        fig_block("F2", True, "fig:f2"),
    "### 7.3 H2: the graceful-degradation hypothesis, reversed and killed":
        "\\setcounter{figure}{4}\n" + fig_block("F5", False, "fig:f5")
        + "\\setcounter{figure}{2}\n",
    "### 7.4 H3: containment, and the starvation result":
        fig_block("F3", False, "fig:f3")
        + tab_block("T3", "T3_h3_rates", "tab:h3", small=False),
    "### 7.6 Sensitivity: what the numbers are conditioned on":
        tab_block("T4", "T4_sensitivity", "tab:sens", small=False),
    "## 8. The horizon lesson": fig_block("F4", False, "fig:f4"),
}


def prose(text: str) -> str:
    """Mechanical LaTeX form for prose. No wording changes."""
    text = CITE_RE.sub(lambda m: r"\citep{" +
                       re.sub(r"\s+", "", m.group(1)) + "}", text)
    text = text.replace(" -> ", r" $\to$ ")
    # Straight-quote pairs -> LaTeX quotes (opening after whitespace/start).
    out, opened = [], False
    for i, ch in enumerate(text):
        if ch == '"':
            if not opened and (i == 0 or text[i - 1] in " \n(:"):
                out.append("``")
                opened = True
            else:
                out.append("''")
                opened = False
        else:
            out.append(ch)
    return "".join(out)


def main() -> None:
    lines = MD.read_text().splitlines()
    # Drop the header comment block: everything before "## Abstract".
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "## Abstract")
    lines = lines[start:]

    # Split on headings.
    chunks: list[tuple[str, list[str]]] = []
    cur_head, cur = None, []
    for ln in lines:
        if ln.startswith("## ") or ln.startswith("### "):
            if cur_head is not None:
                chunks.append((cur_head, cur))
            cur_head, cur = ln.strip(), []
        else:
            cur.append(ln)
    chunks.append((cur_head, cur))

    files: dict[str, list[str]] = {}
    order: list[str] = []

    def emit(fname: str, s: str) -> None:
        if fname not in files:
            files[fname] = []
            order.append(fname)
        files[fname].append(s)

    sec_re = re.compile(r"^## (\d+)\. (.*)$")
    sub_re = re.compile(r"^### (\d+)\.(\d+) (.*)$")
    app_re = re.compile(r"^## Appendix ([A-D])\. (.*)$")

    fname = None
    for head, body in chunks:
        text = "\n".join(body).strip("\n")
        if head == "## Abstract":
            fname = "00_abstract"
            emit(fname, "\\begin{abstract}\n" + prose(text)
                 + "\n\\end{abstract}\n")
            continue
        m = sec_re.match(head)
        if m:
            n, title = int(m.group(1)), m.group(2)
            fname = f"{n:02d}_{re.sub(r'[^a-z0-9]+', '_', title.lower())[:24].strip('_')}"
            emit(fname, f"\\section{{{title}}}\n")
            if head in INSERTS:
                emit(fname, INSERTS[head])
            emit(fname, prose(text) + "\n")
            continue
        m = sub_re.match(head)
        if m:
            title = m.group(3)
            emit(fname, f"\\subsection{{{title}}}\n")
            if head in INSERTS:
                emit(fname, INSERTS[head])
            emit(fname, prose(text) + "\n")
            continue
        if head == "## Artifacts":
            fname = "90_artifacts"
            emit(fname, "\\section*{Artifacts}\n")
            emit(fname, prose(text) + "\n")
            continue
        m = app_re.match(head)
        if m:
            letter, title = m.group(1), m.group(2)
            fname = f"appendix_{letter}"
            emit(fname, f"\\section{{{title}}}\n")
            if letter in ("A", "B"):
                emit(fname, "{\\small\n\\begin{verbatim}\n" + text
                     + "\n\\end{verbatim}\n}\n")
            elif letter == "C":
                emit(fname, prose(text) + "\n")
            else:  # D - machine-generated per the frozen placeholder.
                emit(fname, "Per-cell H1 results, machine-generated from the "
                     "FINAL readout (cell, best baseline, improvement, 95 "
                     "percent CI, guardrail; all 108 cells).\n\n"
                     "{\\footnotesize\n\\input{tables/appendix_D_cells}\n}\n")
            continue
        raise SystemExit(f"unhandled heading: {head}")

    for fn in order:
        (HERE / f"{fn}.tex").write_text("\n".join(files[fn]))
    body = ["% body_shared.tex -- generated by gen_sections.py; the identical",
            "% document body for BOTH targets (D-054 pattern). Do not fork.",
            r"\maketitle", ""]
    body += [rf"\input{{{fn}}}" for fn in order if fn.startswith("0")
             or fn.startswith("1")]
    body += [rf"\input{{90_artifacts}}", "", r"\appendix"]
    body += [rf"\input{{appendix_{L}}}" for L in "ABCD"]
    body += ["", r"\bibliographystyle{tmlr}", r"\bibliography{references}", ""]
    (HERE / "body_shared.tex").write_text("\n".join(body))
    print(f"[gen] wrote {len(order)} section files + body_shared.tex")


if __name__ == "__main__":
    main()
