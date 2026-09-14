"""Generate the MLSys 2027 LaTeX sources from MERGED_PAPER_v1.0_FROZEN.md.

VERBATIM conversion - zero prose changes. The only transformations are
mechanical LaTeX form: bracket citations -> \\citep, " -> " arrows -> $\\to$,
straight-quote pairs -> LaTeX quotes, literal underscores escaped outside
citation keys, pseudocode appendices -> verbatim, float insertion per the CG2
float decisions with the FIGURE_SPEC's FINAL captions, and the float-number
remap described under REFERENCE REMAP below. Any other character
transformation is a bug.

REBUILT AT CG6 T2 (2026-09-14). The previous version keyed on the pre-CG3
structure ("### 7.1 H1: the primary comparison", "## 8. The horizon lesson")
and every one of its anchors was gone; the post-CG4 measurements had to be
produced with a scratch converter. This version keys on the ratified post-CG4
structure: sections 1-10, results subsections 6.1-6.6, appendices A-H.

TARGET. MLSys 2027 Research Track, two-column, built against the official
mlsys2025 package the 2027 CFP directs authors to. The style file is
UNMODIFIED and carries geometry tamper checks; never touch it.
This is the ONLY paper line (D-068). main_tmlr.tex and main_arxiv.tex are the
retired record of the 2026-07 submission (D-063); they no longer build,
because their section inputs are regenerated here, and they are not restored.
main_tmlr.pdf stands as the committed July record and is never rebuilt. arXiv
receives the submitted version plus appendices after CG7, from this tree.

FLOAT PLACEMENT (CG2 float decisions, executed here as the CG6 half of the
deferred CG3 M5 obligation):
  BODY      F-lead 6.1 and F-horizon 6.2, single-column at 3.25in (T2-f).
            Each body figure plus its frozen caption costs exactly one full
            column, 0.50pp, which is why CG6 Track 1b and 1c migrated three
            more floats rather than trim prose: only block-sized removals
            move a page budget. These two are not substitutable. F-lead
            draws two baseline lines flat on the axis at zero, the paper's
            most forceful object, and F-horizon draws a deficit halving and
            stabilising, a shape prose describes poorly.
  APPENDIX G  F-verdict improvement, F-h2 served value, BOTH panels of the
            isolation figure, then T1 roster, T2 verdict, T3 H3 rates,
            T4 sensitivity. The
            CG3 M5 order, extended by the CG6 Track 1b block cuts, which
            reversed CG2's "keep in body" ruling for the reserved-idle
            panel and CG5's promotion of the sensitivity TABLE. Both
            reversals were made on a measured page cost that did not
            exist when the originals were ruled; the fidelity ARGUMENT
            stays in the body as prose.
Each body float is emitted AFTER the paragraph that cites it, located by the
citation token rather than by a hardcoded paragraph index, so a reflow cannot
put a float a page ahead of its own citation.

REFERENCE REMAP - READ THIS BEFORE CHANGING IT.
The markdown names floats by literal number ("Figure 4", "Table 1"). Those
numbers are the PFC-4 first-citation order of the PRE-CG4 body. CG4 reordered
the results spine, so the literal numbers no longer match the order the floats
appear in, and T1/T2/T3 now sit in an appendix. This module therefore rewrites
each literal float reference to a \\ref against the float's semantic label, the
same class of mechanical transformation as [key] -> \\citep{key}: the markdown
stays canonical and byte-identical, and LaTeX supplies the rendered number.
REF_MAP is asserted exhaustive against the markdown on every run - an
unmapped "Figure N"/"Table N" is a hard stop, never a silent pass-through.
This keeps the BUILD correct while the canonical markdown still reads with the
old numbers. Making the two agree is a strategy-surface renumbering, not a
build-session edit.

Run:  python3 gen_sections.py   (from merged_paper/latex/)
House style: hyphens only (D-026).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MD = HERE.parent / "MERGED_PAPER_v1.0_FROZEN.md"

KEY = r"[a-z][a-z_0-9]*"
CITE_RE = re.compile(rf"\[({KEY}(?:\s*,\s*{KEY})*)\]")
FLOATREF_RE = re.compile(r"\b(Figure|Table)~?\s+(\d+)\b")

# FINAL captions from FIGURE_SPEC_MERGED.md, minus the auto-generated
# "Figure N: " prefix. FROZEN PROSE - never edited here.
#
# F2a/F2b: the isolation figure is two assets since CG6 T2-e. CG6 Track 3a
# replaced the provisional split - which duplicated one lead sentence across
# both and left "(a)"/"(b)" vestigial on standalone figures - with two
# captions authored for the purpose. Every claim they carry is unchanged.
#
# PFC-5 (2026-09-14) repointed every caption cross-reference from the pre-CG4
# numbering these captions carried through CG3 and CG4 (7.2, 7.3, 7.4, 7.5, 8)
# to the ratified post-CG4 sections, and corrected two factual errors: F1 said
# three guardrail-failing cells where the grid, the figure and Section 6.4 all
# say four, and F2a said the effect inverts "only" in the heavy-agentic corner
# when the fourth cell inverts outside it. A caption's CLAIMS remain frozen; a
# pointer to a section that no longer exists was never a claim. The build still
# prints every caption cross-reference on every run, so the next structural
# renumbering cannot leave one behind quietly.
CAPTIONS = {
    "F1": ("Per-cell improvement of the reservation discipline over the best "
           "per-cell tuned baseline, across offered load. Each point is one of "
           "108 pre-registered core cells; the heavy line traces the per-load "
           "median. The deficit is U-shaped, deepest at nominal provisioning "
           "(22.4 to 23.6 percent at 1.0x), and the sign flips past "
           "saturation "
           "(+0.8 to +3.2 percent at 1.5x). Dashed lines mark the pre-registered "
           "WEAK (+5 percent) and SUPPORT (+10 percent) thresholds; no cell "
           "reaches SUPPORT. Open symbols mark the four cells failing the "
           "interactive guardrail (Section 6.4)."),
    "F2a": ("Interactive tail latency under reservations. Per-cell p95 "
            "time-to-first-token change versus the best baseline: at and "
            "above nominal load, reservations improve interactive tail "
            "latency by 63.3 to 97.1 percent by keeping agentic flows off the "
            "shared pool. The effect inverts in the heavy-agentic, "
            "overloaded, short-gap corner, where the guardrail fails by "
            "468.6 to 526.5 percent (Section 6.4)."),
    # The reference to the tail-latency figure is a \ref, not a literal
    # number: float numbers come from document order, so a literal would go
    # stale the next time a float moves. Same mechanism as the body's float
    # references (REFERENCE REMAP above).
    "F2b": ("What that isolation costs. Reserved-idle fraction per cell: the "
            "protection of Figure~" + r"\ref{fig:ttft}" + " is priced in "
            "capacity held idle, 93 to 100 percent of the reserved pool in 77 "
            "of 81 cells, with three load-1.5 cells at 88.9 percent and one "
            "cell granted no reservations (marked). This is the structural "
            "duty-cycle cost of Section 6.5."),
    "F4": ("Compliant-flow completion under adversarial runaway injection, "
           "absolute rates. The reservation discipline holds near 0.5 with a "
           "worst-case degradation of 3.1 points; FCFS completes 0.33 with no "
           "containment; the two strongest request-oriented baselines "
           "complete zero compliant flows at every level including no "
           "injection, instrumented admission starvation (one of 107 "
           "compliant flows ever scheduled, zero preemptions; Section 6.1). "
           "Deltas against zero are undefined, which is why rates are "
           "absolute."),
    "F5": ("Horizon censoring on one core cell. The population median agentic "
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
           "payoff (Section 6.2)."),
    "F3": ("Served value across the load sweep. The reservation discipline's "
           "area under curve (0.626) trails deadline-ordered class scheduling "
           "(0.648), "
           "killing the graceful-degradation hypothesis; the "
           "admission-throttling baselines collapse under overload "
           "(Section 6.5)."),
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
    "F2a": "F2a_interactive_ttft.pdf",
    "F2b": "F2b_reserved_idle.pdf",
    "F4": "F4_containment_starvation.pdf",
    "F5": "F5_horizon_lesson.pdf",
    "F3": "F3_served_value.pdf",
}
TAB_FILES = {"T1": "T1_roster", "T2": "T2_verdict",
             "T3": "T3_h3_rates", "T4": "T4_sensitivity"}

COL_W = "3.25in"   # MLSys column width (textwidth 6.75in, columnsep 0.25in)


def fig_block(name: str, label: str, double: bool = False) -> str:
    env = "figure*" if double else "figure"
    width = r"\textwidth" if double else COL_W
    return "\n".join([
        rf"\begin{{{env}}}[tbp]", r"\centering",
        rf"\includegraphics[width={width}]{{figures/{FIG_FILES[name]}}}",
        rf"\caption{{{CAPTIONS[name]}}}",
        rf"\label{{{label}}}",
        rf"\end{{{env}}}", ""])


def tab_block(name: str, label: str, small: bool = True,
              double: bool = False) -> str:
    env = "table*" if double else "table"
    size = r"\small" if small else r"\footnotesize"
    return "\n".join([
        rf"\begin{{{env}}}[tbp]", r"\centering", size,
        rf"\caption{{{TABLE_CAPTIONS[name]}}}",
        rf"\label{{{label}}}",
        rf"\input{{tables/{TAB_FILES[name]}}}",
        rf"\end{{{env}}}", ""])


# BODY floats: heading -> (block, citation token to place it after).
BODY_FLOATS = {
    "### 6.1 Request-oriented schedulers starve agentic flows":
        (fig_block("F4", "fig:containment"), "Figure 1"),
    "### 6.2 The horizon lesson":
        (fig_block("F5", "fig:horizon"), "Figure 2"),
}

# APPENDIX G, in the CG3 M5 order: F-h2, F-isolation panel (a), T1, T2, T3.
# F2a and T1 are full-width: the TTFT panel is a 108-cell grouped scatter and
# the roster is the table CG1 reported as overflowing worst. The appendix has
# no page limit, so neither is squeezed into a column.
APPENDIX_G = (fig_block("F1", "fig:improvement")
              + fig_block("F3", "fig:servedvalue")
              + fig_block("F2a", "fig:ttft", double=True)
              + fig_block("F2b", "fig:isolation")
              + tab_block("T1", "tab:roster", double=True)
              + tab_block("T2", "tab:verdict", double=True)
              + tab_block("T3", "tab:h3", small=False, double=True)
              + tab_block("T4", "tab:sens", small=False, double=True))

# Literal float reference in the markdown -> semantic label. See REFERENCE
# REMAP in the module docstring. Asserted exhaustive on every run.
REF_MAP = {
    ("Figure", 1): "fig:containment",   # F-lead,      body 6.1
    ("Figure", 2): "fig:horizon",       # F-horizon,   body 6.2
    # CG6 Track 1b took the reserved-idle panel and the sensitivity table
    # out of the body; Track 1c took the improvement plot. Their callouts
    # are gone with them, so only two figure references remain.
    ("Table", 1): "tab:roster",         # Appendix G, cited from 4
}

# Appendices whose markdown body is a placeholder instruction rather than
# prose. The placeholder is DROPPED and replaced by generated content; each
# is guarded by the prefix it must start with, so real prose can never be
# dropped by accident.
PLACEHOLDER_APPENDICES = {"G": "[CG6:", "H": "[Generated at packaging"}

# Appendices carried as pseudocode listings.
VERBATIM_APPENDICES = ("B", "C")

# Appendices that set ONE column: the pseudocode listings, whose lines cannot
# fit a 3.25in column, and the 108-row per-cell table. The appendix has no
# page limit, so this costs nothing.
ONECOLUMN_APPENDICES = ("B", "C", "H")
# ...and those directly followed by another one-column appendix, which
# therefore must NOT switch back: B is followed by C, and a \twocolumn
# immediately undone by the next \onecolumn is two clearpages in a row.
ONECOLUMN_FOLLOWERS = ("B",)

# The anonymous-branch wording, carried verbatim in the markdown and replaced
# by \ArtifactStatement at generation. Operator-ruled 2026-09-14.
ANON_LOCATION = ("The release is public and archived under a DOI; the "
                 "identifiers are withheld for anonymous review and are "
                 "supplied in the camera-ready.")

REMAPS: list[str] = []


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
    text = "".join(out)

    def _ref(m: re.Match) -> str:
        kind, num = m.group(1), int(m.group(2))
        label = REF_MAP.get((kind, num))
        if label is None:
            sys.exit(f"[gen] STOP: unmapped float reference {m.group(0)!r}. "
                     f"REF_MAP must cover every literal float reference in "
                     f"the markdown.")
        REMAPS.append(f"{m.group(0)} -> \\ref{{{label}}}")
        return rf"{kind}~\ref{{{label}}}"

    text = FLOATREF_RE.sub(_ref, text)
    # Escape literal underscores, never inside a citation key or a \ref key.
    parts = re.split(r"(\\(?:citep|ref)\{[^}]*\})", text)
    return "".join(p if p.startswith("\\") else p.replace("_", r"\_")
                   for p in parts)


def main() -> None:
    raw = MD.read_text()
    lines = raw.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "## Abstract")
    lines = lines[start:]

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
    appendices: list[str] = []

    def emit(fname: str, s: str) -> None:
        if fname not in files:
            files[fname] = []
            order.append(fname)
        files[fname].append(s)

    def emit_with_float(fname: str, head: str, text: str) -> None:
        """Body prose plus its float, the float after the citing paragraph."""
        if head not in BODY_FLOATS:
            emit(fname, prose(text) + "\n")
            return
        block, token = BODY_FLOATS[head]
        paras = text.split("\n\n")
        hit = [i for i, p in enumerate(paras)
               if re.search(re.escape(token) + r"\b", re.sub(r"\s+", " ", p))]
        if not hit:
            sys.exit(f"[gen] STOP: {head!r} declares a float cited as "
                     f"{token!r} but no paragraph cites it.")
        k = hit[0]
        emit(fname, prose("\n\n".join(paras[:k + 1])) + "\n\n")
        emit(fname, block)
        if paras[k + 1:]:
            emit(fname, prose("\n\n".join(paras[k + 1:])) + "\n")
        print(f"[gen] float for {head[:44]!r} placed after paragraph "
              f"{k + 1} (cites {token})")

    sec_re = re.compile(r"^## (\d+)\. (.*)$")
    sub_re = re.compile(r"^### (?:[A-H0-9]+(?:\.\d+)?\s+)?(.*)$")
    app_re = re.compile(r"^## Appendix ([A-H])\. (.*)$")

    fname = None
    in_appendix = False
    for head, body in chunks:
        text = "\n".join(body).strip("\n")

        if head == "## Abstract":
            fname = "00_abstract"
            emit(fname, "\\begin{abstract}\n" + prose(text)
                 + "\n\\end{abstract}\n")
            continue

        m = app_re.match(head)
        if m:
            in_appendix = True
            letter, title = m.group(1), m.group(2)
            fname = f"appendix_{letter}"
            appendices.append(letter)
            # \onecolumn issues a \clearpage, so it must come BEFORE the
            # heading. Emitted after it, the heading is stranded alone on a
            # page and the content starts on the next one, which is what
            # left pages 17 and 24 of the first reading build blank.
            if letter in ONECOLUMN_APPENDICES:
                emit(fname, "\\onecolumn\n")
            emit(fname, f"\\section{{{title}}}\n")
            guard = PLACEHOLDER_APPENDICES.get(letter)
            if guard:
                if not text.lstrip().startswith(guard):
                    sys.exit(f"[gen] STOP: Appendix {letter} was expected to "
                             f"hold a placeholder starting {guard!r}; it holds "
                             f"prose. Refusing to drop it.")
                if letter == "G":
                    emit(fname, APPENDIX_G)
                    emit(fname, "\\clearpage\n")
                else:  # H - the 108-row per-cell table, one column.
                    emit(fname,
                         "Per-cell H1 results, machine-generated from the "
                         "FINAL readout (cell, best baseline, improvement, 95 "
                         "percent CI, guardrail; all 108 cells).\n\n"
                         "{\\footnotesize\n\\input{tables/appendix_D_cells}\n}"
                         "\n")
            elif letter in VERBATIM_APPENDICES:
                emit(fname, "{\\small\n\\begin{verbatim}\n" + text
                     + "\n\\end{verbatim}\n}\n")
                if letter not in ONECOLUMN_FOLLOWERS:
                    emit(fname, "\\twocolumn\n")
            else:
                emit(fname, prose(text) + "\n")
            continue

        m = sec_re.match(head)
        if m:
            n, title = int(m.group(1)), m.group(2)
            slug = re.sub(r"[^a-z0-9]+", "_", title.lower())[:24].strip("_")
            fname = f"{n:02d}_{slug}"
            emit(fname, f"\\section{{{title}}}\n")
            emit_with_float(fname, head, text)
            continue

        if head == "## Artifacts":
            fname = "90_artifacts"
            emit(fname, "\\section*{Artifacts}\n")
            # The location sentence is the ONE build-dependent sentence in the
            # paper: the named build names the DOI and the repository, the
            # anonymous build names neither. The markdown carries the anonymous
            # form, which is the true statement for the submission, and the
            # generator swaps it for the macro so the two builds diverge where
            # they must. Same class of mechanical substitution as the float
            # REF_MAP above, and asserted the same way: exactly one match, or
            # the build stops.
            body_text = prose(text)
            # Whitespace-flexible: the markdown wraps at 72 columns, so the
            # sentence carries line breaks. Matching it literally found zero
            # and stopped the build, which is the assertion working.
            pat = re.compile(r"\s+".join(map(re.escape, ANON_LOCATION.split())))
            n = len(pat.findall(body_text))
            if n != 1:
                sys.exit(f"[gen] STOP: the artifact location sentence matched "
                         f"{n} times in the Artifacts section, expected 1.")
            emit(fname, pat.sub(r"\\ArtifactStatement", body_text) + "\n")
            continue

        m = sub_re.match(head)
        if m:
            title = m.group(1)
            emit(fname, f"\\subsection{{{title}}}\n")
            if in_appendix:
                emit(fname, prose(text) + "\n")
            else:
                emit_with_float(fname, head, text)
            continue

        sys.exit(f"[gen] STOP: unhandled heading: {head}")

    # --- integrity checks -----------------------------------------------
    if appendices != list("ABCDEFGH"):
        sys.exit(f"[gen] STOP: appendices {appendices} != A through H")
    body_order = [f for f in order if f[0].isdigit()]
    expect = ["00_abstract"] + [f"{i:02d}_" for i in range(1, 11)]
    got = [body_order[0]] + [f[:3] for f in body_order[1:-1]]
    if got != expect:
        sys.exit(f"[gen] STOP: body section ladder {got} != {expect}")
    unplaced = set(BODY_FLOATS) - {h for h in BODY_FLOATS}
    assert not unplaced

    # Stale cross-references inside the frozen captions. Reported, never
    # silently repaired: captions are frozen prose (CG6 T2-f).
    stale = []
    for name, cap in CAPTIONS.items():
        for s in re.findall(r"Section (\d+(?:\.\d+)?)", cap):
            stale.append(f"{name}: Section {s}")
    if stale:
        print("[gen] CAPTION CROSS-REFERENCES (post-CG4 numbering since "
              "PFC-5; check against the section ladder on any renumber):")
        for s in stale:
            print(f"        {s}")

    # --- write ----------------------------------------------------------
    generated = set()
    for fn in order:
        (HERE / f"{fn}.tex").write_text("\n".join(files[fn]))
        generated.add(f"{fn}.tex")
    stale_files = sorted(
        p.name for p in HERE.glob("*.tex")
        if re.fullmatch(r"(\d\d_.*|appendix_[A-Z])\.tex", p.name)
        and p.name not in generated)
    for name in stale_files:
        (HERE / name).unlink()
        print(f"[gen] removed stale generated source {name}")

    # DURABLE FIX for the defect this gate found: \ArtifactRepoURL was defined
    # in both branches and referenced ZERO times, in every build back through
    # a94b1a7, and the July TMLR submission shipped the same way. A defined
    # pointer nobody renders is invisible to every probe in the battery, so the
    # build now refuses it.
    # THE .bbl IS RENDERED OUTPUT AND NO SOURCE GREP SEES IT. Omitting it is
    # what produced the wrong finding at CG7 Phase 0: \ArtifactRepoURL was
    # reported as defined-and-never-referenced when evalspec2026's bibliography
    # entry had been consuming it since PKG2, as references.bib's own header
    # says. General rule: any assertion over "the rendered paper" includes the
    # bibliography.
    main = (HERE / "main_mlsys.tex").read_text()
    bbl = ""
    for b in ("main_mlsys.bbl", "main_mlsys_reading.bbl"):
        if (HERE / b).exists():
            bbl += (HERE / b).read_text()
    bib = (HERE / "references.bib").read_text()
    defined = set(re.findall(r"\\newcommand\{\\(Artifact\w+)\}", main))
    rendered = "".join("".join(files[f]) for f in order) + main + bbl + bib
    unused = sorted(m for m in defined
                    if len(re.findall(rf"\\{m}\b", rendered)) < 2)
    if unused:
        sys.exit(f"[gen] STOP: artifact macro(s) defined but never referenced "
                 f"in rendered output: {unused}. A pointer the paper does not "
                 f"print is a pointer the reader cannot follow.")
    where = {}
    for m in sorted(defined):
        src = []
        if re.search(rf"\\{m}\b", "".join("".join(files[f]) for f in order)):
            src.append("body")
        if len(re.findall(rf"\\{m}\b", main)) > 1:
            src.append("main")
        if re.search(rf"\\{m}\b", bbl):
            src.append("bbl")
        if re.search(rf"\\{m}\b", bib):
            src.append("bib")
        where[m] = "+".join(src)
    print(f"[gen] artifact macros all referenced: {where}")

    body = ["% body_mlsys.tex -- generated by gen_sections.py. Do not edit.",
            "% MLSys 2027 Research Track; structure ratified at CG2 and",
            "% executed at CG3/CG4. Regenerate, never hand-patch.", ""]
    body += [rf"\input{{{fn}}}" for fn in order if fn[0].isdigit()
             and fn != "00_abstract"]
    body += ["",
             r"\bibliographystyle{mlsys2025}", r"\bibliography{references}",
             "", r"\appendix"]
    body += [rf"\input{{appendix_{L}}}" for L in appendices]
    body += [""]
    (HERE / "body_mlsys.tex").write_text("\n".join(body))
    print(f"[gen] wrote {len(order)} source files + body_mlsys.tex "
          f"(body sections {len(body_order) - 1}, appendices "
          f"{len(appendices)})")
    print(f"[gen] float reference remaps ({len(REMAPS)}): "
          + "; ".join(sorted(set(REMAPS))))


if __name__ == "__main__":
    main()
