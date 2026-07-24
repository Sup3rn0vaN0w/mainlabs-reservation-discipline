"""vLLM-style priority scheduling with preemption (spec Section 4, baseline 1).

Model of a vLLM-class serving scheduler: continuous batching, admit-all, strict
class priority, and preemption of lower-priority work under capacity pressure.
When a higher-priority invocation is ready and no device slot is free, the policy
evicts a strictly-lower-priority resident invocation to make room. The evicted
invocation is re-offered by the engine and re-queued here.

Preemption cost is NOT modeled here -- the engine owns it (brief Section 4). The
policy only chooses the mode, which is the spec's two variants:
  * RECOMPUTE - the evicted KV is discarded and re-prefilled on resume
  * SWAP      - the evicted KV is copied to host memory and back

Preemption cannot cycle: only a strictly-higher-priority invocation may preempt,
so a victim can never preempt its own preemptor.

Tie-breaks are a deterministic total order (priority, flow_id, step), so no
policy-local RNG is needed and runs are exactly reproducible (brief Section 4).

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

_DEFAULT_PRIORITY = 99  # unknown classes sort last (lowest priority)


class VLLMStylePolicy(Policy):
    """Priority + preemption with continuous batching."""

    name = "vllm_style"

    def __init__(self, preempt_mode: PreemptMode, class_priority: dict[str, int],
                 max_batch_fraction: float = 1.0) -> None:
        self.preempt_mode = preempt_mode
        self.class_priority = dict(class_priority)
        # vLLM's max_num_seqs, as a fraction of the device's decode capacity. Running
        # a device flat out maximizes batch throughput but deepens contention; the
        # right setting is workload-dependent, which is why it is tuned (SG5).
        self.max_batch_fraction = float(max_batch_fraction)

        self._running: dict[int, tuple[Invocation, int]] = {}  # id(inv) -> (inv, dev)
        self._flow_class: dict[int, str] = {}               # flow_id -> class
        self._queue = PolicyQueue(self._order_key)          # ready, unplaced

    @classmethod
    def from_config(cls, cfg: dict) -> "VLLMStylePolicy":
        return cls(
            preempt_mode=PreemptMode(cfg["preempt_mode"]),
            class_priority=cfg["class_priority"],
            max_batch_fraction=cfg.get("max_batch_fraction", 1.0),
        )

    # --- priority helpers --------------------------------------------------------

    def _priority(self, inv: Invocation) -> int:
        cls = self._flow_class.get(inv.flow_id, "")
        return self.class_priority.get(cls, _DEFAULT_PRIORITY)

    def _order_key(self, inv: Invocation) -> tuple[int, int, int]:
        """Deterministic total order: priority first, then FIFO by flow and step."""
        return (self._priority(inv), inv.flow_id, inv.index)

    # --- event handling ----------------------------------------------------------

    def on_event(self, event: Event, state: ClusterStateView) -> list[Action]:
        if isinstance(event, FlowArrival):
            self._flow_class[event.flow.flow_id] = event.flow.cls
            return [Admit(event.flow)]

        if isinstance(event, InvocationReady):
            self._queue.push(event.invocation)
            return self._schedule_pass(state)

        if isinstance(event, InvocationComplete):
            self._running.pop(id(event.invocation), None)
            return self._schedule_pass(state)

        if isinstance(event, Tick):
            return self._schedule_pass(state)

        return [NoOp()]

    # --- scheduling --------------------------------------------------------------

    def _schedule_pass(self, state: ClusterStateView) -> list[Action]:
        """Place queued work by priority, preempting lower-priority work if needed."""
        actions: list[Action] = []
        # Mirror capacity locally so several placements in one pass account for each
        # other before the engine applies them.
        free_slots = {d.device_id: d.free_slots for d in state.devices}
        free_hbm = {d.device_id: d.hbm_free_gib for d in state.devices}
        # How many more sequences this device may batch, under max_batch_fraction.
        batch_room = {
            d.device_id: int(self.max_batch_fraction * d.max_concurrent_decodes)
                         - d.slots_used
            for d in state.devices
        }

        def try_place(inv: Invocation) -> bool:
            dev_id = self._find_device(inv, free_slots, free_hbm, batch_room)

            if dev_id is None:
                # No room. Evict strictly-lower-priority work to make some.
                victim = self._pick_victim(inv)
                if victim is None:
                    return False
                actions.append(Preempt(victim, self.preempt_mode))
                _, vdev = self._running.pop(id(victim))
                free_slots[vdev] += 1
                free_hbm[vdev] += self.kv_gib(victim)
                batch_room[vdev] += 1
                dev_id = self._find_device(inv, free_slots, free_hbm, batch_room)
                if dev_id is None:
                    return False

            actions.append(Schedule(inv, dev_id))
            free_slots[dev_id] -= 1
            free_hbm[dev_id] -= self.kv_gib(inv)
            batch_room[dev_id] -= 1
            self._running[id(inv)] = (inv, dev_id)
            return True

        def has_capacity() -> bool:
            # Either somewhere to put work, or something worth evicting to make room.
            if any(free_slots[d] > 0 and batch_room[d] > 0 for d in free_slots):
                return True
            head = self._queue.peek()
            return head is not None and self._pick_victim(head) is not None

        self._queue.drain(try_place, has_capacity)
        return actions if actions else [NoOp()]

    def _find_device(self, inv: Invocation, free_slots: dict[int, int],
                     free_hbm: dict[int, float],
                     batch_room: dict[int, int]) -> int | None:
        """First device (by id) with a free slot, batch room, and space for this KV."""
        need = self.kv_gib(inv)
        for dev_id in sorted(free_slots):
            if (free_slots[dev_id] > 0 and batch_room[dev_id] > 0
                    and free_hbm[dev_id] + 1e-9 >= need):
                return dev_id
        return None

    def _pick_victim(self, inv: Invocation) -> Invocation | None:
        """Choose a resident invocation of strictly lower priority to evict.

        vLLM evicts the most recently admitted sequences first (LIFO), which
        preserves the progress of older work. Ties break deterministically.
        """
        my_priority = self._priority(inv)
        candidates = [
            running for running, _ in self._running.values()
            if self._priority(running) > my_priority
        ]
        if not candidates:
            return None
        # Lowest priority first; among those, the newest (largest flow_id, step).
        candidates.sort(key=lambda v: (-self._priority(v), -v.flow_id, -v.index))
        return candidates[0]
