from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from typing import Optional

from boat.test.exceptions import TestTimeoutError

_SUBSCRIBE_POLL_S = 0.05


class _FrameStreamReader:
    """Background thread reading from a gRPC stream into a thread-safe queue."""

    def __init__(self, stream_iterator) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._reader, args=(stream_iterator,), daemon=True)
        self._thread.start()

    def _reader(self, stream) -> None:
        try:
            for item in stream:
                if self._stop.is_set():
                    break
                self._queue.put(item)
        except Exception as exc:
            self._queue.put(exc)

    def poll(self, timeout: float = 0) -> list:
        frames: list = []
        deadline = time.monotonic() + timeout if timeout > 0 else None
        first = True
        while True:
            remaining = None
            if deadline is not None:
                remaining = max(0, deadline - time.monotonic())
                if first:
                    first = False
                elif remaining <= 0:
                    break
            try:
                item = self._queue.get(timeout=min(remaining or _SUBSCRIBE_POLL_S, _SUBSCRIBE_POLL_S))
                if isinstance(item, Exception):
                    raise item
                frames.append(item)
            except queue.Empty:
                if deadline is not None and time.monotonic() >= deadline:
                    break
                continue
        return frames

    def close(self) -> None:
        self._stop.set()


class TestCanBus:
    __test__ = False
    """Abstract CAN bus — works identically with virtual or physical hardware.

    Optionally associated with a ``PduHelper`` for symbolic signal access.
    The test harness sets ``.pdu`` automatically when a PDU database is loaded.
    

    Usage::

        can1 = harness.can_bus("can1")
        can1.send(0x100, b'\\x01\\xF4')
        frame = can1.expect(can_id=0x300, timeout_ms=500)
        for frame in can1.subscribe(can_id=0x300):
            print(frame)
    """

    def __init__(self, client, config) -> None:
        self._client = client
        self._config = config
        self._name = config.logical_name
        self._reader: Optional[_FrameStreamReader] = None
        self.pdu = None  # PduHelper reference, set by TestHarness

    @property
    def name(self) -> str:
        return self._name

    @property
    def interface(self) -> str:
        return self._config.interface

    def send(self, can_id: int, data: bytes, flags: int = 0) -> bool:
        """Send a CAN frame. Returns True if the gateway accepted it."""
        from boat.v1 import can_pb2

        frame = can_pb2.CanFrame(
            can_id=can_id,
            dlc=len(data),
            data=data,
            iface=self.interface,
            flags=flags,
        )
        req = can_pb2.SendCanFrameRequest(frame=frame)
        try:
            resp = self._client.can.SendCanFrame(req)
            return resp.accepted
        except Exception as exc:
            raise RuntimeError(f"CAN send failed on {self._name}: {exc}") from exc

    def send_signal(self, msg_name: str, signals: dict,
                    can_id: Optional[int] = None,
                    bus: Optional[str] = None) -> bool:
        """Pack signal values and send as a CAN frame.

        Requires a ``PduHelper`` assigned via ``.pdu`` (set automatically
        when the parent ``TestHarness`` has a PDU database loaded).

        Args:
            msg_name: Symbolic message name from the PDU database.
            signals:  ``{signal_name: physical_value, ...}``.
            can_id:   Optional CAN ID override (uses DB value by default).
            bus:      Optional bus filter for message lookup.

        Returns:
            ``True`` if the gateway accepted the frame.
        """
        if self.pdu is None:
            raise RuntimeError("PduHelper not available — call harness.load_pdu_database()")
        payload = self.pdu.pack(msg_name, signals, bus=bus)
        msg_id = can_id or self.pdu.lookup_can_id(msg_name, bus=bus)
        return self.send(msg_id, payload)

    def expect_signal(self, msg_name: str,
                      signals: Optional[dict] = None,
                      can_id: Optional[int] = None,
                      timeout_ms: int = 1000,
                      bus: Optional[str] = None) -> dict:
        """Receive a CAN frame and unpack its signal values.

        Args:
            msg_name:  Symbolic message name from the PDU database.
            signals:   Optional expected ``{name: value}`` to assert against.
            can_id:    Optional CAN ID override.
            timeout_ms: Maximum wait in milliseconds.
            bus:       Optional bus filter for message lookup.

        Returns:
            ``{signal_name: physical_value, ...}`` from the received frame.

        Raises:
            AssertionError: If expected signals don't match.
        """
        if self.pdu is None:
            raise RuntimeError("PduHelper not available — call harness.load_pdu_database()")
        msg_id = can_id or self.pdu.lookup_can_id(msg_name, bus=bus)
        frame = self.expect(can_id=msg_id, timeout_ms=timeout_ms)
        values = self.pdu.unpack(msg_name, bytes(frame.data), bus=bus)
        if signals:
            for name, expected_val in signals.items():
                actual_val = values.get(name)
                if actual_val is None:
                    raise AssertionError(f"Signal '{name}' not found in unpacked values")
                if abs(actual_val - expected_val) > 1e-9:
                    raise AssertionError(
                        f"Signal '{name}': expected {expected_val}, got {actual_val}"
                    )
        return values

    def expect(
        self,
        can_id: Optional[int] = None,
        data: Optional[bytes] = None,
        mask: Optional[bytes] = None,
        timeout_ms: int = 1000,
    ) -> object:
        """Wait for a matching CAN frame.

        Args:
            can_id: Expected CAN ID (any if None).
            data:   Expected payload bytes (any if None).
            mask:   Bitmask applied to both payload and data before comparison.
            timeout_ms: Maximum wait time in milliseconds.

        Returns:
            The matching ``CanFrame`` protobuf object.

        Raises:
            TestTimeoutError: If no matching frame arrives within the timeout.
        """
        from boat.v1 import can_pb2

        deadline = time.monotonic() + timeout_ms / 1000
        req = can_pb2.SubscribeCanFramesRequest(iface=self.interface)
        try:
            for frame in self._client.can.SubscribeCanFrames(req):
                if time.monotonic() > deadline:
                    break
                if self._matches(frame, can_id, data, mask):
                    return frame
        except Exception as exc:
            raise RuntimeError(f"CAN subscribe error on {self._name}: {exc}") from exc

        raise TestTimeoutError(
            f"No matching CAN frame on {self._name} "
            f"(can_id={hex(can_id) if can_id is not None else 'any'}) "
            f"within {timeout_ms}ms"
        )

    def subscribe(self, can_id: Optional[int] = None) -> Iterator:
        """Continuously yield CAN frames matching the optional filter.

        Operates in a background thread so the caller can iterate without
        blocking indefinitely — use ``next()`` with a timeout or break
        when done.
        """
        from boat.v1 import can_pb2

        if self._reader is None:
            req = can_pb2.SubscribeCanFramesRequest(iface=self.interface)
            self._reader = _FrameStreamReader(self._client.can.SubscribeCanFrames(req))

        while True:
            frames = self._reader.poll(timeout=_SUBSCRIBE_POLL_S)
            for frame in frames:
                if can_id is None or frame.can_id == can_id:
                    yield frame

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    @staticmethod
    def _matches(frame, can_id, data, mask) -> bool:
        if can_id is not None and frame.can_id != can_id:
            return False
        if data is not None:
            fdata = bytes(frame.data)
            edata = bytes(data)
            if mask is not None:
                bmask = bytes(mask)
                minlen = min(len(fdata), len(edata), len(bmask))
                fdata = bytes(fdata[i] & bmask[i] for i in range(minlen))
                edata = bytes(edata[i] & bmask[i] for i in range(minlen))
            else:
                minlen = min(len(fdata), len(edata))
                fdata = fdata[:minlen]
                edata = edata[:minlen]
            if fdata != edata:
                return False
        return True


class TestEthBus:
    __test__ = False
    """Abstract Ethernet bus — works with virtual or physical hardware.

    Usage::

        eth0 = harness.eth_bus("eth0")
        eth0.send(dst_mac=b'\\x00\\x11\\x22\\x33\\x44\\x55', ethertype=0x88B5, payload=b'...')
        frame = eth0.expect(ethertype=0x88B5, timeout_ms=500)
    """

    def __init__(self, client, config) -> None:
        self._client = client
        self._config = config
        self._name = config.logical_name
        self._reader: Optional[_FrameStreamReader] = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def interface(self) -> str:
        return self._config.interface

    def send(
        self,
        dst_mac: bytes,
        src_mac: Optional[bytes] = None,
        ethertype: int = 0x88B5,
        payload: bytes = b"",
        vlan_id: Optional[int] = None,
    ) -> bool:
        from boat.v1 import ethernet_pb2

        frame = ethernet_pb2.EthernetFrame(
            iface=self.interface,
            src_mac=src_mac or b'\x00' * 6,
            dst_mac=dst_mac,
            ethertype=ethertype,
            payload=payload,
        )
        if vlan_id is not None:
            frame.vlan_id = vlan_id
        req = ethernet_pb2.SendEthernetFrameRequest(frame=frame)
        try:
            resp = self._client.ethernet.SendFrame(req)
            return resp.accepted
        except Exception as exc:
            raise RuntimeError(f"Ethernet send failed on {self._name}: {exc}") from exc

    def expect(
        self,
        ethertype: Optional[int] = None,
        timeout_ms: int = 1000,
    ) -> object:
        from boat.v1 import ethernet_pb2

        deadline = time.monotonic() + timeout_ms / 1000
        req = ethernet_pb2.SubscribeEthernetFramesRequest(iface=self.interface)
        try:
            for frame in self._client.ethernet.SubscribeFrames(req):
                if time.monotonic() > deadline:
                    break
                if ethertype is None or frame.ethertype == ethertype:
                    return frame
        except Exception as exc:
            raise RuntimeError(f"Ethernet subscribe error on {self._name}: {exc}") from exc

        raise TestTimeoutError(
            f"No matching Ethernet frame on {self._name} "
            f"(ethertype={hex(ethertype) if ethertype is not None else 'any'}) "
            f"within {timeout_ms}ms"
        )

    def subscribe(self, ethertype: Optional[int] = None) -> Iterator:
        from boat.v1 import ethernet_pb2

        if self._reader is None:
            req = ethernet_pb2.SubscribeEthernetFramesRequest(iface=self.interface)
            self._reader = _FrameStreamReader(self._client.ethernet.SubscribeFrames(req))

        while True:
            frames = self._reader.poll(timeout=_SUBSCRIBE_POLL_S)
            for frame in frames:
                if ethertype is None or frame.ethertype == ethertype:
                    yield frame

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None
