"""SG7 H1 verdict logic (spec Section 3), on hand-computable synthetic manifests.

This is the machinery D-058 executes the filing off, so every branch of the
support/weak/kill classification, the corner-cell exclusions, the best-baseline-per-
cell rule, and the guardrail gate is pinned against inputs whose verdict is obvious
by hand.
"""

from sim.analysis.h1_readout import (
    corner_exclusion,
    h1_verdict,
    h2_auc,
    h3_deltas,
)


def _m(policy, mix, load, goodput, ttft=0.5, ndev=8, gap=20.0, seed=1,
       served=0.9, runaway=0.0, compliant=1.0):
    """Build one synthetic manifest."""
    return {
        "cell_key": f"mix{mix}_load{load}_dev{ndev}_gap{gap}",
        "spec": {"policy": policy, "mix": mix, "load": load, "num_devices": ndev,
                 "slots": 16, "gap_median_s": gap, "seed": seed,
                 "runaway_fraction": runaway},
        "metrics": {"goodput_per_gpu_hour": goodput, "p95_ttft_s": ttft,
                    "served_value_fraction": served},
        "compliant_completion_rate": compliant,
    }


def _cell(mix, load, ours_goodput, base_goodput, ours_ttft=0.5, base_ttft=0.5,
          gap=20.0, n_seeds=3):
    """A full cell: reservation vs one baseline (niyama), n seeds each."""
    ms = []
    for s in range(n_seeds):
        ms.append(_m("reservation", mix, load, ours_goodput, ours_ttft,
                     gap=gap, seed=s))
        ms.append(_m("niyama_style", mix, load, base_goodput, base_ttft,
                     gap=gap, seed=s))
    return ms


# --- corner exclusions -------------------------------------------------------------

def test_corner_exclusions():
    assert corner_exclusion(0.3, 1.0, 20.0) is None            # normal core cell
    assert corner_exclusion(0.3, 1.0, 16 * 60) == "gap median > 15 min"
    assert corner_exclusion(0.8, 1.0, 20.0) == "agentic share > 70 percent"
    assert corner_exclusion(0.3, 0.5, 20.0) == "offered load < 0.6x"


# --- the three verdicts ------------------------------------------------------------

def test_support_when_60pct_of_cells_beat_10pct_with_guardrail():
    # 3 cells all at +20%, guardrail fine -> SUPPORT.
    ms = (_cell(0.3, 0.7, 120.0, 100.0) + _cell(0.3, 1.0, 120.0, 100.0)
          + _cell(0.3, 1.3, 120.0, 100.0))
    v = h1_verdict(ms)
    assert v.verdict == "SUPPORT", v.reason
    assert v.support_fraction == 1.0
    assert abs(v.median_improvement - 0.20) < 1e-6


def test_kill_when_median_improvement_below_5pct():
    # reservation loses ~-15% everywhere -> KILL (this is the pilot's shape).
    ms = (_cell(0.3, 0.7, 85.0, 100.0) + _cell(0.3, 1.0, 85.0, 100.0)
          + _cell(0.3, 1.3, 85.0, 100.0))
    v = h1_verdict(ms)
    assert v.verdict == "KILL", v.reason
    assert v.median_improvement < 0.05


def test_weak_when_median_between_5_and_10pct():
    # +7% everywhere: guardrail intact, but <60% cross the +10% support bar -> WEAK.
    ms = (_cell(0.3, 0.7, 107.0, 100.0) + _cell(0.3, 1.0, 107.0, 100.0)
          + _cell(0.3, 1.3, 107.0, 100.0))
    v = h1_verdict(ms)
    assert v.verdict == "WEAK", v.reason
    assert 0.05 <= v.median_improvement < 0.10
    assert v.support_fraction < 0.60


def test_kill_when_guardrail_violated_across_region():
    # Big goodput win, but interactive TTFT is 50% worse everywhere -> KILL.
    ms = (_cell(0.3, 0.7, 130.0, 100.0, ours_ttft=0.75, base_ttft=0.5)
          + _cell(0.3, 1.0, 130.0, 100.0, ours_ttft=0.75, base_ttft=0.5)
          + _cell(0.3, 1.3, 130.0, 100.0, ours_ttft=0.75, base_ttft=0.5))
    v = h1_verdict(ms)
    assert v.verdict == "KILL", v.reason
    assert "guardrail" in v.reason


# --- the aggregation rules --------------------------------------------------------

def test_best_baseline_is_selected_per_cell():
    """The strongest baseline in EACH cell is the opponent, not a fixed one."""
    ms = _cell(0.3, 1.0, 118.0, 100.0)          # niyama at 100
    ms += [_m("vllm_style", 0.3, 1.0, 115.0, seed=s) for s in range(3)]  # stronger
    v = h1_verdict(ms)
    cell = v.cells[0]
    assert cell.best_baseline == "vllm_style"    # 115 > 100
    # improvement measured vs 115, not 100: 118/115 - 1 ~= +2.6%
    assert abs(cell.improvement - (118.0 / 115.0 - 1.0)) < 0.02


def test_corner_cells_do_not_count_toward_support():
    """A huge win confined to a corner cell must not produce SUPPORT."""
    # One core cell (mild loss) + two corner cells (load 0.5) with huge wins.
    ms = _cell(0.3, 1.0, 95.0, 100.0)
    ms += _cell(0.3, 0.5, 200.0, 100.0)          # corner: load < 0.6x
    ms += _cell(0.5, 0.5, 200.0, 100.0)          # corner: load < 0.6x
    v = h1_verdict(ms)
    assert v.core_cells == 1                       # only the load-1.0 cell counts
    assert v.excluded_cells == 2
    assert v.verdict == "KILL", v.reason           # the one core cell is a loss


# --- H2 and H3 --------------------------------------------------------------------

def test_h2_auc_trapezoid_is_correct():
    # reservation served-value 0.9 flat across loads 1.0 and 2.0 -> normalized AUC 0.9.
    ms = [_m("reservation", 0.3, 1.0, 100.0, served=0.9),
          _m("reservation", 0.3, 2.0, 100.0, served=0.9),
          _m("niyama_style", 0.3, 1.0, 100.0, served=0.6),
          _m("niyama_style", 0.3, 2.0, 100.0, served=0.4)]
    r = h2_auc(ms)
    assert abs(r["auc"]["reservation"] - 0.9) < 1e-9
    assert abs(r["auc"]["niyama_style"] - 0.5) < 1e-9   # mean of 0.6 and 0.4
    assert r["relative"] > 0                              # 0.9 vs 0.5


def test_h3_delta_is_bounded_vs_unbounded():
    # reservation holds compliant completion ~1.0 as injection rises; baseline falls.
    ms = []
    for inj, ours_rate, base_rate in [(0.0, 1.0, 1.0), (0.05, 0.98, 0.6),
                                      (0.10, 0.97, 0.3)]:
        ms.append(_m("reservation", 0.3, 1.0, 100.0, runaway=inj, compliant=ours_rate))
        ms.append(_m("niyama_style", 0.3, 1.0, 100.0, runaway=inj, compliant=base_rate))
    d = h3_deltas(ms)
    assert d["reservation"]["worst_delta"] > -0.05      # bounded
    assert d["niyama_style"]["worst_delta"] < -0.5       # unbounded collapse


def test_duplicate_runids_are_collapsed_not_double_counted():
    """The same run synced twice must not inflate the seed count."""
    ms = _cell(0.3, 1.0, 120.0, 100.0, n_seeds=3)
    for m in ms:                                   # forge stable run_ids
        m["run_id"] = f"{m['spec']['policy']}-{m['spec']['seed']}"
    v = h1_verdict(ms + ms)                          # each manifest duplicated
    assert v.core_cells == 1                         # still one cell, not two


def test_stale_schema_manifests_are_a_hard_error():
    """Two cell_keys for the same workload (a leftover from a schema change) raises."""
    import pytest
    ms = _cell(0.3, 1.0, 120.0, 100.0, n_seeds=2)
    stale = _cell(0.3, 1.0, 120.0, 100.0, n_seeds=2)
    for m in stale:
        m["cell_key"] = m["cell_key"].replace("_gap20.0", "_gap20.0_OLD")  # old schema
    with pytest.raises(ValueError, match="same workload"):
        h1_verdict(ms + stale)
