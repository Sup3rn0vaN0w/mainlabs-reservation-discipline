"""Distribution helpers for the Section 5 workload generators.

Lognormals are specified by (median, p95) -- the form the spec uses -- and
converted to the underlying normal's (mu, sigma):

    median = exp(mu)                        -> mu = ln(median)
    p95    = exp(mu + z95 * sigma)          -> sigma = ln(p95 / median) / z95

with z95 the 0.95 standard-normal quantile. A degenerate spec (median <= 0, or
p95 <= median) collapses to a point mass at `median`, which is how zero-gap
interactive traffic and single-invocation counts are expressed.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# 0.95 quantile of the standard normal (scipy.stats.norm.ppf(0.95)).
Z95 = 1.6448536269514722


@dataclass(frozen=True)
class LognormalSpec:
    """A lognormal distribution specified by its median and 95th percentile."""

    median: float
    p95: float

    @classmethod
    def from_config(cls, cfg: dict) -> "LognormalSpec":
        return cls(median=float(cfg["median"]), p95=float(cfg["p95"]))

    @property
    def degenerate(self) -> bool:
        return self.median <= 0.0 or self.p95 <= self.median

    @property
    def mu(self) -> float:
        return math.log(self.median) if self.median > 0 else 0.0

    @property
    def sigma(self) -> float:
        if self.degenerate:
            return 0.0
        return math.log(self.p95 / self.median) / Z95

    def mean(self) -> float:
        """E[X] = exp(mu + sigma^2 / 2); the point mass value when degenerate."""
        if self.degenerate:
            return max(self.median, 0.0)
        return math.exp(self.mu + 0.5 * self.sigma ** 2)

    def sample(self, rng: np.random.Generator, size: int | None = None):
        """Draw from the lognormal (or the point mass if degenerate)."""
        if self.degenerate:
            value = max(self.median, 0.0)
            return value if size is None else np.full(size, value, dtype=float)
        normal = rng.normal(self.mu, self.sigma, size=size)
        return np.exp(normal)

    def sample_scalar(self, rng: np.random.Generator) -> float:
        return float(self.sample(rng))


def sample_positive_int(spec: LognormalSpec, rng: np.random.Generator,
                        floor: int = 1) -> int:
    """Sample a lognormal and round to an integer >= floor (counts / token sizes)."""
    return max(floor, int(round(spec.sample_scalar(rng))))
