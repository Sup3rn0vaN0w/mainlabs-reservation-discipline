"""Bootstrap confidence intervals and aggregation (spec Section 8).

The experiment reports CIs, not point estimates, and aggregates with the MEDIAN, not
the mean (spec Section 3: "no single-cell heroics"). This module is the one place
those two decisions live, so they are applied identically everywhere.

Determinism: the bootstrap draws from a seeded numpy Generator, so a CI is exactly
reproducible from the same inputs. Nothing here calls an unseeded RNG.

House style: hyphens only (D-026).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CI:
    """A point estimate with a percentile-bootstrap confidence interval."""

    point: float
    lo: float
    hi: float
    n: int
    confidence: float

    def as_dict(self) -> dict:
        return {"point": self.point, "lo": self.lo, "hi": self.hi,
                "n": self.n, "confidence": self.confidence}


def bootstrap_ci(values, statistic="median", n_resamples: int = 2000,
                 confidence: float = 0.95, seed: int = 0) -> CI:
    """Percentile-bootstrap CI for a statistic of `values`.

    `statistic` is "median" (default, per spec Section 3) or "mean". The interval is
    the central `confidence` mass of the bootstrap distribution. With fewer than two
    values the interval collapses to the point estimate -- honestly wide is not
    possible with n<2, so callers should treat a degenerate CI as "insufficient
    replication", not as certainty.
    """
    arr = np.asarray(list(values), dtype=float)
    n = arr.size
    stat_fn = np.median if statistic == "median" else np.mean
    if n == 0:
        return CI(0.0, 0.0, 0.0, 0, confidence)
    point = float(stat_fn(arr))
    if n == 1:
        return CI(point, point, point, 1, confidence)

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_resamples, n))
    boot = stat_fn(arr[idx], axis=1)
    alpha = 1.0 - confidence
    lo = float(np.percentile(boot, 100 * alpha / 2))
    hi = float(np.percentile(boot, 100 * (1 - alpha / 2)))
    return CI(point, lo, hi, n, confidence)


def relative_improvement(ours: float, baseline: float) -> float:
    """(ours - baseline) / baseline, guarded against a zero baseline."""
    if baseline == 0:
        return 0.0
    return (ours - baseline) / baseline


def bootstrap_relative_improvement(ours, baseline, n_resamples: int = 2000,
                                   confidence: float = 0.95, seed: int = 0) -> CI:
    """CI on the relative improvement of paired samples (ours vs baseline).

    `ours` and `baseline` are aligned per-seed (or per-cell) samples. Resampling the
    PAIRS together preserves the pairing the experiment is built on (same workload
    realization per policy), which is what makes the comparison a paired one.
    """
    o = np.asarray(list(ours), dtype=float)
    b = np.asarray(list(baseline), dtype=float)
    if o.size != b.size:
        raise ValueError(f"paired samples must be equal length: {o.size} vs {b.size}")
    n = o.size
    if n == 0:
        return CI(0.0, 0.0, 0.0, 0, confidence)

    def rel(oi, bi):
        mb = np.median(bi)
        return (np.median(oi) - mb) / mb if mb != 0 else 0.0

    point = rel(o, b)
    if n == 1:
        return CI(point, point, point, 1, confidence)

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_resamples, n))
    boot = np.array([rel(o[row], b[row]) for row in idx])
    alpha = 1.0 - confidence
    lo = float(np.percentile(boot, 100 * alpha / 2))
    hi = float(np.percentile(boot, 100 * (1 - alpha / 2)))
    return CI(float(point), lo, hi, n, confidence)
