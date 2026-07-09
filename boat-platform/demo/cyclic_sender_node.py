#!/usr/bin/env python3
"""Cyclic sender node.

Behaviour:
  - Listens on vcan0 for 0x111 (any payload) → starts sending 0x300 every 1 s on vcan0.
  - Listens on vcan0 for 0x112 (any payload) → stops cyclic sending.
  - Receiving 0x111 while already sending is a no-op (keeps running).
  - Receiving 0x112 while not sending is a no-op.
  - Bus signal ``cyclic_sender.payload`` (bytes) updates the outgoing payload at any time.

Usage:
    python3 nodes/cyclic_sender_node.py [--address localhost:50051]
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time

from boat.bus_node import BusNode
from boat.can_node import CanNode

_START_ID     = 0x111
_STOP_ID      = 0x112
_CYCLIC_ID    = 0x300
_CYCLIC_DATA  = bytes([0x01, 0x02])
_CYCLIC_IFACE = "vcan0"
_CYCLE_S      = 1.0
_BUS_SIGNAL   = "cyclic_sender.payload"


class _PayloadListener(BusNode):
    """Background bus subscriber that forwards payload updates via a callback."""

    def __init__(self, address: str, on_payload) -> None:
        super().__init__(address=address, node_id="cyclic-sender-bus")
        self._on_payload = on_payload

    def on_signal(self, signal) -> None:
        if signal.name == _BUS_SIGNAL and signal.WhichOneof("value") == "bytes_value":
            self._on_payload(signal.bytes_value)


class CyclicSenderNode(CanNode):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._active = threading.Event()   # set = cyclic sending ON
        self._cyclic_thread: threading.Thread | None = None
        self._payload_lock = threading.Lock()
        self._payload = bytearray(_CYCLIC_DATA)
        address = kwargs.get("address", "localhost:50051")
        self._bus = _PayloadListener(address=address, on_payload=self._update_payload)

    def _update_payload(self, data: bytes) -> None:
        with self._payload_lock:
            self._payload = bytearray(data)
        print(f"[cyclic] payload updated → {data.hex(':')}")

    def on_frame(self, frame, iface: str) -> None:
        if frame.can_id == _START_ID:
            self._start_cyclic()
        elif frame.can_id == _STOP_ID:
            self._stop_cyclic()

    # ── cyclic control ──────────────────────────────────────────────────────

    def _start_cyclic(self) -> None:
        if self._active.is_set():
            return  # already running
        with self._payload_lock:
            payload_hex = self._payload.hex(":")
        print(f"[cyclic] START — sending 0x{_CYCLIC_ID:03X} every {_CYCLE_S} s on {_CYCLIC_IFACE}  payload={payload_hex}")
        self._active.set()
        self._cyclic_thread = threading.Thread(
            target=self._cyclic_loop, daemon=True, name="cyclic"
        )
        self._cyclic_thread.start()

    def _stop_cyclic(self) -> None:
        if not self._active.is_set():
            return  # already stopped
        print("[cyclic] STOP")
        self._active.clear()

    def _cyclic_loop(self) -> None:
        while self._active.is_set():
            with self._payload_lock:
                data = bytes(self._payload)
            self.send(_CYCLIC_ID, data, iface=_CYCLIC_IFACE)
            # Sleep in small increments so stop is responsive
            deadline = time.monotonic() + _CYCLE_S
            while self._active.is_set() and time.monotonic() < deadline:
                time.sleep(0.05)

    def run(self) -> None:
        self._bus.run_background(names=[_BUS_SIGNAL])
        super().run()

    def stop(self) -> None:
        self._stop_cyclic()
        self._bus.stop()
        super().stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="BoAt cyclic sender node")
    parser.add_argument("--address", default="localhost:50051")
    args = parser.parse_args()

    node = CyclicSenderNode(address=args.address, iface_filter="vcan0")

    def _shutdown(sig, frame):
        print("\n[cyclic] shutting down…")
        node.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"[cyclic] listening on vcan0 — 0x{_START_ID:03X} starts, 0x{_STOP_ID:03X} stops")
    node.run()


if __name__ == "__main__":
    main()
