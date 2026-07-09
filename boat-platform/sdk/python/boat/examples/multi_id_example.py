"""Example: door-control-node — handle multiple CAN IDs in a single node.

Listens on vcan0 (iface_filter="vcan0") for three different IDs:
  0x300 — door open request:  sends ack 0x310, publishes door.state = "open"
  0x301 — door close request: sends ack 0x311, publishes door.state = "closed"
  0x302 — status query:       sends status reply 0x312 with current state byte

Demonstrates:
  - Handling multiple CAN IDs cleanly using a dispatch dict
  - Maintaining simple state across frames
  - Sending different response frames per ID
"""
from __future__ import annotations

from boat.bus_node import BusNode
from boat.can_node import CanNode


class DoorControlNode(CanNode):
    def __init__(self) -> None:
        super().__init__(address="localhost:50051", iface_filter="vcan0")
        self._bus = BusNode(address="localhost:50051", node_id="door-control")
        self._state = "closed"  # internal state persists across frames

        # Dispatch table: can_id → handler method
        self._handlers = {
            0x300: self._handle_open,
            0x301: self._handle_close,
            0x302: self._handle_status_query,
        }

    def on_frame(self, frame, iface: str) -> None:
        handler = self._handlers.get(frame.can_id)
        if handler:
            handler(frame, iface)

    def _handle_open(self, frame, iface: str) -> None:
        self._state = "open"
        self._bus.publish("door.state", self._state)
        self.send(can_id=0x310, data=bytes([0x01]), iface=iface)  # ACK open

    def _handle_close(self, frame, iface: str) -> None:
        self._state = "closed"
        self._bus.publish("door.state", self._state)
        self.send(can_id=0x311, data=bytes([0x00]), iface=iface)  # ACK close

    def _handle_status_query(self, frame, iface: str) -> None:
        # Encode state as a single status byte: 0x01 = open, 0x00 = closed
        status = 0x01 if self._state == "open" else 0x00
        self.send(can_id=0x312, data=bytes([status]), iface=iface)


if __name__ == "__main__":
    DoorControlNode().run()
