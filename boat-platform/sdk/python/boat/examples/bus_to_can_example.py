"""Example: setpoint-actuator — react to a bus signal by sending a CAN frame.

Subscribes to the bus signal 'actuator.setpoint' (a float, 0.0–100.0 %).
Whenever the setpoint changes, encodes it as a 2-byte big-endian integer
(scaled ×10, so 0–1000) and sends it as CAN ID 0x400 on vcan0.
Also subscribes to 'actuator.enable' (bool) and only forwards when enabled.

Demonstrates:
  - Bus signal → CAN frame direction (the reverse of can_example.py)
  - Subscribing to multiple named signals in one BusNode
  - Encoding a float into a 2-byte CAN payload with .to_bytes()
  - Guarding sends with a state flag updated from a second signal
"""
from __future__ import annotations

from boat.bus_node import BusNode
from boat.client import BoAtClient
from boat.v1 import can_pb2


class SetpointActuator(BusNode):
    def __init__(self) -> None:
        super().__init__(address="localhost:50051", node_id="setpoint-actuator")
        self._enabled = False
        # A separate gRPC client is used to send CAN frames from a BusNode.
        self._can = BoAtClient(address="localhost:50051").can

    def on_signal(self, signal) -> None:
        if signal.name == "actuator.enable":
            self._enabled = bool(signal.bool_value)

        elif signal.name == "actuator.setpoint" and self._enabled:
            # Clamp and scale: 0.0-100.0 % → 0-1000 (uint16 big-endian)
            raw = max(0, min(1000, int(signal.number_value * 10)))
            payload = raw.to_bytes(2, "big")
            frame = can_pb2.CanFrame(can_id=0x400, dlc=2, data=payload, iface="vcan0")
            self._can.SendCanFrame(can_pb2.SendCanFrameRequest(frame=frame))


if __name__ == "__main__":
    node = SetpointActuator()
    node.run(names=["actuator.setpoint", "actuator.enable"])
