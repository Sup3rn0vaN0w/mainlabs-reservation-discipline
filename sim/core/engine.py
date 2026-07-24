"""The discrete-event simulation engine (brief Sections 3-4).

The engine owns ALL physics and mechanics; policies only decide. It:
  * drives arrivals from a workload source,
  * delivers events to the policy and executes the actions it returns,
  * models device service -- prefill as a head-of-service latency, decode as
    processor sharing with batch-size-dependent throughput,
  * models INTER-INVOCATION TOOL-TIME (spec Section 5): after an invocation
    completes, the flow's next invocation becomes ready only after its gap. The
    flow holds no device during the gap -- this is where a reservation policy will
    strand capacity (SG4),
  * models PREEMPTION COST (spec Section 4): evicting an in-flight invocation and
    the price of resuming it, either RECOMPUTE (KV discarded, accumulated context
    re-prefilled -- wasted computation) or SWAP (KV copied to host and back at the
    modeled interconnect bandwidth),
  * accounts capacity (decode batch cap, HBM) and busy time.

Physics are identical for every policy -- no policy-specific mechanics, ever. That
is the fairness guarantee (brief Section 4).

## The decode model, and why it settles

Decode is piecewise-constant processor sharing: within an interval the per-request
rate is fixed by the current decode batch. Any change to the decode set (an
invocation joins from prefill, completes, or is preempted) SETTLES the device
first: elapsed service since `interval_start` is credited to the invocations that
were actually decoding, and only then is the set mutated. This is what makes
mid-interval preemption exact -- a preempted invocation keeps credit for the tokens
it really generated, and no invocation is credited service it did not receive.

## Deferred re-ready

Preempting an invocation makes it a candidate for rescheduling. If the engine
re-offered it to the policy immediately, a policy that preempted A to make room for
B could see A come back and place it into the very slot it just freed, before B was
scheduled. So preempted invocations are queued on `_reready` and re-offered as
InvocationReady only after the current event's whole action list has been executed.

House style: hyphens only (D-026).
"""

from __future__ import annotations

from typing import Iterator

import simpy

from ..cluster.cluster import Cluster
from ..cluster.device import Device
from ..metrics.collectors import MetricsCollector
from ..scheduler.interface import (
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
    Reject,
    Schedule,
    Tick,
)
from .types import (
    DegradeSpec,
    Flow,
    InvPhase,
    Invocation,
    PinMode,
    PreemptMode,
    Reservation,
    RunConfig,
)

# Decode is complete when remaining output tokens fall to (numerically) zero.
_TOKEN_EPS = 1e-6


WorkloadSource = Iterator[tuple[float, Flow]]


class SimulationEngine:
    """Owns the SimPy clock, the cluster physics, and the policy interaction loop."""

    def __init__(
        self,
        cluster: Cluster,
        policy: Policy,
        workload: WorkloadSource,
        metrics: MetricsCollector,
        config: RunConfig,
        degradation_catalog: dict[str, DegradeSpec] | None = None,
    ) -> None:
        self.env = simpy.Environment()
        self.cluster = cluster
        self.policy = policy
        self.workload = workload
        self.metrics = metrics
        self.config = config
        self.degradation_catalog = degradation_catalog or {}
        # Flows registered by id as they arrive, so a completing invocation can
        # look up its owning flow.
        self._flows: dict[int, Flow] = {}
        # Preempted invocations awaiting re-offer to the policy (see module docs).
        self._reready: list[Invocation] = []
        # Reserved flows whose pin is currently OCCUPIED (their invocation is on a
        # device). A pin that is held but not occupied is stranded capacity.
        self._pin_occupied: set[int] = set()

    # --- public entry point -----------------------------------------------------

    def run(self) -> None:
        """Run the simulation to the configured horizon."""
        for dev in self.cluster.devices:
            dev.proc = self.env.process(self._device_loop(dev))
        self.env.process(self._arrival_loop())
        if self.config.tick_interval_s:
            self.env.process(self._tick_loop())
        self.env.run(until=self.config.horizon_s)

    # --- read-only state view ---------------------------------------------------

    def _state_view(self) -> ClusterStateView:
        views = tuple(
            DeviceView(
                device_id=d.device_id,
                batch_size=d.batch_size,
                num_resident=d.num_resident,
                slots_used=d.slots_used,
                pinned_slots=d.pinned_slots,
                max_concurrent_decodes=d.max_concurrent_decodes,
                hbm_used_gib=d.hbm_used_gib,
                hbm_capacity_gib=d.hbm_capacity_gib,
                hbm_committed_gib=d.hbm_committed_gib,
                spilled_pins=frozenset(
                    fid for fid, p in d.pins.items() if p.spilled),
            )
            for d in self.cluster.devices
        )
        return ClusterStateView(now=self.env.now, devices=views)

    # --- policy delivery boundaries ---------------------------------------------
    #
    # `_deliver` is the ONLY place the policy is called. Every external trigger
    # (arrival, tick, gap expiry, device completion) delivers its event, lets the
    # resulting action cascade run, then drains any invocations that preemption
    # made re-schedulable.

    def _deliver(self, event: Event) -> None:
        self._dispatch(self.policy.on_event(event, self._state_view()))

    def _drain_reready(self) -> None:
        """Re-offer preempted invocations to the policy, after the action cascade."""
        while self._reready:
            inv = self._reready.pop(0)
            if inv.phase is not InvPhase.PREEMPTED:
                continue  # already rescheduled or completed
            inv.ready_time = self.env.now
            self._deliver(InvocationReady(inv, self.env.now))

    # --- arrival / tick / gap processes ------------------------------------------

    def _arrival_loop(self):
        for arrival_time, flow in self.workload:
            delay = arrival_time - self.env.now
            if delay > 0:
                yield self.env.timeout(delay)
            self._flows[flow.flow_id] = flow
            self.metrics.on_arrival(self.env.now, value_weight=flow.value_weight,
                                    cls=flow.cls, is_runaway=flow.is_runaway)
            self._deliver(FlowArrival(flow, self.env.now))
            self._drain_reready()

    def _tick_loop(self):
        interval = self.config.tick_interval_s
        while True:
            yield self.env.timeout(interval)
            self._deliver(Tick(self.env.now))
            self._drain_reready()

    def _gap_then_ready(self, flow: Flow, gap_s: float):
        """Inter-invocation tool-time: hold the flow idle, then ready its next step."""
        yield self.env.timeout(gap_s)
        self._make_ready(flow)
        self._drain_reready()

    # --- action dispatch --------------------------------------------------------

    def _dispatch(self, actions: list[Action]) -> None:
        for action in actions:
            if isinstance(action, NoOp):
                continue
            if isinstance(action, Admit):
                self._admit(action.flow, action.reservation, action.budget_tokens)
            elif isinstance(action, Reject):
                self.metrics.on_reject(self.env.now)
            elif isinstance(action, Schedule):
                self._schedule(action.invocation, action.device_id)
            elif isinstance(action, Preempt):
                self._preempt(action.invocation, action.mode)
            elif isinstance(action, Degrade):
                self._degrade(action.flow, action.action)
            else:  # pragma: no cover - exhaustive vocabulary guard
                raise TypeError(f"unknown action: {action!r}")

    def _admit(self, flow: Flow, reservation: Reservation | None = None,
               budget_tokens: float | None = None) -> None:
        flow.admitted = True
        flow.admit_time = self.env.now
        self.metrics.on_admit(self.env.now)
        if reservation is not None:
            self._establish_reservation(flow, reservation)
        elif budget_tokens is not None:
            # Aggregate-class flow, metered but not pinned.
            flow.budget_remaining = float(budget_tokens)
        self._make_ready(flow)

    # --- the reservation ledger (engine-enforced) --------------------------------

    def _establish_reservation(self, flow: Flow, res: Reservation) -> None:
        """Pin the flow's capacity and open its budget.

        The POLICY maintains its ledger and makes the admission decision; the ENGINE
        enforces it. `Device.pin_flow` raises if the pin would over-commit compute or
        HBM, so a policy that over-commits fails loudly rather than silently getting
        capacity that does not exist (brief Section 4).
        """
        if res.device_id is None:
            raise RuntimeError(
                f"reservation for flow {flow.flow_id} was not bound to a device by "
                f"policy '{self.policy.name}'")
        dev = self.cluster.device(res.device_id)
        dev.pin_flow(flow.flow_id, res.hbm_gib)   # raises on over-commit

        flow.reservation = res
        flow.budget_remaining = res.token_budget
        self.metrics.on_pin_change(self.env.now, +1)

    def _release_reservation(self, flow: Flow) -> None:
        """Give the pinned capacity back (flow completed or was abandoned)."""
        res = flow.reservation
        if res is None or res.device_id is None:
            return
        self._set_pin_occupied(flow, False)
        self.cluster.device(res.device_id).unpin_flow(flow.flow_id)
        self.metrics.on_pin_change(self.env.now, -1)

    def _set_pin_occupied(self, flow: Flow, occupied: bool) -> None:
        """Track whether a pin is actually being used (vs held idle = stranded)."""
        if not flow.is_reserved:
            return
        held = flow.flow_id in self._pin_occupied
        if occupied and not held:
            self._pin_occupied.add(flow.flow_id)
            self.metrics.on_pin_active_change(self.env.now, +1)
        elif not occupied and held:
            self._pin_occupied.discard(flow.flow_id)
            self.metrics.on_pin_active_change(self.env.now, -1)

    # --- degradation (headroom-keyed; the policy decides, the engine applies) -----

    def _degrade(self, flow: Flow, action: str) -> None:
        """Apply a degradation action: less work to do, at a cost in served value."""
        spec = self.degradation_catalog.get(action)
        if spec is None:
            raise RuntimeError(
                f"policy '{self.policy.name}' asked for unknown degradation action "
                f"{action!r}; known: {sorted(self.degradation_catalog)}")

        # Only reshape work that has NOT been placed yet. Rewriting a resident
        # invocation's token count would desync the device's HBM accounting, which
        # was taken when it was placed.
        for inv in flow.invocations[flow.cursor:]:
            if inv.phase is not InvPhase.PENDING:
                continue
            scaled = max(1, int(inv.output_tokens * spec.token_scale))
            inv.output_tokens = scaled
            inv.decode_remaining = float(scaled)

        flow.value_weight *= (1.0 - spec.quality_cost)
        flow.degradations += 1
        self.metrics.on_degradation(self.env.now)

    def _make_ready(self, flow: Flow) -> None:
        """Offer the flow's next invocation to the policy, if any."""
        inv = flow.next_invocation()
        if inv is None:
            return
        inv.ready_time = self.env.now
        self._deliver(InvocationReady(inv, self.env.now))

    # --- placement and the cost of entering a device ------------------------------

    def _entry_latency(self, inv: Invocation) -> tuple[float, float]:
        """Latency to bring an invocation onto a device, and tokens re-prefilled.

        Fresh placement pays prefill of this step's new input tokens. A resumed
        placement pays the preemption price the policy chose:
          * RECOMPUTE -- re-prefill the whole materialized context (wasted work),
          * SWAP      -- copy the KV back from host, plus the earlier copy out.
        """
        svc = self.cluster.service
        if inv.resume_mode is None:
            return svc.prefill_time(inv.prefill_tokens), 0.0

        ctx = inv.materialized_context_tokens
        if inv.resume_mode is PreemptMode.RECOMPUTE:
            return svc.prefill_time(ctx), float(ctx)
        # SWAP: the copy out (at preempt) and the copy back (now), both metered at
        # the modeled host-interconnect bandwidth.
        gib = svc.kv_footprint_gib(ctx)
        return 2.0 * svc.spill_time(gib), 0.0

    def _schedule(self, inv: Invocation, device_id: int) -> None:
        """Place an invocation on a device (engine validates capacity)."""
        if inv.phase not in (InvPhase.PENDING, InvPhase.PREEMPTED):
            raise RuntimeError(
                f"policy '{self.policy.name}' scheduled invocation (flow "
                f"{inv.flow_id}, step {inv.index}) that is already {inv.phase.value}")

        flow = self._flow_of(inv)
        dev = self.cluster.device(device_id)
        latency, reprefill_tokens = self._entry_latency(inv)

        if flow.is_reserved:
            res = flow.reservation
            # A reserved invocation runs in the capacity its pin already holds -- it
            # cannot be placed anywhere else, and it never competes for free slots.
            if device_id != res.device_id:
                raise RuntimeError(
                    f"policy '{self.policy.name}' scheduled reserved flow "
                    f"{flow.flow_id} on device {device_id}, but its pin is on "
                    f"device {res.device_id}")
            if res.pin_mode is PinMode.TIERED and dev.pins[flow.flow_id].spilled:
                # Guaranteed restore: bring the KV back from host. Raises if the
                # policy failed to make room -- the guarantee is not optional.
                gib = dev.restore_pin(flow.flow_id)
                latency += self.cluster.service.spill_time(gib)
        else:
            if not dev.can_place(inv):
                raise RuntimeError(
                    f"policy '{self.policy.name}' scheduled invocation "
                    f"(flow {inv.flow_id}) on device {device_id} with no capacity "
                    f"(slots {dev.slots_used}/{dev.max_concurrent_decodes}, "
                    f"hbm_used={dev.hbm_used_gib:.2f}/{dev.hbm_capacity_gib} GiB)")

        if reprefill_tokens > 0:
            self.metrics.on_reprefill(self.env.now, reprefill_tokens)
        inv.resume_mode = None

        if dev.num_resident == 0:
            self.metrics.on_device_busy_start(self.env.now, device_id)
        dev.reserve(inv, reserved=flow.is_reserved)
        self._set_pin_occupied(flow, True)
        inv.device_id = device_id
        inv.phase = InvPhase.PREFILL
        if inv.start_time is None:
            inv.start_time = self.env.now
        self.env.process(self._enter_device(dev, inv, latency))

    def _enter_device(self, dev: Device, inv: Invocation, latency_s: float):
        """Serve the entry latency, then join the device's decode set."""
        if latency_s > 0:
            yield self.env.timeout(latency_s)
        # The policy may have preempted this invocation while it was entering.
        if inv.phase is not InvPhase.PREFILL or inv.device_id != dev.device_id:
            return
        self._settle_device(dev)      # credit current decoders before the set changes
        inv.phase = InvPhase.DECODE
        if inv.decode_start_time is None:
            inv.decode_start_time = self.env.now   # first token begins (drives TTFT)
        dev.decoding.append(inv)
        dev.interval_start = self.env.now
        self._poke_device(dev)

    # --- device service ---------------------------------------------------------

    def _settle_device(self, dev: Device) -> None:
        """Credit elapsed decode service to the invocations that were decoding.

        Must be called before ANY mutation of `dev.decoding`, so that service is
        attributed to exactly the set that received it over the interval.
        """
        now = self.env.now
        elapsed = now - dev.interval_start
        if elapsed > 0 and dev.decoding and dev.interval_rate > 0:
            served = elapsed * dev.interval_rate
            for inv in dev.decoding:
                inv.decode_remaining -= served
                # Cross-invocation token budget is metered AT DECODE: every token
                # generated is a token spent, across all of the flow's invocations.
                flow = self._flow_of(inv)
                if flow.budget_remaining is not None:
                    flow.budget_remaining -= served
            # Total output tokens generated across the batch this interval.
            self.metrics.on_tokens_decoded(now, served * len(dev.decoding))
        dev.interval_start = now

    def _tokens_until_stop(self, inv: Invocation) -> float:
        """Tokens this invocation may still generate before something stops it.

        Either it finishes, or its flow's budget runs out -- whichever comes first.
        The device loop sizes its interval by this, so a flow stops EXACTLY at its
        budget rather than overshooting to the next event boundary.
        """
        stop = inv.decode_remaining
        flow = self._flow_of(inv)
        if flow.budget_remaining is not None:
            stop = min(stop, max(flow.budget_remaining, 0.0))
        return max(stop, 0.0)

    def _device_loop(self, dev: Device):
        """Processor-sharing decode loop for one device."""
        svc = self.cluster.service
        while True:
            if not dev.decoding:
                dev.interval_rate = 0.0
                dev.idle_event = self.env.event()
                try:
                    yield dev.idle_event
                except simpy.Interrupt:
                    pass
                dev.idle_event = None
                dev.interval_start = self.env.now
                continue

            # Open a constant-rate interval over the current decode set. The interval
            # ends at the first thing that stops any decoder: finishing its output,
            # or its flow exhausting its cross-invocation budget.
            dev.interval_rate = svc.decode_rate_per_request(len(dev.decoding))
            dev.interval_start = self.env.now
            dt = min(self._tokens_until_stop(i)
                     for i in dev.decoding) / dev.interval_rate

            try:
                yield self.env.timeout(max(dt, 0.0))
            except simpy.Interrupt:
                pass

            self._settle_device(dev)

            finished, exhausted = [], []
            for inv in list(dev.decoding):
                if inv.phase is not InvPhase.DECODE:
                    continue
                if inv.decode_remaining <= _TOKEN_EPS:
                    finished.append(inv)
                else:
                    flow = self._flow_of(inv)
                    if (flow.budget_remaining is not None
                            and flow.budget_remaining <= _TOKEN_EPS):
                        exhausted.append(inv)

            for inv in finished:
                self._complete_invocation(dev, inv)
            for inv in exhausted:
                self._abandon_flow(dev, inv)
            if finished or exhausted:
                self._drain_reready()

    def _poke_device(self, dev: Device) -> None:
        """Wake or recompute a device's serve loop after its decode set changed.

        No-op if the current process IS that device's serve loop: the loop will
        re-open an interval on its next iteration, so a self-interrupt (illegal in
        SimPy) is neither needed nor allowed.
        """
        if dev.proc is self.env.active_process:
            return
        if dev.idle_event is not None and not dev.idle_event.triggered:
            dev.idle_event.succeed()
        elif dev.proc is not None and dev.proc.is_alive:
            dev.proc.interrupt()

    # --- preemption --------------------------------------------------------------

    def _preempt(self, inv: Invocation, mode: PreemptMode) -> None:
        """Evict an in-flight invocation; price its eventual resume."""
        if inv.phase not in (InvPhase.PREFILL, InvPhase.DECODE) or inv.device_id is None:
            raise RuntimeError(
                f"policy '{self.policy.name}' preempted invocation (flow "
                f"{inv.flow_id}, step {inv.index}) that is not resident "
                f"(phase={inv.phase.value})")

        # THE NON-PREEMPTION GUARANTEE (spec Section 4). A reserved flow cannot be
        # preempted -- that is what its reservation buys. The engine enforces it, so
        # no policy can quietly cheat by taking the guarantee back under pressure.
        # Ablation A2 turns the guarantee off by admitting with non_preemptible=False.
        flow = self._flow_of(inv)
        if flow.is_reserved and flow.reservation.non_preemptible:
            raise RuntimeError(
                f"policy '{self.policy.name}' tried to preempt reserved flow "
                f"{flow.flow_id} -- the non-preemption guarantee forbids it")

        dev = self.cluster.device(inv.device_id)
        if inv.phase is InvPhase.DECODE:
            # Credit the tokens it really generated before evicting it.
            self._settle_device(dev)

        was_resident = dev.num_resident
        dev.release(inv)
        if was_resident == 1 and dev.num_resident == 0:
            self.metrics.on_device_busy_end(self.env.now, dev.device_id)

        inv.phase = InvPhase.PREEMPTED
        inv.device_id = None
        inv.resume_mode = mode
        inv.preemptions += 1
        self._set_pin_occupied(flow, False)
        self.metrics.on_preemption(self.env.now, swapped=(mode is PreemptMode.SWAP))

        dev.interval_start = self.env.now
        self._poke_device(dev)
        self._reready.append(inv)

    # --- completion --------------------------------------------------------------

    def _complete_invocation(self, dev: Device, inv: Invocation) -> None:
        """Finish an invocation: free the device, advance its flow, notify policy.

        A RESERVED flow keeps its pin here: the slot (and, under STRICT pinning, the
        HBM) stay held across the tool-time gap. That held-but-idle capacity is the
        stranding the primary metric prices. A TIERED pin spills its KV to host for
        the duration of the gap, giving the HBM back to best-effort work.
        """
        inv.phase = InvPhase.COMPLETE
        inv.complete_time = self.env.now
        inv.decode_remaining = 0.0

        was_resident = dev.num_resident
        dev.release(inv)
        if was_resident == 1 and dev.num_resident == 0:
            self.metrics.on_device_busy_end(self.env.now, dev.device_id)

        flow = self._flow_of(inv)
        self._set_pin_occupied(flow, False)   # pin held, no longer occupied
        gap_s = inv.gap_after_s
        flow.cursor += 1

        if flow.is_complete:
            flow.complete_time = self.env.now
            if flow.is_reserved:
                self._release_reservation(flow)
            self._record_flow_complete(flow)
        elif gap_s > 0:
            # Tool-time: the flow goes idle. A reservation keeps holding its slot.
            if flow.is_reserved and flow.reservation.pin_mode is PinMode.TIERED:
                # Spill the KV to host for the gap. The copy-out overlaps the idle
                # gap, so only the restore is on the critical path (see docs).
                dev.spill_pin(flow.flow_id)
            self.env.process(self._gap_then_ready(flow, gap_s))
        else:
            self._make_ready(flow)

        self._deliver(InvocationComplete(inv, self.env.now))

    def _abandon_flow(self, dev: Device, inv: Invocation) -> None:
        """Kill a flow that exhausted its cross-invocation token budget.

        This is the containment mechanism for runaway flows (H3): the budget is a
        HARD cap across all of a flow's invocations, so a looping flow cannot keep
        drawing capacity forever at everyone else's expense.
        """
        inv.phase = InvPhase.COMPLETE
        inv.complete_time = self.env.now

        was_resident = dev.num_resident
        dev.release(inv)
        if was_resident == 1 and dev.num_resident == 0:
            self.metrics.on_device_busy_end(self.env.now, dev.device_id)

        flow = self._flow_of(inv)
        self._set_pin_occupied(flow, False)
        flow.abandoned = True
        flow.cursor = len(flow.invocations)   # no further invocations run
        if flow.is_reserved:
            self._release_reservation(flow)
        self.metrics.on_abandonment(self.env.now)

        self._deliver(InvocationComplete(inv, self.env.now))

    def _record_flow_complete(self, flow: Flow) -> None:
        """Assemble a flow's latency/value record and hand it to the metrics."""
        first = flow.invocations[0]
        ttft = (None if first.decode_start_time is None
                else first.decode_start_time - flow.arrival_time)

        # Mean inter-token latency: total decode span over total output tokens.
        decode_span = 0.0
        out_tokens = 0
        for inv in flow.invocations:
            if inv.decode_start_time is not None and inv.complete_time is not None:
                decode_span += inv.complete_time - inv.decode_start_time
            out_tokens += inv.output_tokens
        itl = decode_span / out_tokens if out_tokens > 0 else None

        generated = sum(inv.generated_tokens for inv in flow.invocations)

        self.metrics.on_flow_complete(
            self.env.now,
            sojourn_s=self.env.now - flow.arrival_time,
            value_weight=flow.value_weight,
            cls=flow.cls,
            is_runaway=flow.is_runaway,
            ttft_s=ttft,
            itl_s=itl,
            generated_tokens=generated,
        )

    # --- flow bookkeeping -------------------------------------------------------

    def _flow_of(self, inv: Invocation) -> Flow:
        return self._flows[inv.flow_id]
