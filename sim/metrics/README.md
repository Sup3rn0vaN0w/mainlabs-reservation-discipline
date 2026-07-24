# sim/metrics

Metrics collection.

- `collectors.py` - `MetricsCollector` + `RunResult`. SG1 subset: time-average number in system (L), throughput (lambda), mean sojourn (W) for the Little's law check, and per-device busy-time utilization. Warmup cutoff discards the initial transient.

The full spec Section 2 metric set - goodput per provisioned GPU-hour, p95/p99 tail latencies, TTFT guardrail, reserved-idle fraction, wasted-computation fraction, bootstrap CIs - lands at SG6.
