"""DeviceNode — client for the structured DeviceService.

A device-shaped view over the always-on signal bus: list discovered devices
(power supplies, relays, generators, generic I/O), drive their controllable
channels, read measured state, and stream updates. Delegated by the gateway to
the ``device_manager`` plugin.

Example::

    node = DeviceNode()
    for dev in node.list_devices():
        print(dev.device_id, dev.kind)
    node.set_control("psu.main", "voltage", 24.0)   # setpoint
    node.set_control("relay.kl15", "state", 1)       # close ignition contact
    state = node.read_state("psu.main")
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

import grpc

from boat.client import BoAtClient
from boat.v1 import device_pb2


class DeviceNode:
    def __init__(self, address: str = "localhost:50051") -> None:
        self._client = BoAtClient(address)

    def list_devices(self) -> list[Any]:
        """Return all discovered devices (DeviceInfo messages)."""
        resp = self._client.device.ListDevices(device_pb2.ListDevicesRequest())
        return list(resp.devices)

    def set_control(self, device_id: str, channel: str, value: float) -> bool:
        """Drive a controllable channel (setpoint / command).

        Returns True if the gateway accepted it. Relay: value 0 = open, else closed.
        """
        resp = self._client.device.SetControl(
            device_pb2.SetControlRequest(
                device_id=device_id, channel=channel, value=float(value)
            )
        )
        return bool(resp.accepted)

    def read_state(self, device_id: str) -> Any | None:
        """Return a device's current DeviceInfo, or None if not discovered."""
        resp = self._client.device.ReadState(
            device_pb2.ReadStateRequest(device_id=device_id)
        )
        return resp.device if resp.found else None

    def stream_state(
        self,
        callback: Callable[[Any], None],
        device_ids: Iterable[str] | None = None,
    ) -> None:
        """Block, invoking callback for each DeviceStateUpdate until cancelled."""
        req = device_pb2.StreamStateRequest(device_ids=list(device_ids or []))
        try:
            for update in self._client.device.StreamState(req):
                callback(update)
        except grpc.RpcError:
            pass

    def close(self) -> None:
        self._client.close()
