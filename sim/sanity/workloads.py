"""SG1 sanity workloads (NOT the spec Section 5 generators).

These are the minimal, degenerate workloads the SG1 sanity checks need: a single-
invocation Poisson/exponential source that reduces the simulator to an M/M/1 queue.
The full flow-structure / mix / arrival / adversarial generators of spec Section 5
are an SG2 deliverable and will live in sim/workload/.

House style: hyphens only (D-026).
"""

from __future__ import annotations

from typing import Iterator

from ..core.seed import SeedManager
from ..core.types import Flow, Invocation


def mm1_workload(
    seed_mgr: SeedManager,
    arrival_rate_per_s: float,
    mean_output_tokens: float,
    prefill_tokens: int,
    horizon_s: float,
) -> Iterator[tuple[float, Flow]]:
    """Yield (arrival_time, flow) pairs for an M/M/1-degenerate workload.

    Interarrival times are Exponential(1/lambda) (Poisson arrivals); each flow has
    exactly one invocation whose output-token count is Exponential(mean). With a
    single-server device (decode batch cap 1) and constant per-request decode rate,
    service time = output_tokens / rate is exponential -> M/M/1.
    """
    arr = seed_mgr.stream("arrivals")
    tok = seed_mgr.stream("output_tokens")
    t = 0.0
    flow_id = 0
    while True:
        t += float(arr.exponential(1.0 / arrival_rate_per_s))
        if t > horizon_s:
            return
        out_tokens = max(1, int(round(float(tok.exponential(mean_output_tokens)))))
        inv = Invocation(
            flow_id=flow_id,
            index=0,
            prefill_tokens=int(prefill_tokens),
            output_tokens=out_tokens,
        )
        flow = Flow(flow_id=flow_id, arrival_time=t, invocations=[inv])
        yield (t, flow)
        flow_id += 1
