"""The scheduler plug-in interface (brief Section 4 -- load-bearing design rule).

STRICT policy/engine separation:

  * The ENGINE owns all physics and mechanics (service_model.py, engine.py).
  * A POLICY is a pure decision module. It receives events plus a READ-ONLY view
    of cluster state and returns actions from a fixed vocabulary. It never mutates
    cluster state, never computes service costs, and never sees another policy's
    workload differently -- every policy in a cell sees the identical workload
    realization.

Fixed action vocabulary (brief Section 4):
    admit(flow, cls|reservation), schedule(invocation, device), preempt(invocation),
    degrade(flow, action), reject(flow), no_op.

For SG1 the FCFS floor policy uses only Admit / Schedule / Reject / NoOp. Preempt
and Degrade are defined here (so the vocabulary is complete and stable) but the
engine rejects them until SG3/SG4 wire their mechanics -- a policy emitting them at
SG1 is a hard error, never a silent no-op.

House style: hyphens only (D-026).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..core.types import Flow, Invocation, PreemptMode, Reservation


# --- events (engine -> policy) --------------------------------------------------

@dataclass(frozen=True)
class FlowArrival:
    """A new flow has arrived and awaits an admission decision."""
    flow: Flow
    now: float


@dataclass(frozen=True)
class InvocationReady:
    """An admitted flow's next invocation is ready to be scheduled."""
    invocation: Invocation
    now: float


@dataclass(frozen=True)
class InvocationComplete:
    """An invocation finished; frees capacity and may unblock queued work."""
    invocation: Invocation
    now: float


@dataclass(frozen=True)
class Tick:
    """Periodic scheduling opportunity (for time-driven policies)."""
    now: float


Event = FlowArrival | InvocationReady | InvocationComplete | Tick


# --- actions (policy -> engine) -------------------------------------------------

@dataclass(frozen=True)
class Admit:
    """Admit a flow -- either into an aggregate (best-effort) class, or WITH a
    reservation (spec Section 4: `admit(flow, class | reservation)`).

    When `reservation` is set the engine establishes the pin, the non-preemption
    guarantee, and the cross-invocation token budget, and validates that the
    policy's ledger has not over-committed compute or HBM.
    """

    flow: Flow
    cls: str = "default"
    reservation: Reservation | None = None
    # Cross-invocation token budget for an AGGREGATE-class (non-reserved) flow.
    # The reservation discipline meters every flow it admits, not just reserved
    # ones -- that is what contains a runaway that was too big to pin (H3).
    # Baselines pass None: they have no cross-invocation budget, only per-request
    # caps, which is exactly the comparison H3 makes.
    budget_tokens: float | None = None


@dataclass(frozen=True)
class Reject:
    flow: Flow


@dataclass(frozen=True)
class Schedule:
    invocation: Invocation
    device_id: int


@dataclass(frozen=True)
class Preempt:
    invocation: Invocation
    mode: PreemptMode = PreemptMode.RECOMPUTE


@dataclass(frozen=True)
class Degrade:
    flow: Flow
    action: str


@dataclass(frozen=True)
class NoOp:
    pass


Action = Admit | Reject | Schedule | Preempt | Degrade | NoOp


# --- read-only cluster state view (engine -> policy) ----------------------------

@dataclass(frozen=True)
class DeviceView:
    """Immutable snapshot of one device's schedulable capacity.

    `slots_used` counts pins (held even while idle) plus best-effort residents, so
    `free_slots` is what an aggregate-class flow may actually compete for.
    """
    device_id: int
    batch_size: int
    num_resident: int
    slots_used: int
    pinned_slots: int
    max_concurrent_decodes: int
    hbm_used_gib: float
    hbm_capacity_gib: float
    # HBM committed to pins, including spilled ones (owed their memory back). New
    # pins must fit against this; best-effort work may fit against free HBM.
    hbm_committed_gib: float = 0.0
    # Flow ids whose TIERED pin currently sits in host memory. Their HBM is free for
    # best-effort work right now, but is owed back to them on restore.
    spilled_pins: frozenset[int] = frozenset()

    @property
    def free_slots(self) -> int:
        return self.max_concurrent_decodes - self.slots_used

    @property
    def hbm_free_gib(self) -> float:
        """HBM a BEST-EFFORT invocation may use (spilled pins' memory included)."""
        return self.hbm_capacity_gib - self.hbm_used_gib

    @property
    def hbm_pinnable_gib(self) -> float:
        """HBM a NEW pin may claim: free of every commitment, spilled ones included."""
        return self.hbm_capacity_gib - self.hbm_committed_gib


@dataclass(frozen=True)
class ClusterStateView:
    """Immutable snapshot of the cluster passed to a policy on every event."""
    now: float
    devices: tuple[DeviceView, ...]

    def device(self, device_id: int) -> DeviceView:
        return self.devices[device_id]

    @property
    def slot_headroom(self) -> float:
        """Free decode slots as a fraction of provisioned slots (0..1)."""
        total = sum(d.max_concurrent_decodes for d in self.devices)
        if total <= 0:
            return 0.0
        free = sum(max(0, d.free_slots) for d in self.devices)
        return free / total

    @property
    def hbm_headroom(self) -> float:
        """Free HBM as a fraction of provisioned HBM (0..1)."""
        total = sum(d.hbm_capacity_gib for d in self.devices)
        if total <= 0:
            return 0.0
        free = sum(max(0.0, d.hbm_free_gib) for d in self.devices)
        return free / total

    @property
    def headroom(self) -> float:
        """Cluster headroom = the BINDING constraint (spec Section 4 degradation key).

        Degradation is keyed on how much room is actually left, which is whichever
        of compute and memory is tighter.
        """
        return min(self.slot_headroom, self.hbm_headroom)


# --- the policy contract --------------------------------------------------------

class Policy(ABC):
    """Base class for all scheduling policies (ours and baselines).

    A policy is a pure decision function: `on_event` maps (event, read-only state)
    to a list of actions. Any queue the policy needs it maintains internally.
    Policy-internal randomness must draw from a policy-local seeded stream so runs
    are exactly reproducible (brief Section 4).
    """

    name: str = "policy"

    # The KV rate is the one physics constant a policy needs, and only to avoid
    # proposing placements the engine would reject for lack of HBM. The engine
    # remains the authority: it re-checks capacity on every Schedule. Attached by
    # the orchestration layer so no policy hard-codes a constant.
    _kv_mib_per_token: float = 0.0

    def attach_kv_rate(self, kv_mib_per_token: float) -> None:
        self._kv_mib_per_token = float(kv_mib_per_token)

    def kv_gib(self, inv: Invocation) -> float:
        """HBM the engine will reserve for this invocation's KV, in GiB."""
        return inv.max_context_tokens * self._kv_mib_per_token / 1024.0

    @abstractmethod
    def on_event(self, event: Event, state: ClusterStateView) -> list[Action]:
        """Return actions in response to an event. Never mutate `state`."""
        raise NotImplementedError
