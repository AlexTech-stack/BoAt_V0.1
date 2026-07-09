"""Always-on node signal bus — publish and subscribe to named typed signals.

Independent of simulation lifecycle.  Any running node can publish or subscribe
at any time.

Example::

    class MyNode(BusNode):
        def on_signal(self, signal) -> None:
            print(signal.name, signal.number_value)

    node = MyNode(node_id="my-node")
    node.run(names=["vehicle.speed", "engine.rpm"])

Publishing from anywhere::

    node = BusNode(node_id="sender")
    node.publish("vehicle.speed", 120.5)
    node.publish("status",        "active")
    node.publish("flag",          True)
    node.publish("raw",           bytes([0xDE, 0xAD]))
"""

from __future__ import annotations

import threading
from typing import Any

import grpc

from boat.client import BoAtClient
from boat.v1 import bus_pb2


class BusNode:
    """Publish to and subscribe from the BoAt always-on signal bus.

    Args:
        address: Gateway gRPC address (host:port).
        node_id: Optional identifier stamped on every published signal.
    """

    def __init__(
        self,
        address: str = "localhost:50051",
        node_id: str = "",
    ) -> None:
        self._client = BoAtClient(address)
        self._node_id = node_id
        self._stream: Any = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Override in subclass
    # ------------------------------------------------------------------

    def on_signal(self, signal: Any) -> None:  # noqa: B027
        """Called for every received bus signal.  Override in subclass."""

    # ------------------------------------------------------------------
    # Publish helpers
    # ------------------------------------------------------------------

    def publish(self, name: str, value: float | int | str | bool | bytes,
                publisher: str = "") -> bool:
        """Publish a named signal to the bus.

        The value type is inferred automatically:
          - bool   → bool_value   (check before int/float — bool is subclass of int)
          - int / float → number_value
          - str         → string_value
          - bytes       → bytes_value

        Returns True if the gateway accepted the signal.
        """
        sig = bus_pb2.BusSignal(name=name, publisher=publisher or self._node_id)
        if isinstance(value, bool):
            sig.bool_value = value
        elif isinstance(value, (int, float)):
            sig.number_value = float(value)
        elif isinstance(value, str):
            sig.string_value = value
        elif isinstance(value, (bytes, bytearray)):
            sig.bytes_value = bytes(value)
        else:
            raise TypeError(f"Unsupported value type: {type(value)}")
        try:
            resp = self._client.bus.Publish(bus_pb2.BusPublishRequest(signal=sig))
            return bool(resp.accepted)
        except grpc.RpcError:
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self, names: list[str] | None = None) -> None:
        """Subscribe to bus signals and block until stop() is called.

        Args:
            names: Signal names to subscribe to.  None / empty = all signals.
        """
        self._stop_event.clear()
        self._stream = self._client.bus.Subscribe(
            bus_pb2.BusSubscribeRequest(names=names or [])
        )
        try:
            for signal in self._stream:
                if self._stop_event.is_set():
                    break
                self.on_signal(signal)
        except grpc.RpcError:
            pass
        finally:
            self._stream.cancel()
            self._client.close()

    def run_background(self, names: list[str] | None = None) -> threading.Thread:
        """Start in a daemon thread.  Returns the thread."""
        thread = threading.Thread(target=lambda: self.run(names), daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        """Signal the node to stop after the current message."""
        self._stop_event.set()
        if self._stream is not None:
            self._stream.cancel()
