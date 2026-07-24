"""A priority queue for ready invocations, shared by every queue-based policy.

## Why this exists

The naive thing -- keep the ready set in a list, sort it on every event, scan it
looking for something placeable -- is O(n log n) per event. Under sustained overload
the backlog grows without bound, so that becomes O(n^2) over a run. Measured, it was
brutal: at 1.3x capacity a single 300s run took 228s for the Niyama baseline and
153s for vLLM, against 1.6s for policies that bound their own backlog by admitting
less work.

That is not a physics result, it is a data-structure artifact -- and a DANGEROUS one,
because it penalizes exactly the policies that let a queue build up. A comparison
where the baselines look bad because of how their queue is stored is not a
comparison. Every policy gets the same queue, with the same cost model, for the same
reason the engine gives every policy the same service physics (brief Section 4).

## What it guarantees

* `push` / `pop` are O(log n). No per-event re-sort.
* When the cluster is full, a scheduling pass does NO queue work at all -- there is
  nothing to place, so there is nothing to look at.
* `drain` examines at most `scan_budget` invocations per pass. A real scheduler does
  not rescan an unbounded backlog every time a slot frees either; it looks at the
  head of the queue. The budget is applied IDENTICALLY to every policy.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import heapq
from typing import Callable

from ..core.types import Invocation

# How deep a single scheduling pass may look past invocations it cannot place (for
# example because their KV will not fit anywhere right now). Applied identically to
# every policy. Large enough that it never binds in practice at the cluster sizes in
# the experiment matrix; small enough that a pathological backlog cannot make one
# pass O(n).
DEFAULT_SCAN_BUDGET = 64


class PolicyQueue:
    """Ready invocations, ordered by a policy-supplied key.

    The key may change over time (Niyama demotes flows; MLFQ demotes invocations).
    When it does, call `reheapify()` -- typically on a tick, not on every event.
    """

    def __init__(self, key_fn: Callable[[Invocation], tuple]) -> None:
        self._key = key_fn
        self._heap: list[tuple] = []
        self._seq = 0    # tie-break, and keeps Invocation out of the comparison

    def __len__(self) -> int:
        return len(self._heap)

    def __bool__(self) -> bool:
        return bool(self._heap)

    def push(self, inv: Invocation) -> None:
        heapq.heappush(self._heap, (self._key(inv), self._seq, inv))
        self._seq += 1

    def peek(self) -> Invocation | None:
        return self._heap[0][2] if self._heap else None

    def pop(self) -> Invocation:
        return heapq.heappop(self._heap)[2]

    def reheapify(self) -> None:
        """Re-key everything. Call after priorities change (O(n))."""
        items = [entry[2] for entry in self._heap]
        self._heap = []
        self._seq = 0
        for inv in items:
            self.push(inv)

    def snapshot(self) -> list[Invocation]:
        """All queued invocations in priority order (O(n log n)); for reconcilers."""
        return [entry[2] for entry in sorted(self._heap)]

    def drain(
        self,
        try_place: Callable[[Invocation], bool],
        has_capacity: Callable[[], bool],
        scan_budget: int = DEFAULT_SCAN_BUDGET,
    ) -> None:
        """Place queued work, best first, while capacity lasts.

        `try_place(inv)` returns True if it placed the invocation (and has already
        booked the capacity). Invocations it declines are set aside and returned to
        the queue afterwards, so nothing is lost and priority order is preserved.

        Costs nothing when `has_capacity()` is False -- which is the common case
        precisely when the backlog is largest.
        """
        if not self._heap or not has_capacity():
            return

        set_aside: list[Invocation] = []
        examined = 0
        while self._heap and has_capacity() and examined < scan_budget:
            inv = self.pop()
            examined += 1
            if not try_place(inv):
                set_aside.append(inv)
        for inv in set_aside:
            self.push(inv)
