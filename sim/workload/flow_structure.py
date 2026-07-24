"""Per-flow structure generation (spec Section 5 FLOW STRUCTURE).

A ClassSpec captures one traffic class's distributions. `generate_flow` samples a
single flow: its invocation count, and for each invocation the new input tokens,
output tokens, accumulated KV context, and the tool-time gap before the next
invocation. Runaway flows (spec Section 5 adversarial family) amplify the count and
token draw and tighten the gaps.

House style: hyphens only (D-026).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..core.types import Flow, Invocation
from .distributions import LognormalSpec, sample_positive_int


@dataclass(frozen=True)
class RunawaySpec:
    """Amplification applied to a runaway agentic flow (spec Section 5 / H3)."""

    invocation_multiplier: float
    output_token_multiplier: float
    gap_shrink: float

    @classmethod
    def from_config(cls, cfg: dict) -> "RunawaySpec":
        return cls(
            invocation_multiplier=float(cfg["invocation_multiplier"]),
            output_token_multiplier=float(cfg["output_token_multiplier"]),
            gap_shrink=float(cfg["gap_shrink"]),
        )


@dataclass(frozen=True)
class ClassSpec:
    """Distributions defining one traffic class."""

    name: str
    invocations_per_flow: LognormalSpec
    input_tokens: LognormalSpec
    output_tokens: LognormalSpec
    gap_s: LognormalSpec
    value_weight: float

    @classmethod
    def from_config(cls, name: str, cfg: dict) -> "ClassSpec":
        return cls(
            name=name,
            invocations_per_flow=LognormalSpec.from_config(cfg["invocations_per_flow"]),
            input_tokens=LognormalSpec.from_config(cfg["input_tokens"]),
            output_tokens=LognormalSpec.from_config(cfg["output_tokens"]),
            gap_s=LognormalSpec.from_config(cfg["gap_s"]),
            value_weight=float(cfg["value_weight"]),
        )

    def mean_tokens_per_flow(self) -> float:
        """Expected total tokens (input+output over all invocations) per flow.

        Used to calibrate the class mix to a target token-volume share.
        """
        return (self.invocations_per_flow.mean()
                * (self.input_tokens.mean() + self.output_tokens.mean()))


def generate_flow(
    flow_id: int,
    arrival_time: float,
    spec: ClassSpec,
    rng: np.random.Generator,
    runaway: bool = False,
    runaway_spec: RunawaySpec | None = None,
) -> Flow:
    """Sample one flow of the given class.

    KV context accumulates across invocations (spec Section 5: "KV footprint grows
    with accumulated context"): invocation j's `context_tokens` is the running sum
    of all input and output tokens up to and including step j.
    """
    n_inv = sample_positive_int(spec.invocations_per_flow, rng, floor=1)
    out_mult = 1.0
    gap_mult = 1.0
    if runaway:
        if runaway_spec is None:
            raise ValueError("runaway=True requires a runaway_spec")
        n_inv = max(1, int(round(n_inv * runaway_spec.invocation_multiplier)))
        out_mult = runaway_spec.output_token_multiplier
        gap_mult = runaway_spec.gap_shrink

    invocations: list[Invocation] = []
    accumulated = 0
    for j in range(n_inv):
        inp = sample_positive_int(spec.input_tokens, rng, floor=1)
        out = max(1, int(round(
            sample_positive_int(spec.output_tokens, rng, floor=1) * out_mult)))
        accumulated += inp + out
        # Gap after this step (tool-time), zero after the final invocation.
        if j < n_inv - 1:
            gap = spec.gap_s.sample_scalar(rng) * gap_mult
        else:
            gap = 0.0
        invocations.append(Invocation(
            flow_id=flow_id,
            index=j,
            prefill_tokens=inp,
            output_tokens=out,
            context_tokens=accumulated,
            gap_after_s=gap,
        ))

    return Flow(
        flow_id=flow_id,
        arrival_time=arrival_time,
        invocations=invocations,
        cls=spec.name,
        value_weight=spec.value_weight,
        is_runaway=runaway,
    )
