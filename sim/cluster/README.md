# sim/cluster

The device model and service physics.

- `service_model.py` - `ServiceModel`: the ONLY place decode/prefill/KV/spill costs are computed. Identical for every policy (the fairness guarantee). All constants come from `configs/service_model.yaml`; every value is LOW CONFIDENCE until Phase 2 calibration.
- `device.py` - `Device`: HBM budget, decode batch cap, resident-invocation and KV accounting, capacity queries. No scheduling logic.
- `cluster.py` - `Cluster`: an N-device fleet (size sweep 8-64) built from config.

Decode model: `throughput(b) = min(b * single_stream, peak)`, per-request rate `throughput(b)/b`. At batch 1 this is a constant-rate server, which the SG1 M/M/1 sanity checks rely on.
