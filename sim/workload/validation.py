"""Workload validation (SG2 deliverable): stats, plots, reproducibility hash.

Produces the evidence the SG2 gate requires (brief Section 6):
  * distribution stats + plots that match the spec Section 5 parameters,
  * a seed-reproducibility hash (two runs -> identical),
  * a demonstration that the adversarial family is runaway.

Structural distributions are validated by sampling flows directly from
`generate_flow` (precise and cheap); mix and arrivals are validated from a full
`WorkloadGenerator` run.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from ..core.seed import SeedManager
from ..core.types import Flow
from .distributions import LognormalSpec
from .flow_structure import ClassSpec, RunawaySpec, generate_flow
from .generator import WorkloadGenerator


# --- direct structural sampling -------------------------------------------------

def sample_class_flows(
    spec: ClassSpec, seed: int, n: int,
    runaway: bool = False, runaway_spec: RunawaySpec | None = None,
) -> list[Flow]:
    """Directly sample `n` flows of one class (bypasses arrivals/mix)."""
    rng = SeedManager(seed).stream(f"validate_{spec.name}_{int(runaway)}")
    return [
        generate_flow(i, 0.0, spec, rng, runaway=runaway, runaway_spec=runaway_spec)
        for i in range(n)
    ]


def invocation_counts(flows: list[Flow]) -> np.ndarray:
    return np.array([len(f.invocations) for f in flows], dtype=float)


def all_gaps(flows: list[Flow]) -> np.ndarray:
    gaps = [inv.gap_after_s for f in flows for inv in f.invocations
            if inv.gap_after_s > 0]
    return np.array(gaps, dtype=float)


def all_output_tokens(flows: list[Flow]) -> np.ndarray:
    return np.array([inv.output_tokens for f in flows for inv in f.invocations],
                    dtype=float)


def total_tokens(flows: list[Flow]) -> float:
    return float(sum(inv.prefill_tokens + inv.output_tokens
                     for f in flows for inv in f.invocations))


@dataclass(frozen=True)
class DistStats:
    median: float
    p95: float
    mean: float
    n: int

    @classmethod
    def of(cls, arr: np.ndarray) -> "DistStats":
        if arr.size == 0:
            return cls(0.0, 0.0, 0.0, 0)
        return cls(
            median=float(np.percentile(arr, 50)),
            p95=float(np.percentile(arr, 95)),
            mean=float(np.mean(arr)),
            n=int(arr.size),
        )


def target_of(spec: LognormalSpec) -> dict:
    return {"median": spec.median, "p95": spec.p95}


# --- reproducibility hash -------------------------------------------------------

def workload_hash(flows: list[Flow]) -> str:
    """Deterministic SHA-256 over the materialized workload structure."""
    h = hashlib.sha256()
    for f in flows:
        h.update(f"F{f.flow_id}|{f.cls}|{int(f.is_runaway)}|{f.arrival_time:.6f}\n"
                 .encode("utf-8"))
        for inv in f.invocations:
            h.update(f"  I{inv.index}|{inv.prefill_tokens}|{inv.output_tokens}|"
                     f"{inv.context_tokens}|{inv.gap_after_s:.6f}\n".encode("utf-8"))
    return h.hexdigest()


def materialize(generator: WorkloadGenerator) -> list[Flow]:
    return [flow for _, flow in generator]


# --- mix and arrivals from a full run -------------------------------------------

def mix_token_share(flows: list[Flow]) -> dict:
    """Achieved agentic token-volume share and per-class flow/token counts."""
    tok = {"interactive": 0.0, "agentic": 0.0}
    cnt = {"interactive": 0, "agentic": 0}
    for f in flows:
        key = f.cls if f.cls in tok else "agentic"
        tok[key] += sum(inv.prefill_tokens + inv.output_tokens
                        for inv in f.invocations)
        cnt[key] += 1
    total = tok["interactive"] + tok["agentic"]
    share = tok["agentic"] / total if total > 0 else 0.0
    return {
        "agentic_token_share": share,
        "tokens": tok,
        "flow_counts": cnt,
        "total_flows": len(flows),
    }


def arrival_counts_per_bin(flows: list[Flow], horizon_s: float,
                           num_bins: int = 96) -> tuple[np.ndarray, np.ndarray]:
    """Arrival counts binned over [0, horizon] (default 96 bins = 15 min/day)."""
    times = np.array([f.arrival_time for f in flows], dtype=float)
    edges = np.linspace(0.0, horizon_s, num_bins + 1)
    counts, _ = np.histogram(times, bins=edges)
    centers = 0.5 * (edges[:-1] + edges[1:])
    return centers, counts
