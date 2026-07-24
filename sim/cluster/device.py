"""Device model: HBM capacity, decode batch, KV accounting, pins (brief Section 3).

A Device tracks two kinds of occupancy:

  * PINS -- a reserved flow holds one decode slot and its KV footprint on this
    device for the flow's whole lifetime, INCLUDING across inter-invocation
    tool-time when it is doing nothing. That idle-but-held capacity IS the
    stranding the paper is about, and the primary metric (goodput per PROVISIONED
    GPU-hour) prices it. A TIERED pin may spill its KV to host across the gap,
    releasing the HBM (but never the slot) until the flow returns.

  * BEST-EFFORT residents -- ordinary invocations occupying a slot and HBM only
    while they are actually on the device.

Capacity therefore reads:
    slots_used = pins + best-effort residents
    hbm_used   = HBM of unspilled pins + HBM of best-effort residents

A reserved flow's invocation does NOT consume fresh capacity when it is placed --
it moves into the slot and HBM its pin already holds. That is the guarantee.

The device holds no scheduling logic: placement is a policy decision executed by
the engine (brief Section 4).

House style: hyphens only (D-026).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.types import Invocation
from .service_model import ServiceModel


@dataclass
class Pin:
    """A reserved flow's held capacity on one device."""

    flow_id: int
    hbm_gib: float
    spilled: bool = False   # TIERED pin whose KV currently sits in host memory

    @property
    def hbm_resident_gib(self) -> float:
        """HBM actually occupied right now (a spilled pin occupies none)."""
        return 0.0 if self.spilled else self.hbm_gib


class Device:
    """A single accelerator: HBM budget + decode batch + pins + residents."""

    def __init__(self, device_id: int, hbm_capacity_gib: float,
                 max_concurrent_decodes: int, service: ServiceModel) -> None:
        self.device_id = device_id
        self.hbm_capacity_gib = float(hbm_capacity_gib)
        self.max_concurrent_decodes = int(max_concurrent_decodes)
        self._service = service

        # Reserved flows pinned here: flow_id -> Pin.
        self.pins: dict[int, Pin] = {}

        # Resident invocations (prefill or decode phase).
        self.resident: set[int] = set()             # id(inv)
        self.resident_reserved: set[int] = set()    # id(inv) for reserved flows
        self.decoding: list[Invocation] = []
        self.besteffort_hbm_gib: float = 0.0        # HBM of best-effort residents

        # Engine-owned handles: the SimPy serve process and the idle event.
        self.proc = None
        self.idle_event = None

        # Decode-interval accounting (engine-owned; see engine._settle_device).
        self.interval_start: float = 0.0
        self.interval_rate: float = 0.0

    # --- capacity queries --------------------------------------------------------

    @property
    def batch_size(self) -> int:
        """Current decode batch size (drives decode throughput)."""
        return len(self.decoding)

    @property
    def pinned_slots(self) -> int:
        return len(self.pins)

    @property
    def besteffort_residents(self) -> int:
        return len(self.resident) - len(self.resident_reserved)

    @property
    def slots_used(self) -> int:
        """Pins hold their slot even when idle; best-effort work holds only while on."""
        return self.pinned_slots + self.besteffort_residents

    @property
    def num_resident(self) -> int:
        """Invocations physically on the device (drives busy-time accounting)."""
        return len(self.resident)

    @property
    def hbm_used_gib(self) -> float:
        """HBM physically occupied right now. A spilled pin occupies none."""
        pinned = sum(p.hbm_resident_gib for p in self.pins.values())
        return pinned + self.besteffort_hbm_gib

    @property
    def hbm_committed_gib(self) -> float:
        """HBM OWED to pins -- including spilled ones, whose memory must come back.

        This is the difference between memory that is free and memory that is merely
        vacant. When a TIERED pin spills, its HBM becomes physically free and
        best-effort work may borrow it -- best-effort work is preemptible, so it can
        always be evicted to hand the memory back. But that memory is still SPOKEN
        FOR. If a new pin were allowed to claim it, the guarantee would be a lie: new
        pins are not preemptible, so the spilled flow could never get its memory back.

        Pin admission is therefore tested against COMMITTED HBM, not free HBM. This
        is what makes "guaranteed restore" actually guaranteed.
        """
        return sum(p.hbm_gib for p in self.pins.values())

    @property
    def hbm_free_gib(self) -> float:
        return self.hbm_capacity_gib - self.hbm_used_gib

    def has_free_slot(self) -> bool:
        return self.slots_used < self.max_concurrent_decodes

    def hbm_fits(self, gib: float) -> bool:
        return self.hbm_used_gib + gib <= self.hbm_capacity_gib + 1e-9

    def can_place(self, inv: Invocation) -> bool:
        """Capacity test for a BEST-EFFORT invocation.

        Best-effort work may use HBM that a spilled pin has vacated: it is
        preemptible, so it can be evicted when the pin comes home.
        """
        need = self._service.kv_footprint_gib(inv.max_context_tokens)
        return self.has_free_slot() and self.hbm_fits(need)

    # --- pins (reserved flows) ----------------------------------------------------

    def can_pin(self, hbm_gib: float) -> bool:
        """Capacity test for a NEW pin.

        Two conditions, and both matter:
          * it must fit physically right now, and
          * it must fit against HBM already COMMITTED to existing pins -- including
            pins that are currently spilled and owed their memory back.
        """
        physically_fits = self.hbm_fits(hbm_gib)
        commitment_fits = (self.hbm_committed_gib + hbm_gib
                           <= self.hbm_capacity_gib + 1e-9)
        return self.has_free_slot() and physically_fits and commitment_fits

    def pin_flow(self, flow_id: int, hbm_gib: float) -> Pin:
        """Hold a slot and KV footprint for a reserved flow's whole lifetime.

        Raises if it would over-commit the device -- the ledger invariant, enforced
        by the engine, not trusted to the policy (brief Section 4).
        """
        if flow_id in self.pins:
            raise RuntimeError(f"device {self.device_id}: flow {flow_id} already pinned")
        if not self.can_pin(hbm_gib):
            raise RuntimeError(
                f"device {self.device_id}: pin would over-commit "
                f"(slots {self.slots_used}/{self.max_concurrent_decodes}, "
                f"hbm {self.hbm_used_gib:.3f}+{hbm_gib:.3f}/{self.hbm_capacity_gib} GiB)")
        pin = Pin(flow_id=flow_id, hbm_gib=float(hbm_gib))
        self.pins[flow_id] = pin
        return pin

    def unpin_flow(self, flow_id: int) -> None:
        self.pins.pop(flow_id, None)

    def spill_pin(self, flow_id: int) -> float:
        """TIERED pin goes idle: move its KV to host, releasing the HBM."""
        pin = self.pins[flow_id]
        if pin.spilled:
            return 0.0
        pin.spilled = True
        return pin.hbm_gib

    def can_restore_pin(self, flow_id: int) -> bool:
        pin = self.pins[flow_id]
        return (not pin.spilled) or self.hbm_fits(pin.hbm_gib)

    def restore_pin(self, flow_id: int) -> float:
        """TIERED pin returns: bring its KV back into HBM (guaranteed restore)."""
        pin = self.pins[flow_id]
        if not pin.spilled:
            return 0.0
        if not self.hbm_fits(pin.hbm_gib):
            raise RuntimeError(
                f"device {self.device_id}: cannot restore pin for flow {flow_id} "
                f"(needs {pin.hbm_gib:.3f} GiB, free {self.hbm_free_gib:.3f} GiB) -- "
                f"the guaranteed-restore invariant is broken")
        pin.spilled = False
        return pin.hbm_gib

    # --- residency (engine-only) --------------------------------------------------

    def reserve(self, inv: Invocation, reserved: bool) -> None:
        """Put an invocation on the device.

        A reserved invocation moves into the slot and HBM its flow's pin already
        holds, so it consumes no fresh capacity. A best-effort invocation must fit.
        """
        if reserved:
            if inv.flow_id not in self.pins:
                raise RuntimeError(
                    f"device {self.device_id}: reserved invocation for flow "
                    f"{inv.flow_id} has no pin here")
            self.resident.add(id(inv))
            self.resident_reserved.add(id(inv))
            return

        if not self.can_place(inv):
            raise RuntimeError(
                f"device {self.device_id}: cannot place invocation "
                f"(slots {self.slots_used}/{self.max_concurrent_decodes}, "
                f"hbm_used {self.hbm_used_gib:.3f}/{self.hbm_capacity_gib} GiB)")
        self.resident.add(id(inv))
        self.besteffort_hbm_gib += self._service.kv_footprint_gib(
            inv.max_context_tokens)

    def release(self, inv: Invocation) -> None:
        """Take an invocation off the device (its pin, if any, survives)."""
        was_reserved = id(inv) in self.resident_reserved
        self.resident.discard(id(inv))
        self.resident_reserved.discard(id(inv))
        if inv in self.decoding:
            self.decoding.remove(inv)
        if not was_reserved:
            self.besteffort_hbm_gib -= self._service.kv_footprint_gib(
                inv.max_context_tokens)
            if self.besteffort_hbm_gib < 0:
                self.besteffort_hbm_gib = 0.0
