"""Example CAN node: speed-limiter-alert.

Listens on vcan0 for CAN ID 0x100 (vehicle speed, 2 bytes big-endian, unit: 0.1 km/h).
When speed exceeds 120 km/h, sends an alert frame (ID 0x200, payload 0x01) back on
the same interface and publishes a 'speed.alert' boolean signal on the BoAt bus.
"""
from __future__ import annotations

from boat.bus_node import BusNode
from boat.can_node import CanNode


class SpeedLimiterAlert(CanNode):
    def __init__(self) -> None:
        super().__init__(address="localhost:50051", iface_filter="vcan0")
        # A BusNode is used alongside to publish signals independently of CAN.
        self._bus = BusNode(address="localhost:50051", node_id="speed-alert")

    def on_frame(self, frame, iface: str) -> None:
        if frame.can_id != 0x100:
            return
        # Decode speed: 2 bytes big-endian, unit 0.1 km/h
        speed_kmh = int.from_bytes(frame.data[:2], "big") / 10.0
        alert = speed_kmh > 120.0
        # Publish boolean signal to the bus (always, so subscribers see updates)
        self._bus.publish("speed.alert", alert)
        if alert:
            # Send alert frame: ID 0x200, 1-byte payload 0x01
            self.send(can_id=0x200, data=bytes([0x01]), iface=iface)


if __name__ == "__main__":
    SpeedLimiterAlert().run()
