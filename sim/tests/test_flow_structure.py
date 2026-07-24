"""SG2: per-flow structure matches spec Section 5."""

import numpy as np

from sim.core.orchestration import CONFIG_DIR, load_yaml
from sim.core.seed import SeedManager
from sim.workload.flow_structure import ClassSpec, generate_flow


def _agentic():
    cfg = load_yaml(CONFIG_DIR / "workload.yaml")
    return ClassSpec.from_config("agentic", cfg["classes"]["agentic"])


def _interactive():
    cfg = load_yaml(CONFIG_DIR / "workload.yaml")
    return ClassSpec.from_config("interactive", cfg["classes"]["interactive"])


def test_agentic_invocation_and_gap_distributions():
    spec = _agentic()
    rng = SeedManager(1).stream("s")
    flows = [generate_flow(i, 0.0, spec, rng) for i in range(8000)]

    counts = np.array([len(f.invocations) for f in flows])
    assert abs(np.percentile(counts, 50) - 12) / 12 < 0.15
    assert abs(np.percentile(counts, 95) - 80) / 80 < 0.20

    gaps = np.array([inv.gap_after_s for f in flows for inv in f.invocations
                     if inv.gap_after_s > 0])
    assert abs(np.percentile(gaps, 50) - 20) / 20 < 0.10


def test_kv_context_accumulates_monotonically():
    spec = _agentic()
    rng = SeedManager(2).stream("s")
    flow = generate_flow(0, 0.0, spec, rng)
    ctx = [inv.context_tokens for inv in flow.invocations]
    assert all(b > a for a, b in zip(ctx, ctx[1:]))
    # Final accumulated context == sum of all input+output tokens.
    total = sum(inv.prefill_tokens + inv.output_tokens for inv in flow.invocations)
    assert flow.invocations[-1].context_tokens == total


def test_interactive_is_single_turn_no_gap():
    spec = _interactive()
    rng = SeedManager(3).stream("s")
    for i in range(200):
        flow = generate_flow(i, 0.0, spec, rng)
        assert len(flow.invocations) == 1
        assert flow.invocations[0].gap_after_s == 0.0
