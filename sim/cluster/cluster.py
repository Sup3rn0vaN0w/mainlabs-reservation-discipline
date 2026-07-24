"""Cluster: an N-device fleet built from config (brief Section 3).

House style: hyphens only (D-026).
"""

from __future__ import annotations

from .device import Device
from .service_model import ServiceModel


class Cluster:
    """A homogeneous fleet of N devices sharing one service model."""

    def __init__(self, devices: list[Device], service: ServiceModel) -> None:
        self.devices = devices
        self.service = service

    @classmethod
    def from_config(cls, cluster_cfg: dict, service: ServiceModel) -> "Cluster":
        """Build from the `cluster:` block of a resolved config."""
        n = int(cluster_cfg["num_devices"])
        dev_cfg = cluster_cfg["device"]
        devices = [
            Device(
                device_id=i,
                hbm_capacity_gib=float(dev_cfg["hbm_capacity_gib"]),
                max_concurrent_decodes=int(dev_cfg["max_concurrent_decodes"]),
                service=service,
            )
            for i in range(n)
        ]
        return cls(devices, service)

    @property
    def num_devices(self) -> int:
        return len(self.devices)

    def device(self, device_id: int) -> Device:
        return self.devices[device_id]
