"""Domain types for the T-055 discrete-event serving simulator.

Simulation is at INVOCATION granularity (spec Section 6): a flow is a sequence of
invocations, each invocation is a unit of prefill+decode work served on a device.
These are plain data carriers -- no policy logic and no physics live here (the
engine owns physics, policies own decisions; see sim/scheduler/interface.py).

House style: hyphens only (D-026).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class InvPhase(Enum):
    """Lifecycle phase of an invocation."""

    PENDING = "pending"      # created, not yet scheduled onto a device
    PREFILL = "prefill"      # occupying a device slot, running prefill / entry latency
    DECODE = "decode"        # in the device decode processor-sharing set
    PREEMPTED = "preempted"  # evicted mid-flight, awaiting reschedule (SG3)
    COMPLETE = "complete"    # finished all output tokens


class PinMode(Enum):
    """How a reservation holds its KV across inter-invocation tool-time (SG4).

    STRICT: the KV stays resident in device HBM across the gap. Zero restore
    latency when the flow returns, but the HBM is stranded for the whole gap.

    TIERED: the KV spills to host memory across the gap, freeing HBM for
    best-effort work, with a GUARANTEED restore -- the flow gets its HBM back when
    it returns (best-effort work is preemptible, so the restore cannot be blocked).
    The price is the spill-out/spill-in latency.

    Both hold the flow's COMPUTE slot across the gap; that is the reservation's
    guarantee, and it is what the reserved-idle metric prices. Spec Section 4
    fixes both embodiments; ablation A4 compares them.
    """

    STRICT = "strict"
    TIERED = "tiered"


@dataclass
class Reservation:
    """A per-flow reservation: compute + pinned KV + cross-invocation token budget.

    This is the mechanism the paper and the provisional are about (D-049). It is
    created by the reservation policy's admission decision and carried on the Admit
    action; the ENGINE establishes and enforces it (pin, non-preemption, budget
    metering) and validates that the policy's ledger never over-commits.
    """

    flow_id: int
    hbm_gib: float                 # KV pinned for this flow (sized to peak context)
    token_budget: float            # total tokens across ALL invocations of the flow
    pin_mode: PinMode = PinMode.STRICT
    non_preemptible: bool = True   # the guarantee; ablation A2 turns this off

    # Bound by the engine at admission.
    device_id: int | None = None


@dataclass(frozen=True)
class DegradeSpec:
    """The effect and the price of one degradation action (spec Section 10).

    A degradation action reduces the work a flow will still do (`token_scale` on the
    output of its not-yet-started invocations) and costs quality, which is charged
    against the flow's value weight -- so a degraded flow still completes but
    contributes less served value. That is exactly what the H2 graceful-degradation
    curve (served value vs offered load) measures.

    The quality-cost vector is an ASSUMPTION drawn from the cascade/tiering
    literature (spec Section 10) and is swept in sensitivity.
    """

    name: str
    token_scale: float     # multiply remaining output tokens by this (0..1)
    quality_cost: float    # fraction of value weight forfeited (0..1)


class PreemptMode(Enum):
    """How a preempted invocation's KV is handled (spec Section 4, vLLM variants).

    RECOMPUTE: KV is discarded; on resume the accumulated context is re-prefilled
    (compute cost, wasted work). SWAP: KV is copied to host memory and copied back
    on resume (host-bandwidth cost, no recompute). The engine owns both mechanics;
    a policy chooses which by tagging its Preempt action.
    """

    RECOMPUTE = "recompute"
    SWAP = "swap"


@dataclass
class Invocation:
    """One prefill+decode unit of work within a flow.

    Attributes set at construction describe the demand; the remaining fields are
    runtime state mutated by the engine as the invocation is served.
    """

    flow_id: int
    index: int                    # position within the flow (0-based)
    prefill_tokens: int           # NEW input tokens to prefill this step (compute)
    output_tokens: int            # tokens generated this step (decode)

    # Accumulated KV context (spec Section 5: "KV footprint grows with accumulated
    # context"). Peak resident context length for this invocation = prior context
    # carried in the flow + this step's input + this step's output. Set by the
    # Section 5 generators; None for single-step sanity workloads, in which case
    # the footprint falls back to this step's own tokens.
    context_tokens: int | None = None

    # Tool-time before the flow's NEXT invocation becomes ready (spec Section 5
    # inter-invocation gap). The reservation sits idle across this gap -- the
    # stranding phenomenon the paper studies. Carried as workload data here; the
    # engine begins honoring it at SG3 (see docs/architecture.md).
    gap_after_s: float = 0.0

    # Runtime state (engine-owned).
    phase: InvPhase = InvPhase.PENDING
    decode_remaining: float = 0.0     # output tokens not yet generated
    device_id: int | None = None
    ready_time: float | None = None
    start_time: float | None = None   # entered prefill (first time)
    decode_start_time: float | None = None  # first entered DECODE (drives TTFT)
    complete_time: float | None = None
    resume_mode: PreemptMode | None = None  # set when preempted; how to resume (SG3)
    preemptions: int = 0              # times this invocation has been preempted

    def __post_init__(self) -> None:
        self.decode_remaining = float(self.output_tokens)

    @property
    def generated_tokens(self) -> float:
        """Output tokens generated so far (0 during prefill, output_tokens at end)."""
        return max(0.0, self.output_tokens - self.decode_remaining)

    @property
    def materialized_context_tokens(self) -> int:
        """Context currently resident in KV: peak context minus not-yet-generated.

        Drives the preemption resume cost (recompute re-prefill / swap volume).
        """
        return max(1, self.max_context_tokens - int(round(self.decode_remaining)))

    @property
    def max_context_tokens(self) -> int:
        """Peak context length driving the KV reservation.

        Uses the accumulated `context_tokens` when the generator provides it;
        otherwise falls back to this step's own input+output tokens (the SG1
        single-step behavior).
        """
        if self.context_tokens is not None:
            return self.context_tokens
        return self.prefill_tokens + self.output_tokens


@dataclass
class Flow:
    """A sequence of invocations submitted as one long-horizon agentic request.

    For SG1 sanity workloads a flow holds exactly one invocation. Multi-invocation
    flows with inter-invocation gaps arrive with the Section 5 generators at SG2.
    """

    flow_id: int
    arrival_time: float
    invocations: list[Invocation]
    cls: str = "default"          # traffic class (interactive / agentic / ...)
    value_weight: float = 1.0     # goodput value weight (spec Section 2)
    is_runaway: bool = False      # adversarial runaway flow (spec Section 5, H3)

    # Runtime state (engine-owned).
    cursor: int = 0               # index of the next invocation to become ready
    admitted: bool = False
    admit_time: float | None = None
    complete_time: float | None = None

    # Reservation state (SG4). `reservation` is None for best-effort (aggregate
    # class) flows. `budget_remaining` is the cross-invocation token budget, metered
    # by the engine at decode; a flow that exhausts it is abandoned -- that is what
    # contains runaway flows (H3).
    reservation: Reservation | None = None
    budget_remaining: float | None = None
    abandoned: bool = False
    degradations: int = 0

    @property
    def is_reserved(self) -> bool:
        return self.reservation is not None

    @property
    def peak_context_tokens(self) -> int:
        """Largest KV context this flow will ever hold -- the pin size."""
        return max(inv.max_context_tokens for inv in self.invocations)

    @property
    def total_output_tokens(self) -> int:
        return sum(inv.output_tokens for inv in self.invocations)

    @property
    def is_complete(self) -> bool:
        return self.cursor >= len(self.invocations)

    def next_invocation(self) -> Invocation | None:
        """The next invocation to run, or None if the flow is exhausted."""
        if self.is_complete:
            return None
        return self.invocations[self.cursor]


@dataclass
class RunConfig:
    """Resolved run parameters (built from YAML by the orchestration layer)."""

    horizon_s: float
    warmup_s: float = 0.0
    seed: int = 0
    tick_interval_s: float | None = None   # None disables periodic ticks
    extra: dict = field(default_factory=dict)
