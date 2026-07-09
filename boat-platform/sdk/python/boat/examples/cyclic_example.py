"""Example: cyclic-sender-with-trigger.

Listens on ALL interfaces (iface_filter="") for two control frames:
  - ID 0x310 on any interface → start sending ID 0x302 on vcan1 every 500 ms
  - ID 0x311 on any interface → stop sending

Demonstrates:
  - Multi-interface listening (iface_filter="" + filter in on_frame)
  - Cyclic / periodic sending using threading.Timer (stdlib only)
  - Start/stop state driven by received frames
"""
from __future__ import annotations

import threading

from boat.can_node import CanNode


class CyclicSender(CanNode):
    def __init__(self) -> None:
        # iface_filter="" subscribes to ALL registered interfaces at once.
        super().__init__(address="localhost:50051", iface_filter="")
        self._active = False
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Cyclic send logic
    # ------------------------------------------------------------------

    def _send_cyclic(self) -> None:
        """Send one frame then re-schedule if still active."""
        with self._lock:
            if not self._active:
                return
            self.send(can_id=0x302, data=bytes([0x12, 0x34]), iface="vcan1")
            self._timer = threading.Timer(0.5, self._send_cyclic)
            self._timer.daemon = True
            self._timer.start()

    def _start(self) -> None:
        with self._lock:
            if self._active:
                return  # already running
            self._active = True
        self._send_cyclic()

    def _stop(self) -> None:
        with self._lock:
            self._active = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    # ------------------------------------------------------------------
    # Frame handler
    # ------------------------------------------------------------------

    def on_frame(self, frame, iface: str) -> None:
        if frame.can_id == 0x310:
            self._start()
        elif frame.can_id == 0x311:
            self._stop()


if __name__ == "__main__":
    CyclicSender().run()
