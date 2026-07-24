"""Wall-clock cap: a collapsed run records the collapse and NO metrics.

Operator ruling 2026-07-21 (option 3). A run that stops making progress -- observed
as congestion collapse in niyama_style at (mix 0.5, load 0.7, dev 32, gap 20), where
one trajectory reached a 60,231-deep queue and ran 23.8h before an OOM at 77 GB -- is
capped and recorded as collapsed.

The load-bearing property is what a capped run must NOT write: metrics accumulated up
to a cut-off. Those are precisely the right-censored quantity Amendment 2 exists to
keep out of the verdict; writing them under a different name would smuggle the bias
back in. These tests pin that, and pin that the readout discloses the resulting
selection effect instead of silently averaging survivors.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import time

import pytest

from sim.analysis.h1_readout import _by_cell_policy, collapse_report
from sim.experiments.grid import RunWallClockExceeded, _wall_clock_cap


# --- the cap itself -----------------------------------------------------------------

def test_cap_interrupts_a_pure_python_spin():
    """The observed collapse spins in Python, so the cap must interrupt that."""
    t0 = time.time()
    with pytest.raises(RunWallClockExceeded):
        with _wall_clock_cap(0.5):
            while True:            # the shape of the niyama spin: no I/O, no sleep
                sum(range(1000))
    assert time.time() - t0 < 5.0, "cap did not fire promptly"


def test_cap_does_not_fire_when_the_run_finishes():
    with _wall_clock_cap(30.0):
        total = sum(range(1000))
    assert total > 0


def test_cap_disabled_when_none():
    with _wall_clock_cap(None):
        pass


def test_timer_is_cleared_afterwards():
    """A leaked timer would kill an unrelated later run -- a silent, awful failure."""
    with _wall_clock_cap(0.5):
        pass
    time.sleep(1.0)     # would raise here if the itimer were still armed


# --- what a collapsed manifest does to aggregation ----------------------------------

def _m(policy, seed, *, collapsed=False, goodput=100.0, ck="cellA"):
    spec = {"policy": policy, "seed": seed, "mix": 0.5, "load": 0.7,
            "num_devices": 32, "gap_median_s": 20.0, "horizon_s": 1500.0,
            "runaway_fraction": 0.0}
    m = {"run_id": f"{policy}-{seed}-{int(collapsed)}", "cell_key": ck, "spec": spec}
    if collapsed:
        m["collapsed"] = True
        m["wall_cap_s"] = 3600.0
        m["diagnostics"] = {"queue_depth": 60231}
    else:
        m["collapsed"] = False
        m["metrics"] = {"goodput_per_gpu_hour": goodput, "p95_ttft_s": 1.0}
    return m


def test_collapsed_run_contributes_no_metrics():
    cells = _by_cell_policy([
        _m("niyama_style", 1, collapsed=True),
        _m("niyama_style", 2, goodput=500.0),
        _m("niyama_style", 3, collapsed=True),
    ])
    series = cells["cellA"]["policies"]["niyama_style"]["goodput_per_gpu_hour"]
    assert series == [500.0], "a capped run leaked a number into the aggregate"


def test_collapsed_run_is_recorded_not_discarded():
    """Excluded from the aggregate, but the collapse itself must survive."""
    cells = _by_cell_policy([_m("niyama_style", 1, collapsed=True)])
    events = cells["cellA"]["collapsed"]["niyama_style"]
    assert len(events) == 1
    assert events[0]["seed"] == 1
    assert events[0]["diagnostics"]["queue_depth"] == 60231


def test_a_fully_collapsed_policy_is_absent_from_the_cell():
    """No metrics at all -> the policy simply is not a candidate baseline there."""
    cells = _by_cell_policy([
        _m("niyama_style", 1, collapsed=True),
        _m("niyama_style", 2, collapsed=True),
        _m("fcfs", 1, goodput=90.0),
    ])
    assert "niyama_style" not in cells["cellA"]["policies"]
    assert "fcfs" in cells["cellA"]["policies"]


# --- the disclosure -----------------------------------------------------------------

def test_partial_collapse_is_flagged_as_a_selection_effect():
    """The real 2026-07-21 shape: collapses on 2 seeds, completes on 1."""
    rows = collapse_report([
        _m("niyama_style", 1, collapsed=True),
        _m("niyama_style", 2, goodput=500.0),
        _m("niyama_style", 3, collapsed=True),
    ])
    assert len(rows) == 1
    r = rows[0]
    assert r["policy"] == "niyama_style"
    assert r["collapsed_seeds"] == [1, 3]
    assert r["surviving_seeds"] == 1
    assert r["partial"] is True, (
        "a policy recorded only from the seeds where it survived is a selection "
        "effect and must be flagged, not averaged silently")


def test_total_collapse_is_not_a_selection_effect():
    rows = collapse_report([
        _m("niyama_style", 1, collapsed=True),
        _m("niyama_style", 2, collapsed=True),
    ])
    assert rows[0]["partial"] is False


def test_clean_results_report_no_collapses():
    assert collapse_report([_m("niyama_style", 1), _m("fcfs", 1)]) == []
