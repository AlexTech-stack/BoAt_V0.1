#!/usr/bin/env python3
"""Ethernet cyclic sender node.

Behaviour:
  - Listens on vcan0 for CAN ID 0xA1 (any payload) → starts sending a UDP/IPv4
    Ethernet frame every 1 s on each registered Ethernet interface.
  - Listens on vcan0 for CAN ID 0xA2 (any payload) → stops cyclic sending.
  - Bus signal ``eth_cyclic_sender.payload`` (bytes) updates the UDP payload
    at any time without stopping the cyclic loop.

Ethernet frame layout:
  src_mac   02:00:00:00:00:01   (locally-administered, unicast)
  dst_mac   FF:FF:FF:FF:FF:FF   (broadcast)
  ethertype 0x0800              (IPv4)
  IP src    192.168.100.1
  IP dst    192.168.100.255     (directed broadcast)
  protocol  UDP (17)
  UDP src   5000
  UDP dst   5001
  payload   initial: 11 AA 22 BB 33 CC  (updateable via bus signal)

Usage:
    python3 nodes/eth_cyclic_sender_node.py [--address localhost:50051]
"""

from __future__ import annotations

import argparse
import signal
import socket
import struct
import sys
import threading
import time

from boat.bus_node import BusNode
from boat.can_node import CanNode
from boat.client import BoAtClient
from boat.ethernet_node import EthernetNode
from boat.v1 import ethernet_pb2

# ── CAN trigger IDs ──────────────────────────────────────────────────────────
_START_ID    = 0xA1
_STOP_ID     = 0xA2
_CAN_IFACE   = "vcan0"

# ── Ethernet / IP / UDP parameters ───────────────────────────────────────────
_SRC_MAC     = bytes.fromhex("020000000001")   # locally-administered
_DST_MAC     = bytes.fromhex("FFFFFFFFFFFF")   # broadcast
_ETHERTYPE   = 0x0800                          # IPv4

_SRC_IP      = "192.168.100.1"
_DST_IP      = "192.168.100.255"               # directed subnet broadcast
_SRC_PORT    = 5000
_DST_PORT    = 5001
_TTL         = 64

# ── Cyclic parameters ────────────────────────────────────────────────────────
_CYCLE_S       = 1.0
_INITIAL_DATA  = bytes([0x11, 0xAA, 0x22, 0xBB, 0x33, 0xCC])
_BUS_SIGNAL    = "eth_cyclic_sender.payload"


# ── IPv4 header checksum ─────────────────────────────────────────────────────

def _ipv4_checksum(header: bytes) -> int:
    total = 0
    for i in range(0, len(header), 2):
        word = (header[i] << 8) + header[i + 1]
        total += word
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _build_ipv4_udp(udp_payload: bytes) -> bytes:
    """Wrap *udp_payload* in a UDP/IPv4 packet (no UDP checksum)."""
    udp_len = 8 + len(udp_payload)
    udp_header = struct.pack(">HHHH",
        _SRC_PORT,
        _DST_PORT,
        udp_len,
        0,          # checksum omitted (valid for IPv4)
    )

    ip_total = 20 + udp_len
    # Build IP header with checksum field = 0 first
    ip_no_csum = struct.pack(">BBHHHBBH4s4s",
        0x45,                           # Version=4, IHL=5 (20 bytes)
        0x00,                           # DSCP / ECN
        ip_total,
        0x0000,                         # Identification
        0x0000,                         # Flags + Fragment offset
        _TTL,
        17,                             # Protocol: UDP
        0x0000,                         # Checksum placeholder
        socket.inet_aton(_SRC_IP),
        socket.inet_aton(_DST_IP),
    )
    csum = _ipv4_checksum(ip_no_csum)
    ip_header = ip_no_csum[:10] + struct.pack(">H", csum) + ip_no_csum[12:]

    return ip_header + udp_header + udp_payload


# ── Bus listener ─────────────────────────────────────────────────────────────

class _PayloadListener(BusNode):
    """Background bus subscriber — forwards payload updates via callback."""

    def __init__(self, address: str, on_payload) -> None:
        super().__init__(address=address, node_id="eth-cyclic-sender-bus")
        self._on_payload = on_payload

    def on_signal(self, sig) -> None:
        if sig.name == _BUS_SIGNAL and sig.WhichOneof("value") == "bytes_value":
            self._on_payload(sig.bytes_value)


# ── Main node ─────────────────────────────────────────────────────────────────

class EthCyclicSenderNode(CanNode):
    """Listens on vcan0 for start/stop, sends cyclic UDP/IPv4 Ethernet frames."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._active         = threading.Event()
        self._cyclic_thread: threading.Thread | None = None
        self._payload_lock   = threading.Lock()
        self._udp_payload    = bytearray(_INITIAL_DATA)

        address = kwargs.get("address", "localhost:50051")
        self._address = address
        self._eth = EthernetNode(address=address)   # used only for send()
        self._bus = _PayloadListener(address=address, on_payload=self._update_payload)

    # ── bus callback ─────────────────────────────────────────────────────────

    def _update_payload(self, data: bytes) -> None:
        with self._payload_lock:
            self._udp_payload = bytearray(data)
        print(f"[eth-cyclic] UDP payload updated → {data.hex(':')}")

    # ── CAN triggers ─────────────────────────────────────────────────────────

    def on_frame(self, frame, iface: str) -> None:
        if frame.can_id == _START_ID:
            self._start_cyclic()
        elif frame.can_id == _STOP_ID:
            self._stop_cyclic()

    # ── cyclic control ───────────────────────────────────────────────────────

    def _get_ifaces(self) -> list[str]:
        try:
            client = BoAtClient(self._address)
            resp = client.ethernet.ListInterfaces(
                ethernet_pb2.ListEthernetInterfacesRequest()
            )
            return list(resp.ifaces)
        except Exception:
            return []

    def _start_cyclic(self) -> None:
        if self._active.is_set():
            return  # already running
        with self._payload_lock:
            payload_hex = self._udp_payload.hex(":")
        print(f"[eth-cyclic] START — every {_CYCLE_S} s on each registered Ethernet interface"
              f"  UDP payload={payload_hex}")
        self._active.set()
        self._cyclic_thread = threading.Thread(
            target=self._cyclic_loop, daemon=True, name="eth-cyclic"
        )
        self._cyclic_thread.start()

    def _stop_cyclic(self) -> None:
        if not self._active.is_set():
            return
        print("[eth-cyclic] STOP")
        self._active.clear()

    def _cyclic_loop(self) -> None:
        while self._active.is_set():
            with self._payload_lock:
                udp_data = bytes(self._udp_payload)

            eth_payload = _build_ipv4_udp(udp_data)
            ifaces = self._get_ifaces()
            if not ifaces:
                print("[eth-cyclic] no Ethernet interfaces registered, skipping send")
            for iface in ifaces:
                ok = self._eth.send(
                    ethertype=_ETHERTYPE,
                    payload=eth_payload,
                    iface=iface,
                    src_mac=_SRC_MAC,
                    dst_mac=_DST_MAC,
                )
                if not ok:
                    print(f"[eth-cyclic] send failed on {iface} (gateway unreachable?)")

            # Interruptible sleep
            deadline = time.monotonic() + _CYCLE_S
            while self._active.is_set() and time.monotonic() < deadline:
                time.sleep(0.05)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def run(self) -> None:
        self._bus.run_background(names=[_BUS_SIGNAL])
        super().run()

    def stop(self) -> None:
        self._stop_cyclic()
        self._bus.stop()
        super().stop()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="BoAt Ethernet cyclic sender node")
    parser.add_argument("--address", default="localhost:50051")
    args = parser.parse_args()

    node = EthCyclicSenderNode(address=args.address, iface_filter=_CAN_IFACE)

    def _shutdown(sig, frame) -> None:  # noqa: ANN001
        print("\n[eth-cyclic] shutting down…")
        node.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"[eth-cyclic] connected to {args.address}")
    print(f"[eth-cyclic] listening on {_CAN_IFACE} — "
          f"0x{_START_ID:02X} starts, 0x{_STOP_ID:02X} stops")
    print(f"[eth-cyclic] UDP {_SRC_IP}:{_SRC_PORT} → {_DST_IP}:{_DST_PORT}"
          f"  initial payload={_INITIAL_DATA.hex(':')}")
    node.run()


if __name__ == "__main__":
    main()
