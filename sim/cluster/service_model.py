"""Service physics: the ENGINE's cost models (brief Section 4).

These functions are the only place decode/prefill/KV/spill costs are computed.
They are identical for every scheduling policy -- this is the fairness guarantee
(brief Section 4: no policy-specific mechanics, ever). All constants come from a
config dict (loaded from configs/service_model.yaml); nothing is hard-coded.

Every constant is LOW CONFIDENCE until Phase 2 calibration (spec Section 6/9).

House style: hyphens only (D-026).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceModel:
    """Immutable service-cost model built from configs/service_model.yaml."""

    single_stream_tokens_per_s: float
    peak_decode_tokens_per_s: float
    prefill_tokens_per_s: float
    kv_mib_per_token: float
    spill_bandwidth_gib_per_s: float
    spill_latency_s: float

    @classmethod
    def from_config(cls, cfg: dict) -> "ServiceModel":
        """Build from the parsed service_model.yaml mapping."""
        decode = cfg["decode"]
        prefill = cfg["prefill"]
        kv = cfg["kv_cache"]
        spill = cfg["host_spill"]
        return cls(
            single_stream_tokens_per_s=float(decode["single_stream_tokens_per_s"]),
            peak_decode_tokens_per_s=float(decode["peak_decode_tokens_per_s"]),
            prefill_tokens_per_s=float(prefill["prefill_tokens_per_s"]),
            kv_mib_per_token=float(kv["mib_per_token"]),
            spill_bandwidth_gib_per_s=float(spill["bandwidth_gib_per_s"]),
            spill_latency_s=float(spill["latency_s"]),
        )

    # --- decode -----------------------------------------------------------------

    def decode_throughput(self, batch_size: int) -> float:
        """Aggregate device decode throughput (tokens/s) at a given decode batch.

        throughput(b) = min(b * single_stream, peak). Per-request decode rate is
        throughput(b) / b: constant at low batch, decreasing once the device
        saturates at its peak aggregate rate. Monotic non-decreasing in b.
        """
        if batch_size <= 0:
            return 0.0
        return min(batch_size * self.single_stream_tokens_per_s,
                   self.peak_decode_tokens_per_s)

    def decode_rate_per_request(self, batch_size: int) -> float:
        """Per-request decode rate (tokens/s) under processor sharing."""
        if batch_size <= 0:
            return 0.0
        return self.decode_throughput(batch_size) / batch_size

    # --- prefill ----------------------------------------------------------------

    def prefill_time(self, prefill_tokens: int) -> float:
        """Head-of-service prefill latency (s) for an invocation."""
        if prefill_tokens <= 0:
            return 0.0
        return prefill_tokens / self.prefill_tokens_per_s

    # --- KV footprint and host spill --------------------------------------------

    def kv_footprint_gib(self, context_tokens: int) -> float:
        """KV-cache HBM footprint (GiB) for a given context length."""
        return context_tokens * self.kv_mib_per_token / 1024.0

    def spill_time(self, gib: float) -> float:
        """Latency (s) to spill `gib` of KV to host memory.

        Present for SG3/SG4 paging mechanics; not triggered by SG1 sanity configs.
        """
        if gib <= 0:
            return 0.0
        return self.spill_latency_s + gib / self.spill_bandwidth_gib_per_s
