"""Deterministic seed management (spec Section 8; brief Section 4).

One global seed derives independent per-stream generators. Streams are addressed
by name, and the mapping name -> generator is order-independent and stable: the
same (global_seed, name) always yields the same stream, regardless of the order in
which streams are first requested. This is what lets every policy in an experiment
cell run against the IDENTICAL workload realization (paired comparison) while
policy-internal tie-breaks draw from a separate policy-local stream.

House style: hyphens only (D-026).
"""

from __future__ import annotations

import zlib

import numpy as np


def _name_to_int(name: str) -> int:
    """Stable 32-bit integer from a stream name (order-independent)."""
    return zlib.crc32(name.encode("utf-8"))


class SeedManager:
    """Derives named, independent numpy Generators from one global seed."""

    def __init__(self, global_seed: int) -> None:
        self._global_seed = int(global_seed)
        self._streams: dict[str, np.random.Generator] = {}

    @property
    def global_seed(self) -> int:
        return self._global_seed

    def stream(self, name: str) -> np.random.Generator:
        """Return the generator for a named stream, creating it deterministically.

        The stream's seed is derived from (global_seed, hash(name)) via a
        SeedSequence, so it is independent of other streams and stable across runs.
        """
        gen = self._streams.get(name)
        if gen is None:
            seq = np.random.SeedSequence([self._global_seed, _name_to_int(name)])
            gen = np.random.default_rng(seq)
            self._streams[name] = gen
        return gen
