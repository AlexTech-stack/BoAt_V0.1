"""FrameNode — unified frame send/subscribe via FrameService.

Usage::

    from boat.frame_node import FrameNode

    node = FrameNode("localhost:50051")

    # Send a CAN frame
    node.send_can("vcan0", 0x123, b"hello")

    # Subscribe to CAN + Ethernet frames
    def on_frame(frame):
        print(f"Got {frame.bus_type}: {frame}")

    node.subscribe(on_frame, bus_types=["CAN", "ETHERNET"])
    node.run()
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

from boat.client import BoAtClient
from boat.v1 import frame_pb2
from boat.v1 import frame_pb2_grpc


class FrameNode:
    """Unified frame node using the v8 FrameService gRPC endpoint."""

    def __init__(self, address: str = "localhost:50051",
                 bus_types: Optional[List[str]] = None) -> None:
        self._client = BoAtClient(address)
        self._bus_types = bus_types or []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def client(self) -> BoAtClient:
        return self._client

    # ── Send helpers ───────────────────────────────────────────────────

    def send(self, frame: frame_pb2.Frame) -> bool:
        """Send a unified Frame. Returns True if accepted."""
        req = frame_pb2.SendFrameRequest(frame=frame)
        resp = self._client.frame.SendFrame(req)
        return resp.accepted

    def send_can(self, iface: str, can_id: int, data: bytes,
                 is_fd: bool = False, flags: int = 0) -> bool:
        frame = frame_pb2.Frame()
        frame.bus_type = frame_pb2.Frame.CANFD if is_fd else frame_pb2.Frame.CAN
        frame.iface = iface
        frame.payload = data
        frame.can.can_id = can_id
        frame.can.dlc = len(data)
        frame.can.flags = flags
        return self.send(frame)

    def send_eth(self, iface: str, dst_mac: bytes, src_mac: bytes,
                 ethertype: int, payload: bytes, vlan_id: int = 0) -> bool:
        frame = frame_pb2.Frame()
        frame.bus_type = frame_pb2.Frame.ETHERNET
        frame.iface = iface
        frame.payload = payload
        frame.eth.dst_mac = dst_mac
        frame.eth.src_mac = src_mac
        frame.eth.ethertype = ethertype
        frame.eth.vlan_id = vlan_id
        return self.send(frame)

    def send_tcp(self, iface: str, src_ip: bytes, dst_ip: bytes,
                 src_port: int, dst_port: int, data: bytes,
                 ip_version: int = 4, conn_id: int = -1) -> bool:
        frame = frame_pb2.Frame()
        frame.bus_type = frame_pb2.Frame.TCP
        frame.iface = iface
        frame.payload = data
        frame.tcp.src_ip = src_ip
        frame.tcp.dst_ip = dst_ip
        frame.tcp.src_port = src_port
        frame.tcp.dst_port = dst_port
        frame.tcp.ip_version = ip_version
        frame.tcp.conn_id = conn_id
        return self.send(frame)

    # ── Subscribe ──────────────────────────────────────────────────────

    def subscribe(self, callback: Callable[[frame_pb2.Frame], None],
                  bus_types: Optional[List[str]] = None) -> None:
        """Stream frames in a background thread."""
        bt_values: List[int] = []
        if bus_types:
            for bt in bus_types:
                bt_map = {
                    "CAN": frame_pb2.Frame.CAN,
                    "CANFD": frame_pb2.Frame.CANFD,
                    "ETHERNET": frame_pb2.Frame.ETHERNET,
                    "TCP": frame_pb2.Frame.TCP,
                    "PDU": frame_pb2.Frame.PDU,
                }
                if bt in bt_map:
                    bt_values.append(bt_map[bt])

        def _run() -> None:
            req = frame_pb2.SubscribeFramesRequest()
            if bt_values:
                req.bus_types.extend(bt_values)
            try:
                for frame in self._client.frame.SubscribeFrames(req):
                    if self._stop.is_set():
                        return
                    callback(frame)
            except Exception:
                pass

        self._stop.clear()
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def run(self) -> None:
        """Block until stopped (keep-alive for background subscribers)."""
        while not self._stop.is_set():
            self._stop.wait(1.0)

    def stop(self) -> None:
        self._stop.set()
