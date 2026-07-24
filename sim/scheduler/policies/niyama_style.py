"""Niyama-style SLO classes with deterministic demotion (spec Section 4, baseline 2).

SLO-aware serving: every flow belongs to a class with a latency target. The
scheduler serves by class priority and, within a class, earliest deadline first. As
a flow burns through its SLO budget it is DEMOTED -- deterministically, at a fixed
threshold. A demoted flow drops to a lower class and competes for what is left.

This baseline is the load-bearing contrast for H2 and ablation A3. Its demotion is
a CLIFF: cross the threshold and the whole flow changes class. Ours is a
probabilistic function of headroom, so pressure sheds smoothly instead of moving a
whole class at once. Whether that difference is worth anything is exactly what the
graceful-degradation curve (served value vs offered load) is there to measure --
including by finding that it is not.

Deterministic by construction: no RNG anywhere, so runs are exactly reproducible.

House style: hyphens only (D-026).
"""

from __future__ import annotations

from ...core.types import Flow, Invocation, PreemptMode
from ..queueing import PolicyQueue
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
    Schedule,
    Tick,
)


class NiyamaStylePolicy(Policy):
    """SLO classes, earliest-deadline-first within a class, deterministic demotion."""

    name = "niyama_style"

    def __init__(self, class_slo_s: dict[str, float], class_priority: dict[str, int],
                 demote_after_fraction: float, num_demotions: int,
                 preempt_mode: PreemptMode) -> None:
        self.class_slo_s = dict(class_slo_s)
        self.class_priority = dict(class_priority)
        self.demote_after_fraction = float(demote_after_fraction)
        self.num_demotions = int(num_demotions)
        self.preempt_mode = preempt_mode

        self._running: dict[int, tuple[Invocation, int]] = {}
        self._flows: dict[int, Flow] = {}
        self._demotions: dict[int, int] = {}      # flow_id -> times demoted
        self._active: set[int] = set()            # flow ids still doing work
        self._queue = PolicyQueue(self._order_key)

    @classmethod
    def from_config(cls, cfg: dict) -> "NiyamaStylePolicy":
        return cls(
            class_slo_s=cfg["class_slo_s"],
            class_priority=cfg["class_priority"],
            demote_after_fraction=cfg["demote_after_fraction"],
            num_demotions=cfg["num_demotions"],
            preempt_mode=PreemptMode(cfg["preempt_mode"]),
        )

    # --- SLO bookkeeping ------------------------------------------------------------

    def _base_priority(self, flow: Flow) -> int:
        return self.class_priority.get(flow.cls, len(self.class_priority))

    def _effective_priority(self, flow: Flow) -> int:
        """Base class priority, pushed down once per demotion (lower = better)."""
        return self._base_priority(flow) + self._demotions.get(flow.flow_id, 0)

    def _deadline(self, flow: Flow) -> float:
        return flow.arrival_time + self.class_slo_s.get(flow.cls, float("inf"))

    def _demote_overdue_flows(self, now: float) -> bool:
        """The cliff: once a flow has burned `demote_after_fraction` of its SLO, drop it.

        Deterministic -- same input, same demotion, every time. Walks only the flows
        still doing work, not every flow the run has ever seen. Returns True if any
        priority changed, so the caller knows the queue needs re-keying.
        """
        changed = False
        for flow_id in list(self._active):
            flow = self._flows[flow_id]
            if flow.is_complete or flow.abandoned:
                self._active.discard(flow_id)
                continue
            slo = self.class_slo_s.get(flow.cls)
            if slo is None:
                continue
            demotions = self._demotions.get(flow_id, 0)
            if demotions >= self.num_demotions:
                continue
            elapsed = now - flow.arrival_time
            # Each successive demotion costs another slice of the SLO budget.
            threshold = self.demote_after_fraction * slo * (demotions + 1)
            if elapsed >= threshold:
                self._demotions[flow_id] = demotions + 1
                changed = True
        return changed

    def _order_key(self, inv: Invocation) -> tuple[int, float, int, int]:
        """Class priority, then earliest deadline, then FIFO. Fully deterministic."""
        flow = self._flows[inv.flow_id]
        return (self._effective_priority(flow), self._deadline(flow),
                inv.flow_id, inv.index)

    # --- event handling ---------------------------------------------------------------

    def on_event(self, event: Event, state: ClusterStateView) -> list[Action]:
        if isinstance(event, FlowArrival):
            self._flows[event.flow.flow_id] = event.flow
            self._active.add(event.flow.flow_id)
            return [Admit(event.flow)]

        if isinstance(event, InvocationReady):
            self._queue.push(event.invocation)
            return self._schedule_pass(state)

        if isinstance(event, InvocationComplete):
            self._running.pop(id(event.invocation), None)
            flow = self._flows.get(event.invocation.flow_id)
            if flow is not None and (flow.is_complete or flow.abandoned):
                self._active.discard(flow.flow_id)
            return self._schedule_pass(state)

        if isinstance(event, Tick):
            # Demotion changes priorities, so the queue must be re-keyed -- but only
            # here, on the tick, not on every event.
            if self._demote_overdue_flows(state.now):
                self._queue.reheapify()
            return self._schedule_pass(state)

        return [NoOp()]

    # --- scheduling ---------------------------------------------------------------------

    def _schedule_pass(self, state: ClusterStateView) -> list[Action]:
        actions: list[Action] = []
        free_slots = {d.device_id: d.free_slots for d in state.devices}
        free_hbm = {d.device_id: d.hbm_free_gib for d in state.devices}

        def try_place(inv: Invocation) -> bool:
            dev_id = self._find_device(inv, free_slots, free_hbm)

            if dev_id is None:
                victim = self._pick_victim(inv)
                if victim is None:
                    return False
                actions.append(Preempt(victim, self.preempt_mode))
                _, vdev = self._running.pop(id(victim))
                free_slots[vdev] += 1
                free_hbm[vdev] += self.kv_gib(victim)
                dev_id = self._find_device(inv, free_slots, free_hbm)
                if dev_id is None:
                    return False

            actions.append(Schedule(inv, dev_id))
            free_slots[dev_id] -= 1
            free_hbm[dev_id] -= self.kv_gib(inv)
            self._running[id(inv)] = (inv, dev_id)
            return True

        def has_capacity() -> bool:
            if any(v > 0 for v in free_slots.values()):
                return True
            head = self._queue.peek()
            return head is not None and self._pick_victim(head) is not None

        self._queue.drain(try_place, has_capacity)
        return actions if actions else [NoOp()]

    def _find_device(self, inv: Invocation, free_slots: dict[int, int],
                     free_hbm: dict[int, float]) -> int | None:
        need = self.kv_gib(inv)
        for dev_id in sorted(free_slots):
            if free_slots[dev_id] > 0 and free_hbm[dev_id] + 1e-9 >= need:
                return dev_id
        return None

    def _pick_victim(self, inv: Invocation) -> Invocation | None:
        """Evict a strictly-lower-priority resident so a tighter SLO can be met."""
        my_priority = self._effective_priority(self._flows[inv.flow_id])
        candidates = [
            running for running, _ in self._running.values()
            if self._effective_priority(self._flows[running.flow_id]) > my_priority
        ]
        if not candidates:
            return None
        # Worst-priority first (the most-demoted flow pays), then latest deadline.
        candidates.sort(
            key=lambda v: (-self._effective_priority(self._flows[v.flow_id]),
                           -self._deadline(self._flows[v.flow_id]),
                           -v.flow_id, -v.index))
        return candidates[0]
