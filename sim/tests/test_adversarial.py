"""SG2: the adversarial runaway family is demonstrably runaway."""

import numpy as np

from sim.core.orchestration import CONFIG_DIR, load_yaml
from sim.core.seed import SeedManager
from sim.workload.flow_structure import ClassSpec, RunawaySpec, generate_flow
from sim.workload.generator import WorkloadGenerator
from sim.workload.validation import materialize


def _specs():
    cfg = load_yaml(CONFIG_DIR / "workload.yaml")
    agentic = ClassSpec.from_config("agentic", cfg["classes"]["agentic"])
    runaway = RunawaySpec.from_config(cfg["adversarial"]["runaway"])
    return agentic, runaway


def _tokens(flow):
    return sum(inv.prefill_tokens + inv.output_tokens for inv in flow.invocations)


def test_runaway_flows_dwarf_normal_flows():
    agentic, rspec = _specs()
    rng_n = SeedManager(1).stream("normal")
    rng_r = SeedManager(1).stream("runaway")
    normal = [generate_flow(i, 0.0, agentic, rng_n) for i in range(2000)]
    runaway = [generate_flow(i, 0.0, agentic, rng_r, runaway=True, runaway_spec=rspec)
               for i in range(2000)]

    n_inv = np.median([len(f.invocations) for f in normal])
    r_inv = np.median([len(f.invocations) for f in runaway])
    assert r_inv > 10 * n_inv, (n_inv, r_inv)

    n_tok = np.median([_tokens(f) for f in normal])
    r_tok = np.median([_tokens(f) for f in runaway])
    assert r_tok > 10 * n_tok, (n_tok, r_tok)


def test_injection_fraction_matches_target():
    cfg = load_yaml(CONFIG_DIR / "workload.yaml")
    cfg["adversarial"]["runaway_fraction_of_agentic"] = 0.10
    flows = materialize(WorkloadGenerator(cfg, seed=9, horizon_s=20_000.0))
    agentic = [f for f in flows if f.cls == "agentic"]
    frac = sum(f.is_runaway for f in agentic) / len(agentic)
    assert abs(frac - 0.10) < 0.05, frac
