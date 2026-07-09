"""Example Ethernet node: custom-protocol-responder.

Listens on veth0 for frames with EtherType 0x88B5 (IEEE 802 experimental/local use).
Treats the payload as a UTF-8 command string. Responds with an acknowledgement frame
on the same interface and publishes the command text as an 'eth.command' bus signal.
"""
from __future__ import annotations

from boat.bus_node import BusNode
from boat.ethernet_node import EthernetNode


class CustomProtocolResponder(EthernetNode):
    def __init__(self) -> None:
        super().__init__(
            address="localhost:50051",
            iface_filter="veth0",
            ethertype_filter=0x88B5,
        )
        self._bus = BusNode(address="localhost:50051", node_id="eth-responder")

    def on_frame(self, frame, iface: str) -> None:
        try:
            command = frame.payload.decode("utf-8").strip()
        except UnicodeDecodeError:
            return

        # Publish command text as a bus signal
        self._bus.publish("eth.command", command)

        # Send an ACK frame back: same ethertype, payload "ACK:<command>"
        ack_payload = f"ACK:{command}".encode("utf-8")
        self.send(ethertype=0x88B5, payload=ack_payload, iface=iface)


if __name__ == "__main__":
    CustomProtocolResponder().run()
