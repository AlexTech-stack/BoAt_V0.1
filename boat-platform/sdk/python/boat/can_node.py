"""Base class for Python CAN nodes.

.. deprecated:: v8
   Use ``FrameNode`` with ``bus_types=["CAN"]`` instead.

A CAN node connects to the BoAt gateway, subscribes to CAN frames, and can
send frames in response.  Subclass CanNode, override on_frame(), then call
run() or run_background().

Example::

    class MyNode(CanNode):
        def on_frame(self, frame, iface: str) -> None:
            if frame.can_id == 0x123 and iface == "vcan1":
                self.send(0x234, bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88]))

    node = MyNode(address="localhost:50051", iface_filter="vcan1")
    node.run()
"""

from __future__ import annotations

import threading
import warnings
from typing import Any

import grpc

from boat.client import BoAtClient
from boat.v1 import can_pb2


class CanNode:
    """Abstract base for Python CAN processing nodes.

    Args:
        address:      Gateway gRPC address (host:port).
        iface_filter: CAN interface to subscribe to.  Empty string means all
                      registered interfaces.
        sim_id:       Simulation ID forwarded in subscribe/send requests.
    """

    def __init__(
        self,
        address: str = "localhost:50051",
        iface_filter: str = "",
        sim_id: str = "",
    ) -> None:
        warnings.warn(
            "CanNode is deprecated. Use FrameNode(bus_types=['CAN']) instead.",
            DeprecationWarning, stacklevel=2)
        self._client = BoAtClient(address)
        self._iface_filter = iface_filter
        self._sim_id = sim_id
        self._stream: Any = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Override in subclass
    # ------------------------------------------------------------------

    def on_frame(self, frame: Any, iface: str) -> None:  # noqa: B027
        """Called for every received CAN frame.  Override in subclass."""

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    def send(self, can_id: int, data: bytes, iface: str) -> bool:
        """Send a CAN frame via the gateway.

        Args:
            can_id: 11-bit or 29-bit CAN identifier.
            data:   Payload bytes (max 8).
            iface:  CAN interface to send on (e.g. ``"vcan0"``).

        Returns:
            True if the gateway accepted the frame.
        """
        payload = bytes(data[:8])
        frame = can_pb2.CanFrame(
            can_id=can_id,
            dlc=len(payload),
            data=payload,
            iface=iface,
        )
        try:
            resp = self._client.can.SendCanFrame(
                can_pb2.SendCanFrameRequest(simulation_id=self._sim_id, frame=frame)
            )
            return bool(resp.accepted)
        except grpc.RpcError:
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Subscribe to CAN frames and block until stop() is called or
        the stream is cancelled by the server."""
        self._stop_event.clear()
        self._stream = self._client.can.SubscribeCanFrames(
            can_pb2.SubscribeCanFramesRequest(
                simulation_id=self._sim_id,
                iface=self._iface_filter,
            )
        )
        try:
            for frame in self._stream:
                if self._stop_event.is_set():
                    break
                iface = frame.iface or self._iface_filter
                self.on_frame(frame, iface)
        except grpc.RpcError:
            pass
        finally:
            self._stream.cancel()
            self._client.close()

    def run_background(self) -> threading.Thread:
        """Start the node in a daemon thread.  Returns the thread."""
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        """Signal the node to stop after the current frame."""
        self._stop_event.set()
        if self._stream is not None:
            self._stream.cancel()
