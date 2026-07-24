"""SG1 sanity test: Little's law and M/M/1 theory (brief Section 6, SG1)."""

from sim.sanity.littles_law import run_littles_law


def test_littles_law_identity_holds():
    """L == lambda * W (validates the engine's in-system / sojourn accounting)."""
    r = run_littles_law()
    assert r["completions"] > 5000
    rel_err = abs(r["L"] - r["lambda_W"]) / r["L"]
    assert rel_err < 1e-3, f"Little's law identity violated: rel err {rel_err:.2%}"


def test_mm1_matches_closed_form():
    """L and utilization match M/M/1 theory within sampling tolerance."""
    r = run_littles_law()
    l_err = abs(r["L"] - r["L_theory_mm1"]) / r["L_theory_mm1"]
    u_err = abs(r["utilization"] - r["rho"]) / r["rho"]
    assert l_err < 0.10, f"L vs rho/(1-rho): rel err {l_err:.2%}"
    assert u_err < 0.05, f"utilization vs rho: rel err {u_err:.2%}"
