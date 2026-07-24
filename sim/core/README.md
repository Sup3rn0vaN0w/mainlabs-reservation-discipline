# sim/core

The engine and the simulation's spine.

- `types.py` - domain data carriers: `Flow`, `Invocation`, `InvPhase`, `RunConfig`. No logic.
- `seed.py` - `SeedManager`: one global seed -> named, independent, order-stable numpy streams (spec Section 8; brief Section 4 determinism).
- `engine.py` - `SimulationEngine`: owns the SimPy clock and ALL physics. Drives arrivals, delivers events to the policy, executes returned actions, models prefill (head latency) + decode (processor sharing with batch-size-dependent throughput), accounts capacity and busy time. Preempt/Degrade are hard errors until SG3/SG4.
- `orchestration.py` - load configs, build the cluster, run a simulation, return a `RunResult`.

The engine/policy boundary is the load-bearing rule (brief Section 4): the engine decides nothing about scheduling; policies compute no physics.
