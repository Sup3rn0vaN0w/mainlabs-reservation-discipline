"""MARS-style serving policy (spec Section 4 baseline; EVAL_SPEC Amendment 1).

Model of MARS (arXiv 2604.26963): three mechanisms composed.

  1. AIMD ADMISSION WINDOW (as in CONCUR). A concurrency window controls how many
     flows are in flight; arrivals outside it are refused, not queued. Additive
     increase on each completed flow, multiplicative decrease when the backlog
     signals congestion. This also bounds the ready queue, so MARS does not suffer
     the unbounded-backlog cost a pure MLFQ has under overload.

  2. MLFQ PRIORITY (as in FastServe). Admitted work runs in a multi-level feedback
     queue: every invocation enters at level 0 and demotes as it consumes each
     level's token quantum, so short work finishes at high priority without knowing
     job size up front. The scheduler prefers low levels and evicts outranked
     residents.

  3. OPPORTUNISTIC COST-BENEFIT KV RETENTION (the MARS-specific part). When it must
     evict, MARS chooses the cheaper resume path per victim: retain the KV by
     SWAPPING it to host when the materialized context is large (re-prefilling it
     would waste a lot of compute), and discard-and-RECOMPUTE when the context is
     small (a host round-trip would cost more than just redoing the prefill). The
     crossover is a tuned context-size threshold, so the policy stays free of the
     engine's physics constants -- it reads only the workload attribute
     `materialized_context_tokens` and compares it to the threshold.

Deterministic: AIMD and MLFQ are pure functions of the signals, and the retention
choice is a threshold on a workload attribute. No policy-local RNG.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import heapq

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
from ..queueing import DEFAULT_SCAN_BUDGET as _SCAN_BUDGET


class MarsStylePolicy(Policy):
    """AIMD admission + MLFQ priority + cost-benefit KV retention."""

    name = "mars_style"

    def __init__(self, num_levels: int, quantum_tokens: list[float],
                 quantum_scale: float, initial_window_fraction: float,
                 additive_increase: float, multiplicative_decrease: float,
                 congestion_queue_threshold: float,
                 retain_threshold_tokens: float,
                 max_window_fraction: float = 3.0) -> None:
        if len(quantum_tokens) < num_levels:
            raise ValueError(
                f"need a quantum per level: {num_levels} levels, "
                f"{len(quantum_tokens)} quanta")
        self.num_levels = int(num_levels)
        self.quantum_scale = float(quantum_scale)
        self.quantum_tokens = [float(q) * self.quantum_scale for q in quantum_tokens]

        self.initial_window_fraction = float(initial_window_fraction)
        self.additive_increase = float(additive_increase)
        self.multiplicative_decrease = float(multiplicative_decrease)
        self.congestion_queue_threshold = float(congestion_queue_threshold)
        # The window may OVERSUBSCRIBE the decode slots up to this multiple. This is
        # what makes the MLFQ half matter: MARS admits more than can run at once and
        # the multi-level queue arbitrates which admitted work actually decodes. With
        # the window capped at 1.0x slots there is never contention among admitted
        # work and MARS would collapse to plain AIMD admission (CONCUR).
        self.max_window_fraction = float(max_window_fraction)
        # Above this materialized-context size, swapping the KV beats recomputing it.
        self.retain_threshold_tokens = float(retain_threshold_tokens)

        self._window: float | None = None
        self._in_flight = 0
        self._flows: dict[int, Flow] = {}
        self._queue: list[Invocation] = []
        self._running: dict[int, tuple[Invocation, int]] = {}
        self._level: dict[int, int] = {}
        self._level_base: dict[int, float] = {}

    @classmethod
    def from_config(cls, cfg: dict) -> "MarsStylePolicy":
        return cls(
            num_levels=cfg["num_levels"],
            quantum_tokens=cfg["quantum_tokens"],
            quantum_scale=cfg.get("quantum_scale", 1.0),
            initial_window_fraction=cfg["initial_window_fraction"],
            additive_increase=cfg["additive_increase"],
            multiplicative_decrease=cfg["multiplicative_decrease"],
            congestion_queue_threshold=cfg["congestion_queue_threshold"],
            retain_threshold_tokens=cfg["retain_threshold_tokens"],
            max_window_fraction=cfg.get("max_window_fraction", 3.0),
        )

    # --- AIMD window -------------------------------------------------------------

    def _total_slots(self, state: ClusterStateView) -> int:
        return sum(d.max_concurrent_decodes for d in state.devices)

    def _ensure_window(self, state: ClusterStateView) -> None:
        if self._window is None:
            self._window = max(
                1.0, self.initial_window_fraction * self._total_slots(state))

    def _clamp(self, state: ClusterStateView) -> None:
        ceiling = self.max_window_fraction * self._total_slots(state)
        self._window = min(max(self._window, 1.0), ceiling)

    def _increase(self, state: ClusterStateView) -> None:
        self._window += self.additive_increase
        self._clamp(state)

    def _maybe_decrease(self, state: ClusterStateView) -> None:
        threshold = self.congestion_queue_threshold * self._total_slots(state)
        if len(self._queue) > threshold:
            self._window *= self.multiplicative_decrease
            self._clamp(state)

    # --- MLFQ level bookkeeping --------------------------------------------------

    def _track(self, inv: Invocation) -> None:
        if id(inv) not in self._level:
            self._level[id(inv)] = 0
            self._level_base[id(inv)] = inv.generated_tokens

    def _forget(self, inv: Invocation) -> None:
        self._level.pop(id(inv), None)
        self._level_base.pop(id(inv), None)

    def _demote_running(self) -> None:
        """Only running invocations accrue decode service, so only they demote.

        Bounded by the number of residents, so this stays cheap even under overload
        (and the AIMD window already caps how much is admitted)."""
        for inv, _ in self._running.values():
            level = self._level[id(inv)]
            while level < self.num_levels - 1:
                served = inv.generated_tokens - self._level_base[id(inv)]
                if served < self.quantum_tokens[level]:
                    break
                level += 1
                self._level[id(inv)] = level
                self._level_base[id(inv)] = inv.generated_tokens

    def _order_key(self, inv: Invocation) -> tuple[int, int, int]:
        return (self._level.get(id(inv), 0), inv.flow_id, inv.index)

    # --- cost-benefit KV retention ----------------------------------------------

    def _preempt_mode_for(self, inv: Invocation) -> PreemptMode:
        """Swap (retain KV) when the context is large; recompute when it is small."""
        if inv.materialized_context_tokens >= self.retain_threshold_tokens:
            return PreemptMode.SWAP
        return PreemptMode.RECOMPUTE

    # --- event handling ----------------------------------------------------------

    def on_event(self, event: Event, state: ClusterStateView) -> list[Action]:
        self._ensure_window(state)

        if isinstance(event, FlowArrival):
            self._flows[event.flow.flow_id] = event.flow
            if self._in_flight >= self._window:
                return [Reject(event.flow)]
            self._in_flight += 1
            return [Admit(event.flow)]

        if isinstance(event, InvocationReady):
            self._track(event.invocation)
            self._queue.append(event.invocation)
            self._maybe_decrease(state)
            return self._fill(state)

        if isinstance(event, InvocationComplete):
            inv = event.invocation
            self._running.pop(id(inv), None)
            self._forget(inv)
            flow = self._flows.get(inv.flow_id)
            if flow is not None and (flow.is_complete or flow.abandoned):
                self._in_flight = max(0, self._in_flight - 1)
                self._increase(state)
            return self._fill(state)

        if isinstance(event, Tick):
            self._demote_running()
            self._maybe_decrease(state)
            return self._reconcile(state)

        return [NoOp()]

    # --- scheduling --------------------------------------------------------------

    def _find_device(self, inv: Invocation, free_slots: dict[int, int],
                     free_hbm: dict[int, float]) -> int | None:
        need = self.kv_gib(inv)
        for dev_id in sorted(free_slots):
            if free_slots[dev_id] > 0 and free_hbm[dev_id] + 1e-9 >= need:
                return dev_id
        return None

    def _fill(self, state: ClusterStateView) -> list[Action]:
        """Place the highest-ranked queued work into any free capacity (no eviction)."""
        free_slots = {d.device_id: d.free_slots for d in state.devices}
        if not any(v > 0 for v in free_slots.values()) or not self._queue:
            return [NoOp()]
        free_hbm = {d.device_id: d.hbm_free_gib for d in state.devices}
        actions: list[Action] = []
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

    def _reconcile(self, state: ClusterStateView) -> list[Action]:
        """On tick: evict residents outranked by queued work, then fill."""
        actions: list[Action] = []
        free_slots = {d.device_id: d.free_slots for d in state.devices}
        free_hbm = {d.device_id: d.hbm_free_gib for d in state.devices}

        total_slots = self._total_slots(state)
        managed = [inv for inv, _ in self._running.values()] + self._queue
        keep = {id(inv) for inv in heapq.nsmallest(
            total_slots, managed, key=self._order_key)}

        for inv, dev_id in list(self._running.values()):
            if id(inv) in keep:
                continue
            actions.append(Preempt(inv, self._preempt_mode_for(inv)))
            self._running.pop(id(inv))
            free_slots[dev_id] += 1
            free_hbm[dev_id] += self.kv_gib(inv)

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
