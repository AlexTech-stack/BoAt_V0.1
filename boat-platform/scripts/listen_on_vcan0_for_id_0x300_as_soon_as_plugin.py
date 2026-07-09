"""Echo CAN frame from vcan0 to vcan1."""

from boat.can_node import CanNode


class EchoCanFrame(CanNode):
    def __init__(self) -> None:
        super().__init__(address="localhost:50051", iface_filter="vcan0")

    def on_frame(self, frame, iface: str) -> None:
        if frame.can_id != 0x300:
            return
        # Send the same payload with ID 0x301 on vcan1
        self.send(can_id=0x301, data=frame.data, iface="vcan1")


if __name__ == "__main__":
    EchoCanFrame().run()
