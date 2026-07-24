"""Top-level workload generator (spec Section 5).

Combines the four Section 5 components into one seeded, reproducible stream of
(arrival_time, Flow) pairs -- exactly the WorkloadSource the engine consumes:

  1. arrival process   (arrivals.py)  -> when flows arrive
  2. class assignment  (mix.py)       -> interactive vs agentic, by token volume
  3. flow structure    (flow_structure.py) -> invocations, tokens, gaps, KV growth
  4. adversarial       (this module)  -> runaway injection into agentic arrivals

Determinism (brief Section 4): all randomness is drawn from named SeedManager
streams, so the same global seed produces a byte-identical workload.

House style: hyphens only (D-026).
"""

from __future__ import annotations

from typing import Iterator

from ..core.seed import SeedManager
from ..core.types import Flow
from .arrivals import ArrivalSpec, arrival_times, generate_burst_intervals
from .flow_structure import ClassSpec, RunawaySpec, generate_flow
from .mix import agentic_flow_probability


class WorkloadGenerator:
    """A seeded, reproducible Section 5 workload source.

    Iterable and re-iterable: each iteration replays the identical workload (same
    seed -> same flows), so a single generator can drive every policy in a cell.
    """

    def __init__(self, config: dict, seed: int, horizon_s: float) -> None:
        self._cfg = config
        self._seed = int(seed)
        self._horizon = float(horizon_s)

        self.interactive = ClassSpec.from_config("interactive",
                                                 config["classes"]["interactive"])
        self.agentic = ClassSpec.from_config("agentic", config["classes"]["agentic"])
        self.arrival_spec = ArrivalSpec.from_config(config["arrivals"])
        self.runaway_spec = RunawaySpec.from_config(config["adversarial"]["runaway"])

        self.agentic_token_share = float(config["mix"]["agentic_token_share"])
        self.runaway_fraction = float(
            config["adversarial"]["runaway_fraction_of_agentic"])

        # Per-flow agentic probability calibrated to the target token-volume share.
        self.p_agentic = agentic_flow_probability(
            self.agentic_token_share,
            self.interactive.mean_tokens_per_flow(),
            self.agentic.mean_tokens_per_flow(),
        )

    def __iter__(self) -> Iterator[tuple[float, Flow]]:
        # A fresh SeedManager per iteration gives fresh-but-deterministic streams,
        # so every iteration replays the identical workload (same seed -> same
        # flows). This is what lets one generator drive every policy in a cell as a
        # paired comparison (brief Section 4).
        sm = SeedManager(self._seed)
        arr_rng = sm.stream("arrivals")
        burst_rng = sm.stream("burst")
        class_rng = sm.stream("class")
        struct_rng = sm.stream("structure")
        adv_rng = sm.stream("adversarial")

        bursts = generate_burst_intervals(self.arrival_spec, burst_rng, self._horizon)
        times = arrival_times(self.arrival_spec, arr_rng, self._horizon, bursts)

        for flow_id, t in enumerate(times):
            is_agentic = class_rng.random() < self.p_agentic
            if is_agentic:
                runaway = adv_rng.random() < self.runaway_fraction
                flow = generate_flow(flow_id, t, self.agentic, struct_rng,
                                     runaway=runaway, runaway_spec=self.runaway_spec)
            else:
                flow = generate_flow(flow_id, t, self.interactive, struct_rng)
            yield (t, flow)
