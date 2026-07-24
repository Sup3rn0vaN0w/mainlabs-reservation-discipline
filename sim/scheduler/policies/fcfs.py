"""FCFS floor policy (spec Section 4 baseline #5: no-control continuous batching).

The simplest possible policy and the SG1 floor: admit every flow, schedule ready
invocations first-come-first-served onto the first device with a free slot and
enough HBM, never preempt, never degrade.

This is the strongest simple baseline for stranding (it never reserves capacity) and
the correctness floor for the engine's M/M/1 sanity checks.

House style: hyphens only (D-026).
"""

from __future__ import annotations

from ...core.types import Invocation
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
    Schedule,
    Tick,
)
from ..queueing import PolicyQueue


class FCFSPolicy(Policy):
    """First-come-first-served, admit-all, non-preemptive continuous batching."""

    name = "fcfs"

    def __init__(self) -> None:
        # Ready invocations awaiting a device slot, in arrival order.
        self._queue = PolicyQueue(lambda inv: (inv.flow_id, inv.index))

    def on_event(self, event: Event, state: ClusterStateView) -> list[Action]:
        if isinstance(event, FlowArrival):
            # Admit everything; the flow's first invocation will arrive as a
            # separate InvocationReady event once the engine advances it.
            return [Admit(event.flow)]

        if isinstance(event, InvocationReady):
            self._queue.push(event.invocation)
            return self._drain(state)

        if isinstance(event, (InvocationComplete, Tick)):
            # A slot may have freed up (or this is a periodic poke); try to place
            # queued work.
            return self._drain(state)

        return [NoOp()]

    def _drain(self, state: ClusterStateView) -> list[Action]:
        """Place as many queued invocations as free device capacity allows.

        Uses only the read-only view to decide placement, and mirrors the capacity
        bookkeeping locally so multiple placements in one drain do not oversubscribe
        a device before the engine applies them.
        """
        actions: list[Action] = []
        free_slots = {d.device_id: d.free_slots for d in state.devices}
        free_hbm = {d.device_id: d.hbm_free_gib for d in state.devices}

        def try_place(inv: Invocation) -> bool:
            need = self.kv_gib(inv)
            for dev_id in sorted(free_slots):
                if free_slots[dev_id] > 0 and free_hbm[dev_id] + 1e-9 >= need:
                    actions.append(Schedule(inv, dev_id))
                    free_slots[dev_id] -= 1
                    free_hbm[dev_id] -= need
                    return True
            return False

        def has_capacity() -> bool:
            return any(v > 0 for v in free_slots.values())

        self._queue.drain(try_place, has_capacity)
        return actions if actions else [NoOp()]
