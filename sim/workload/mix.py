"""Traffic-mix calibration (spec Section 5 MIX: ratios by TOKEN VOLUME).

The spec expresses the mix as fractions of total token volume (90/10, 70/30,
50/50 interactive/agentic), not fractions of flows. Since agentic flows carry far
more tokens than interactive ones, the per-flow class probability must be solved
so the achieved token-volume share matches the target.

Let a = target agentic token-volume share, M_i and M_a the mean tokens per flow of
each class, and f the probability a flow is agentic. Then

    f * M_a / (f * M_a + (1 - f) * M_i) = a
    => f = a * M_i / (a * M_i + (1 - a) * M_a)

House style: hyphens only (D-026).
"""

from __future__ import annotations


def agentic_flow_probability(
    agentic_token_share: float,
    mean_tokens_interactive: float,
    mean_tokens_agentic: float,
) -> float:
    """Per-flow probability of being agentic to hit a target token-volume share."""
    a = agentic_token_share
    if a <= 0.0:
        return 0.0
    if a >= 1.0:
        return 1.0
    numer = a * mean_tokens_interactive
    denom = a * mean_tokens_interactive + (1.0 - a) * mean_tokens_agentic
    return numer / denom
