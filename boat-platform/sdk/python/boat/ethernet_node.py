"""Base class for Python Ethernet nodes.

An Ethernet node connects to the BoAt gateway, subscribes to Ethernet frames,
and can send frames in response.  Subclass EthernetNode, override on_frame(),
then call run() or run_background().

Virtual interfaces use UDP multicast under the hood; the BoAt gateway handles
all transport details — from Python you only deal with EthernetFrame objects.

Example::

    class MyNode(EthernetNode):
        def on_frame(self, frame, iface: str) -> None:
            if frame.ethertype == 0x0800:  # IPv4
                print(f"IPv4 on {iface}: {frame.payload.hex(':')}")

    node = MyNode(iface_filter="veth0")
    node.run()

Sending a frame::

    node = EthernetNode()
    node.send(
        ethertype=0x88B5,                   # custom/test ethertype
        payload=bytes([0xDE, 0xAD, 0xBE]),
        iface="veth0",                      # interface name is required
    )
"""

from __future__ import annotations

import threading
import warnings
from typing import Any

import grpc

from boat.client import BoAtClient
from boat.v1 import ethernet_pb2


class EthernetNode:
    """Abstract base for Python Ethernet processing nodes.

    Args:
        address:          Gateway gRPC address (host:port).
        iface_filter:     Interface to subscribe to.  Empty = all interfaces.
        ethertype_filter: EtherType filter (0 = all).
    """

    def __init__(
        self,
        address: str = "localhost:50051",
        iface_filter: str = "",
        ethertype_filter: int = 0,
    ) -> None:
        warnings.warn(
            "EthernetNode is deprecated. Use FrameNode(bus_types=['ETHERNET']) instead.",
            DeprecationWarning, stacklevel=2)
        self._client = BoAtClient(address)
        self._iface_filter = iface_filter
        self._ethertype_filter = ethertype_filter
        self._stream: Any = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Override in subclass
    # ------------------------------------------------------------------

    def on_frame(self, frame: Any, iface: str) -> None:  # noqa: B027
        """Called for every received Ethernet frame.  Override in subclass."""

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    def send(
        self,
        ethertype: int,
        payload: bytes,
        iface: str,
        src_mac: bytes = b"",
        dst_mac: bytes = b"",
    ) -> bool:
        """Send an Ethernet frame via the gateway.

        Args:
            ethertype: 16-bit EtherType (e.g. 0x0800=IPv4, 0x86DD=IPv6).
            payload:   Frame payload bytes (max 1500).
            iface:     Ethernet interface to send on (e.g. ``"veth0"``).
            src_mac:   Source MAC (6 bytes).  Leave empty for virtual frames.
            dst_mac:   Destination MAC (6 bytes).  Leave empty for broadcast.

        Returns:
            True if the gateway accepted the frame.
        """
        frame = ethernet_pb2.EthernetFrame(
            iface=iface,
            src_mac=src_mac,
            dst_mac=dst_mac,
            ethertype=ethertype,
            payload=bytes(payload[:1500]),
        )
        try:
            resp = self._client.ethernet.SendFrame(
                ethernet_pb2.SendEthernetFrameRequest(frame=frame)
            )
            return bool(resp.accepted)
        except grpc.RpcError:
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Subscribe to Ethernet frames and block until stop() is called."""
        self._stop_event.clear()
        self._stream = self._client.ethernet.SubscribeFrames(
            ethernet_pb2.SubscribeEthernetFramesRequest(
                iface=self._iface_filter,
                ethertype=self._ethertype_filter,
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
