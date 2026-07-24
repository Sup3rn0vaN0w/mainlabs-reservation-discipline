"""FastServe-style MLFQ preemptive scheduling (spec Section 4, baseline 4).

Multi-level feedback queue. Every invocation enters at level 0. Each time it
consumes its current level's token quantum it is demoted one level. The scheduler
always prefers lower levels, so short invocations finish at high priority while
long ones sink -- approximating shortest-remaining-first without knowing job size
in advance, which is the point of MLFQ (a serving scheduler cannot know an
invocation's output length up front).

Preemptive: when outranking work is waiting and no slot is free, the lowest-ranked
resident invocation is evicted. FastServe pairs MLFQ with proactive KV swapping to
keep that eviction cheap, so SWAP is the default mode; the engine owns the cost.

Demotion is driven by accumulated decode service, which changes continuously as
tokens are generated. The policy therefore needs a periodic scheduling opportunity:
it re-evaluates levels on every Tick (interval set in configs/policies.yaml).

A preempted invocation KEEPS its level -- it is not promoted back to level 0 by
being evicted, which would let long invocations starve short ones by cycling.

Tie-breaks are a deterministic total order (level, flow_id, step), so no
policy-local RNG is needed and runs are exactly reproducible (brief Section 4).

House style: hyphens only (D-026).
"""

from __future__ import annotations

import heapq

from ...core.types import Invocation, PreemptMode
from ..queueing import DEFAULT_SCAN_BUDGET as _SCAN_BUDGET
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


class FastServeMLFQPolicy(Policy):
    """Preemptive multi-level feedback queue with token quanta."""

    name = "fastserve_mlfq"

    def __init__(self, num_levels: int, quantum_tokens: list[float],
                 preempt_mode: PreemptMode, quantum_scale: float = 1.0) -> None:
        if len(quantum_tokens) < num_levels:
            raise ValueError(
                f"need a quantum per level: {num_levels} levels, "
                f"{len(quantum_tokens)} quanta")
        self.num_levels = int(num_levels)
        # quantum_scale stretches or compresses the whole quantum ladder. Short
        # quanta demote aggressively (good separation, more preemption); long quanta
        # demote lazily (less churn, worse separation). Workload-dependent -> tuned.
        self.quantum_scale = float(quantum_scale)
        self.quantum_tokens = [float(q) * self.quantum_scale for q in quantum_tokens]
        self.preempt_mode = preempt_mode

        self._queue: list[Invocation] = []                     # ready, unplaced
        self._running: dict[int, tuple[Invocation, int]] = {}  # id(inv) -> (inv, dev)
        self._level: dict[int, int] = {}                       # id(inv) -> level
        self._level_base: dict[int, float] = {}                # generated at level entry

    @classmethod
    def from_config(cls, cfg: dict) -> "FastServeMLFQPolicy":
        return cls(
            num_levels=cfg["num_levels"],
            quantum_tokens=cfg["quantum_tokens"],
            preempt_mode=PreemptMode(cfg["preempt_mode"]),
            quantum_scale=cfg.get("quantum_scale", 1.0),
        )

    # --- level bookkeeping -------------------------------------------------------

    def _track(self, inv: Invocation) -> None:
        """Register an invocation at level 0, or leave an existing level intact."""
        if id(inv) not in self._level:
            self._level[id(inv)] = 0
            self._level_base[id(inv)] = inv.generated_tokens

    def _forget(self, inv: Invocation) -> None:
        self._level.pop(id(inv), None)
        self._level_base.pop(id(inv), None)

    def _demote_as_earned(self) -> None:
        """Demote any invocation that has consumed its current level's quantum."""
        for inv in self._managed():
            level = self._level[id(inv)]
            while level < self.num_levels - 1:
                served_at_level = inv.generated_tokens - self._level_base[id(inv)]
                if served_at_level < self.quantum_tokens[level]:
                    break
                level += 1
                self._level[id(inv)] = level
                self._level_base[id(inv)] = inv.generated_tokens

    def _managed(self) -> list[Invocation]:
        return [inv for inv, _ in self._running.values()] + list(self._queue)

    def _order_key(self, inv: Invocation) -> tuple[int, int, int]:
        """Deterministic total order: level first, then FIFO by flow and step."""
        return (self._level.get(id(inv), 0), inv.flow_id, inv.index)

    # --- event handling ----------------------------------------------------------

    def on_event(self, event: Event, state: ClusterStateView) -> list[Action]:
        if isinstance(event, FlowArrival):
            return [Admit(event.flow)]

        if isinstance(event, InvocationReady):
            # A re-offered (preempted) invocation keeps its level; a new one starts
            # at level 0.
            self._track(event.invocation)
            self._queue.append(event.invocation)
            return self._fill(state)

        if isinstance(event, InvocationComplete):
            self._running.pop(id(event.invocation), None)
            self._forget(event.invocation)
            return self._fill(state)

        if isinstance(event, Tick):
            # Levels only change as service accrues, so the full priority
            # reconciliation (which may evict outranked residents) belongs on the
            # tick. Doing it on every event would re-rank the entire backlog
            # thousands of times a second for no benefit -- and under overload that
            # backlog is unbounded, which makes it quadratic.
            self._demote_as_earned()
            return self._reconcile(state)

        return [NoOp()]

    def _fill(self, state: ClusterStateView) -> list[Action]:
        """Cheap path: put the highest-ranked queued work into any free capacity.

        No eviction decisions here -- those need up-to-date levels, so they wait for
        the tick. Costs nothing when the cluster is full.
        """
        free_slots = {d.device_id: d.free_slots for d in state.devices}
        if not any(v > 0 for v in free_slots.values()) or not self._queue:
            return [NoOp()]

        free_hbm = {d.device_id: d.hbm_free_gib for d in state.devices}
        actions: list[Action] = []
        examined = 0

        # Only the top of the queue can be placed; ranking the whole backlog
        # every event is what made this quadratic under overload.
        candidates = heapq.nsmallest(_SCAN_BUDGET, self._queue, key=self._order_key)
        chosen = set()
        for inv in candidates:
            if not any(v > 0 for v in free_slots.values()):
                break
            examined += 1
            dev_id = self._find_device(inv, free_slots, free_hbm)
            if dev_id is None:
                continue
            actions.append(Schedule(inv, dev_id))
            free_slots[dev_id] -= 1
            free_hbm[dev_id] -= self.kv_gib(inv)
            self._running[id(inv)] = (inv, dev_id)
            chosen.add(id(inv))
        if chosen:
            self._queue = [i for i in self._queue if id(i) not in chosen]
        return actions if actions else [NoOp()]

    # --- scheduling --------------------------------------------------------------

    def _reconcile(self, state: ClusterStateView) -> list[Action]:
        """Make the resident set the highest-ranked set the cluster can hold."""
        actions: list[Action] = []
        free_slots = {d.device_id: d.free_slots for d in state.devices}
        free_hbm = {d.device_id: d.hbm_free_gib for d in state.devices}

        total_slots = sum(d.max_concurrent_decodes for d in state.devices)
        # Only the top `total_slots` matter for the keep-set; ranking the whole
        # (unbounded) backlog every tick is O(n log n) for no benefit.
        ranked = heapq.nsmallest(total_slots, self._managed(), key=self._order_key)
        keep = {id(inv) for inv in ranked}

        # Evict residents that the cluster's top-ranked set no longer includes.
        for inv, dev_id in list(self._running.values()):
            if id(inv) in keep:
                continue
            actions.append(Preempt(inv, self.preempt_mode))
            self._running.pop(id(inv))
            free_slots[dev_id] += 1
            free_hbm[dev_id] += self.kv_gib(inv)
            # It returns to the queue when the engine re-offers it, keeping its level.

        # Fill free capacity with the highest-ranked queued work. Only the top of the
        # queue can be placed, so rank only that -- ranking the whole (unbounded)
        # backlog is what made this quadratic under overload. Anything not placed
        # STAYS queued; the backlog is never silently dropped.
        chosen: set[int] = set()
        for inv in heapq.nsmallest(_SCAN_BUDGET, self._queue, key=self._order_key):
            if not any(v > 0 for v in free_slots.values()):
                break
            dev_id = self._find_device(inv, free_slots, free_hbm)
            if dev_id is None:
                continue
            actions.append(Schedule(inv, dev_id))
            free_slots[dev_id] -= 1
            free_hbm[dev_id] -= self.kv_gib(inv)
            self._running[id(inv)] = (inv, dev_id)
            chosen.add(id(inv))
        if chosen:
            self._queue = [i for i in self._queue if id(i) not in chosen]

        return actions if actions else [NoOp()]

    def _find_device(self, inv: Invocation, free_slots: dict[int, int],
                     free_hbm: dict[int, float]) -> int | None:
        need = self.kv_gib(inv)
        for dev_id in sorted(free_slots):
            if free_slots[dev_id] > 0 and free_hbm[dev_id] + 1e-9 >= need:
                return dev_id
        return None
