"""The Reservation Discipline -- OUR policy (D-049; the mechanism under provisional).

One mechanism, four coupled parts (spec Section 4):

  1. PER-FLOW RESERVATION over a BOUNDED subset. At most K of the cluster's
     concurrency may be reserved (K swept 1-30 percent, ablation A5). A reserved
     flow gets a pinned KV footprint and a held compute slot for its whole life.
     Everything else is served best-effort out of an aggregate class.

  2. NON-PREEMPTION GUARANTEE. A reserved flow is never evicted. The ENGINE enforces
     this -- this policy could not cheat even if it tried. (Ablation A2 admits with
     the guarantee switched off.)

  3. PINNED KV across tool-time, in two embodiments (ablation A4):
       STRICT -- KV stays in HBM across the gap. No restore latency; HBM stranded.
       TIERED -- KV spills to host across the gap, handing the HBM back to
                 best-effort work, with a GUARANTEED restore. This policy makes the
                 guarantee good by preempting best-effort work to clear room before
                 the reserved flow returns; best-effort work is preemptible, so the
                 restore can never be blocked.
     Both hold the compute slot across the gap. That held-but-idle slot is the
     stranding -- and the primary metric, goodput per PROVISIONED GPU-hour, charges
     us for every second of it. There is no fudge factor to argue about later.

  4. CROSS-INVOCATION TOKEN BUDGET, metered by the engine at decode. Every flow this
     policy admits gets one, reserved or not -- a budget that is only checked
     per-request cannot stop a flow that loops. A flow that exhausts its budget is
     abandoned, which is what contains runaway flows (H3). Ablation A1 replaces the
     cross-invocation budget with a per-request cap, which is what the baselines do.

  Plus HEADROOM-KEYED PROBABILISTIC DEGRADATION: as cluster headroom falls, flows are
  degraded with a probability read off a piecewise-linear curve. Probabilistic, not
  deterministic, so load sheds smoothly instead of cliff-edging a whole class at a
  threshold (ablation A3 swaps the headroom key for a queue-depth key).

Randomness (the degradation draw) comes from a policy-local seeded stream, so runs
are exactly reproducible and our draws never perturb the workload stream that the
baselines see (brief Section 4).

House style: hyphens only (D-026).
"""

from __future__ import annotations

import numpy as np

from ...core.types import (
    Flow,
    Invocation,
    PinMode,
    PreemptMode,
    Reservation,
)
from ..queueing import PolicyQueue
from ..interface import (
    Action,
    Admit,
    ClusterStateView,
    Degrade,
    DeviceView,
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

_UNBOUNDED = float("inf")


class ReservationPolicy(Policy):
    """Reservation discipline: bounded reserved subset + budgets + degradation."""

    name = "reservation"

    def __init__(
        self,
        reserved_subset_fraction: float,
        eligible_classes: list[str],
        max_pin_hbm_fraction: float,
        pin_mode: PinMode,
        non_preemptible: bool,
        budget_enabled: bool,
        budget_tokens_by_class: dict[str, float],
        per_request_cap_tokens: float,
        degradation_key: str,
        degradation_curve: list[tuple[float, float]],
        degradation_action: str,
        besteffort_preempt_mode: PreemptMode,
        seed: int,
    ) -> None:
        self.reserved_subset_fraction = float(reserved_subset_fraction)
        self.eligible_classes = set(eligible_classes)
        self.max_pin_hbm_fraction = float(max_pin_hbm_fraction)
        self.pin_mode = pin_mode
        self.non_preemptible = bool(non_preemptible)
        self.budget_enabled = bool(budget_enabled)
        self.budget_tokens_by_class = dict(budget_tokens_by_class)
        self.per_request_cap_tokens = float(per_request_cap_tokens)
        self.degradation_key = degradation_key
        self.degradation_action = degradation_action
        self.besteffort_preempt_mode = besteffort_preempt_mode

        # Piecewise-linear curve, stored ascending in the key for interpolation.
        pts = sorted(degradation_curve, key=lambda p: p[0])
        self._curve_x = np.array([p[0] for p in pts], dtype=float)
        self._curve_p = np.array([p[1] for p in pts], dtype=float)

        # Policy-local randomness (brief Section 4).
        self._rng = np.random.default_rng(seed)

        # THE LEDGER: flow_id -> Reservation. The engine independently validates that
        # what this ledger commits never over-commits the cluster.
        self._ledger: dict[int, Reservation] = {}

        self._flows: dict[int, Flow] = {}
        self._active: set[int] = set()                          # flows still doing work
        self._running: dict[int, tuple[Invocation, int]] = {}   # best-effort residents
        # Best-effort ready set, FIFO. Same queue every policy gets (see queueing.py).
        self._queue = PolicyQueue(lambda inv: (inv.flow_id, inv.index))

    @classmethod
    def from_config(cls, cfg: dict) -> "ReservationPolicy":
        budget = cfg["budget"]
        deg = cfg["degradation"]
        curve = [(float(p["headroom"]), float(p["probability"])) for p in deg["curve"]]
        return cls(
            reserved_subset_fraction=cfg["reserved_subset_fraction"],
            eligible_classes=cfg["eligible_classes"],
            max_pin_hbm_fraction=cfg["max_pin_hbm_fraction"],
            pin_mode=PinMode(cfg["pin_mode"]),
            non_preemptible=cfg["non_preemptible"],
            budget_enabled=budget["enabled"],
            budget_tokens_by_class=budget["tokens_by_class"],
            per_request_cap_tokens=budget["per_request_cap_tokens"],
            degradation_key=deg["key"],
            degradation_curve=curve,
            degradation_action=deg["action"],
            besteffort_preempt_mode=PreemptMode(cfg["besteffort_preempt_mode"]),
            seed=cfg["seed"],
        )

    # --- the ledger and its admission test ----------------------------------------

    def _reserved_subset_bound(self, state: ClusterStateView) -> int:
        """K: how many concurrent reservations the discipline permits (A5 sweeps it)."""
        total_slots = sum(d.max_concurrent_decodes for d in state.devices)
        return int(self.reserved_subset_fraction * total_slots)

    def _budget_for(self, flow: Flow) -> float | None:
        """The flow's cross-invocation token budget.

        Sized from its CLASS, not from its own actual work -- a budget derived from
        what a flow really intends to draw would hand a runaway flow a runaway
        budget and contain nothing. Ablation A1 (`budget.enabled: false`) drops the
        cross-invocation budget for a per-request cap, the baselines' behavior.
        """
        if not self.budget_enabled:
            return None
        return float(self.budget_tokens_by_class.get(flow.cls, _UNBOUNDED))

    def _find_pin_device(self, flow: Flow, need_gib: float,
                         state: ClusterStateView) -> int | None:
        """A device that can hold this flow's pin for its whole life, or None.

        Mirrors the engine's `can_pin` exactly -- both constraints, or the engine
        raises: the pin must fit PHYSICALLY (free HBM, since best-effort work may be
        occupying memory) AND against COMMITTED HBM (owed to every pin, including
        spilled ones -- the guaranteed-restore invariant).
        """
        for d in state.devices:
            fits_now = d.hbm_free_gib + 1e-9 >= need_gib
            fits_commitment = d.hbm_pinnable_gib + 1e-9 >= need_gib
            if d.free_slots > 0 and fits_now and fits_commitment:
                return d.device_id
        return None

    def _is_reservable(self, flow: Flow, need_gib: float,
                       state: ClusterStateView) -> bool:
        """Whether a flow SHOULD be reserved, before asking whether it CAN be.

        A pin that would swallow most of a device is not a reservation, it is a
        denial of service: it strands that HBM even while the flow sits in tool-time.
        A flow demanding more than `max_pin_hbm_fraction` of a device is almost
        always a runaway (inflated invocation count and token draw), and it belongs
        in the aggregate class where its cross-invocation budget contains it (H3) --
        not holding a GPU hostage under guarantee.
        """
        if flow.cls not in self.eligible_classes:
            return False
        per_device_hbm = max(d.hbm_capacity_gib for d in state.devices)
        if need_gib > self.max_pin_hbm_fraction * per_device_hbm:
            return False
        return len(self._ledger) < self._reserved_subset_bound(state)

    def _admit_flow(self, flow: Flow, state: ClusterStateView) -> list[Action]:
        budget = self._budget_for(flow)
        need_gib = flow.peak_context_tokens * self._kv_mib_per_token / 1024.0

        # Try for a reservation, if the flow both should be and can be reserved.
        if self._is_reservable(flow, need_gib, state):
            device_id = self._find_pin_device(flow, need_gib, state)
            if device_id is not None:
                res = Reservation(
                    flow_id=flow.flow_id,
                    hbm_gib=need_gib,
                    token_budget=budget if budget is not None else _UNBOUNDED,
                    pin_mode=self.pin_mode,
                    non_preemptible=self.non_preemptible,
                    device_id=device_id,
                )
                self._ledger[flow.flow_id] = res
                return [Admit(flow, cls="reserved", reservation=res)]

        # Otherwise the aggregate class: metered, but not pinned and not guaranteed.
        return [Admit(flow, cls="aggregate", budget_tokens=budget)]

    # --- degradation ---------------------------------------------------------------

    def _pressure_key(self, state: ClusterStateView) -> float:
        """The signal degradation is keyed on. A3 swaps headroom for queue depth."""
        if self.degradation_key == "queue_depth":
            total_slots = sum(d.max_concurrent_decodes for d in state.devices)
            if total_slots <= 0:
                return 0.0
            # Expressed as a headroom-like quantity so one curve serves both keys.
            return max(0.0, 1.0 - len(self._queue) / total_slots)
        return state.headroom

    def _degrade_probability(self, state: ClusterStateView) -> float:
        return float(np.interp(self._pressure_key(state),
                               self._curve_x, self._curve_p))

    def _maybe_degrade(self, state: ClusterStateView) -> list[Action]:
        """Shed load probabilistically as headroom falls (spec Section 4)."""
        p = self._degrade_probability(state)
        if p <= 0.0:
            return []
        actions: list[Action] = []
        for flow in self._active_flows():
            if flow.degradations > 0:      # degrade a flow at most once
                continue
            if self._rng.random() < p:
                actions.append(Degrade(flow, self.degradation_action))
        return actions

    def _active_flows(self) -> list[Flow]:
        """Admitted flows still doing work, in deterministic order.

        Walks only the live set, not every flow the run has ever seen -- otherwise
        this is O(n) per tick and O(n^2) over a run.
        """
        live: list[Flow] = []
        for flow_id in sorted(self._active):
            flow = self._flows[flow_id]
            if flow.is_complete or flow.abandoned:
                self._active.discard(flow_id)
                continue
            if flow.admitted:
                live.append(flow)
        return live

    # --- event handling -------------------------------------------------------------

    def on_event(self, event: Event, state: ClusterStateView) -> list[Action]:
        if isinstance(event, FlowArrival):
            self._flows[event.flow.flow_id] = event.flow
            self._active.add(event.flow.flow_id)
            return self._admit_flow(event.flow, state)

        if isinstance(event, InvocationReady):
            flow = self._flows[event.invocation.flow_id]
            if flow.is_reserved:
                # The guarantee: it runs now, in the capacity its pin already holds.
                return self._schedule_reserved(flow, event.invocation, state)
            self._queue.push(event.invocation)
            return self._drain_besteffort(state)

        if isinstance(event, InvocationComplete):
            inv = event.invocation
            self._running.pop(id(inv), None)
            flow = self._flows.get(inv.flow_id)
            if flow is not None and (flow.is_complete or flow.abandoned):
                self._ledger.pop(flow.flow_id, None)   # engine has released the pin
                self._active.discard(flow.flow_id)
            return self._drain_besteffort(state)

        if isinstance(event, Tick):
            actions = self._maybe_degrade(state)
            actions.extend(self._drain_besteffort(state))
            return actions or [NoOp()]

        return [NoOp()]

    # --- scheduling ------------------------------------------------------------------

    def _schedule_reserved(self, flow: Flow, inv: Invocation,
                           state: ClusterStateView) -> list[Action]:
        """Place a reserved invocation into its own pin. This can never fail."""
        res = self._ledger[flow.flow_id]
        dev = state.device(res.device_id)
        actions: list[Action] = []

        # TIERED: the pin's KV is on the host. Make the guaranteed restore good by
        # clearing best-effort work off the device until the KV fits again.
        if flow.flow_id in dev.spilled_pins:
            actions.extend(self._make_room_for_restore(res, dev))

        actions.append(Schedule(inv, res.device_id))
        return actions

    def _make_room_for_restore(self, res: Reservation,
                               dev: DeviceView) -> list[Action]:
        """Evict best-effort work until a spilled pin can come home."""
        free = dev.hbm_free_gib
        if free + 1e-9 >= res.hbm_gib:
            return []

        actions: list[Action] = []
        victims = [inv for inv, dev_id in self._running.values()
                   if dev_id == res.device_id]
        # Biggest KV first (fewest evictions), deterministic ties.
        victims.sort(key=lambda v: (-self.kv_gib(v), v.flow_id, v.index))
        for v in victims:
            actions.append(Preempt(v, self.besteffort_preempt_mode))
            self._running.pop(id(v))
            free += self.kv_gib(v)
            if free + 1e-9 >= res.hbm_gib:
                break
        return actions

    def _drain_besteffort(self, state: ClusterStateView) -> list[Action]:
        """Fill whatever capacity the reservations left over, FIFO."""
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
                    self._running[id(inv)] = (inv, dev_id)
                    return True
            return False

        def has_capacity() -> bool:
            return any(v > 0 for v in free_slots.values())

        self._queue.drain(try_place, has_capacity)
        return actions
