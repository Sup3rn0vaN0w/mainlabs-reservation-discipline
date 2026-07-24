"""CONCUR-style AIMD admission with pause/resume (spec Section 4, baseline 3).

Congestion control, borrowed from TCP and pointed at a serving cluster. The policy
maintains a concurrency window W:

  * ADDITIVE INCREASE  -- every flow that completes is evidence the cluster can take
    more, so W grows by a fixed step.
  * MULTIPLICATIVE DECREASE -- when the backlog crosses a congestion threshold, W is
    cut by a fixed factor. Back off fast, recover slowly.

Admission is gated on W: a flow arriving when the cluster already has W flows in
flight is REJECTED outright rather than queued behind everyone else. That is the
real distinction from the other baselines -- CONCUR keeps the system inside its
operating envelope by refusing work, not by degrading it.

PAUSE / RESUME. When concurrency overshoots W, CONCUR does not throw work away: it
PAUSES the excess and resumes it later. A pause preserves the invocation's state --
its KV goes to host memory and comes back -- so the engine prices it as a SWAP
preemption, never a recompute. Pausing is state-preserving suspension, not
cancellation, and it is not the same thing as a non-preemption guarantee: paused
work stops making progress at the scheduler's discretion, which is precisely what a
reservation forbids.

Deterministic: AIMD is a pure function of the signals, and ties break on a total
order. No RNG.

House style: hyphens only (D-026).
"""

from __future__ import annotations

from ...core.types import Flow, Invocation, PreemptMode
from ..interface import (
    Action,
    Admit,
    ClusterStateView,
    Event,
    FlowArrival,
    InvocationComplete,
    InvocationReady,
    NoOp,
    Policy,
    Preempt,
    Reject,
    Schedule,
    Tick,
)


class ConcurStylePolicy(Policy):
    """AIMD concurrency window + admission gate + state-preserving pause/resume."""

    name = "concur_style"

    def __init__(self, initial_window_fraction: float, additive_increase: float,
                 multiplicative_decrease: float,
                 congestion_queue_threshold: float) -> None:
        self.initial_window_fraction = float(initial_window_fraction)
        self.additive_increase = float(additive_increase)
        self.multiplicative_decrease = float(multiplicative_decrease)
        self.congestion_queue_threshold = float(congestion_queue_threshold)

        # A pause must not destroy work, so it is always a swap, never a recompute.
        self.preempt_mode = PreemptMode.SWAP

        self._window: float | None = None    # set on first event, from cluster size
        self._in_flight: int = 0             # admitted flows not yet finished
        self._queue: list[Invocation] = []
        self._running: dict[int, tuple[Invocation, int]] = {}
        self._flows: dict[int, Flow] = {}

    @classmethod
    def from_config(cls, cfg: dict) -> "ConcurStylePolicy":
        return cls(
            initial_window_fraction=cfg["initial_window_fraction"],
            additive_increase=cfg["additive_increase"],
            multiplicative_decrease=cfg["multiplicative_decrease"],
            congestion_queue_threshold=cfg["congestion_queue_threshold"],
        )

    # --- the AIMD window ---------------------------------------------------------------

    def _total_slots(self, state: ClusterStateView) -> int:
        return sum(d.max_concurrent_decodes for d in state.devices)

    def _ensure_window(self, state: ClusterStateView) -> None:
        if self._window is None:
            self._window = max(
                1.0, self.initial_window_fraction * self._total_slots(state))

    def _clamp_window(self, state: ClusterStateView) -> None:
        self._window = min(max(self._window, 1.0), float(self._total_slots(state)))

    def _increase(self, state: ClusterStateView) -> None:
        """Additive increase: a completion is evidence there is room for more."""
        self._window += self.additive_increase
        self._clamp_window(state)

    def _maybe_decrease(self, state: ClusterStateView) -> None:
        """Multiplicative decrease: back off hard when the backlog says congested."""
        threshold = self.congestion_queue_threshold * self._total_slots(state)
        if len(self._queue) > threshold:
            self._window *= self.multiplicative_decrease
            self._clamp_window(state)

    # --- event handling -------------------------------------------------------------------

    def on_event(self, event: Event, state: ClusterStateView) -> list[Action]:
        self._ensure_window(state)

        if isinstance(event, FlowArrival):
            # The admission gate: outside the window, the work is refused, not queued.
            self._flows[event.flow.flow_id] = event.flow
            if self._in_flight >= self._window:
                return [Reject(event.flow)]
            self._in_flight += 1
            return [Admit(event.flow)]

        if isinstance(event, InvocationReady):
            self._queue.append(event.invocation)
            self._maybe_decrease(state)
            return self._reconcile(state)

        if isinstance(event, InvocationComplete):
            self._running.pop(id(event.invocation), None)
            # Additive increase, but only when a whole FLOW leaves the system -- a
            # single step finishing is not evidence the cluster has spare capacity.
            flow = self._flows.get(event.invocation.flow_id)
            if flow is not None and (flow.is_complete or flow.abandoned):
                self._in_flight = max(0, self._in_flight - 1)
                self._increase(state)
            return self._reconcile(state)

        if isinstance(event, Tick):
            self._maybe_decrease(state)
            return self._reconcile(state)

        return [NoOp()]

    # --- scheduling ---------------------------------------------------------------------

    def _reconcile(self, state: ClusterStateView) -> list[Action]:
        """Hold concurrency at the window: pause the excess, admit up to the limit."""
        actions: list[Action] = []
        free_slots = {d.device_id: d.free_slots for d in state.devices}
        free_hbm = {d.device_id: d.hbm_free_gib for d in state.devices}

        # Overshoot -> pause the newest work (state preserved), oldest keeps running.
        excess = len(self._running) - int(self._window)
        if excess > 0:
            victims = sorted(
                (inv for inv, _ in self._running.values()),
                key=lambda v: (-v.flow_id, -v.index),   # newest first, deterministic
            )[:excess]
            for v in victims:
                actions.append(Preempt(v, self.preempt_mode))
                _, dev_id = self._running.pop(id(v))
                free_slots[dev_id] += 1
                free_hbm[dev_id] += self.kv_gib(v)

        # Fill up to the window with queued work, oldest first.
        self._queue.sort(key=lambda i: (i.flow_id, i.index))
        still_queued: list[Invocation] = []
        for inv in self._queue:
            if len(self._running) >= int(self._window):
                still_queued.append(inv)
                continue
            need = self.kv_gib(inv)
            placed = False
            for dev_id in sorted(free_slots):
                if free_slots[dev_id] > 0 and free_hbm[dev_id] + 1e-9 >= need:
                    actions.append(Schedule(inv, dev_id))
                    free_slots[dev_id] -= 1
                    free_hbm[dev_id] -= need
                    self._running[id(inv)] = (inv, dev_id)
                    placed = True
                    break
            if not placed:
                still_queued.append(inv)
        self._queue = still_queued

        return actions if actions else [NoOp()]
