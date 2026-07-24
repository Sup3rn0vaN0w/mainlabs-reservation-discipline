"""Agentic flow lifetime, derived from the workload parameters (Amendment 2).

Amendment 2 (ratified 2026-07-15) requires every sweep horizon to be at least the
median agentic flow lifetime, so that agentic flows can complete within the
measurement window and the primary metric is not right-censored against the
reservation discipline (docs/H3_ZERO_CHECK.md). This module DERIVES that lifetime
from the frozen workload parameters -- it is not a magic number -- so the horizon
guard is structural, not a convention.

A flow's minimum lifetime (with zero queueing, on an idle cluster) is the sum of its
inter-invocation tool-time gaps plus its prefill and decode service. Real lifetimes
are longer (queueing), so this is a conservative lower bound on how long the window
must be.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import statistics

from ..cluster.service_model import ServiceModel
from .generator import WorkloadGenerator
from ..tuning.harness import apply_overrides


def agentic_flow_min_lifetimes(workload_cfg: dict, service: ServiceModel,
                               n_flows: int = 400, seed: int = 1) -> list[float]:
    """Sample agentic flows and return each one's minimum (no-queueing) lifetime.

    Sampled at mix 1.0 (all agentic) because a flow's structure -- and hence its
    lifetime -- is independent of the traffic mix; the mix only sets how often
    agentic flows arrive. This makes the sample fast and mix-independent.
    """
    cfg = apply_overrides(workload_cfg, {"mix.agentic_token_share": 1.0})
    # A horizon long enough to emit n_flows at the configured arrival rate.
    rate = cfg["arrivals"]["base_rate_per_s"]
    horizon = max(1000.0, 2.0 * n_flows / max(rate, 1e-6))

    lifetimes: list[float] = []
    for _, flow in WorkloadGenerator(cfg, seed=seed, horizon_s=horizon):
        if flow.cls != "agentic":
            continue
        gap = sum(inv.gap_after_s for inv in flow.invocations)
        decode = sum(inv.output_tokens for inv in flow.invocations) \
            / service.single_stream_tokens_per_s
        prefill = sum(service.prefill_time(inv.prefill_tokens)
                      for inv in flow.invocations)
        lifetimes.append(gap + decode + prefill)
        if len(lifetimes) >= n_flows:
            break
    return lifetimes


def median_agentic_flow_lifetime(workload_cfg: dict, service: ServiceModel,
                                 seed: int = 1) -> float:
    """Median minimum agentic flow lifetime, in seconds."""
    lt = agentic_flow_min_lifetimes(workload_cfg, service, seed=seed)
    return statistics.median(lt) if lt else 0.0


def assert_horizon_adequate(horizon_s: float, workload_cfg: dict,
                            service: ServiceModel, *, context: str = "") -> float:
    """Raise if `horizon_s` is below the median agentic flow lifetime.

    Structural enforcement of Amendment 2: a sweep may not run at a horizon that
    right-censors the agentic flows the reservation discipline is built to serve.
    Returns the median lifetime it checked against.
    """
    median = median_agentic_flow_lifetime(workload_cfg, service)
    if horizon_s < median:
        raise ValueError(
            f"horizon {horizon_s:.0f}s is below the median agentic flow lifetime "
            f"{median:.0f}s{(' (' + context + ')') if context else ''}. Amendment 2 "
            f"forbids a right-censoring horizon; set horizon >= {median:.0f}s.")
    return median
