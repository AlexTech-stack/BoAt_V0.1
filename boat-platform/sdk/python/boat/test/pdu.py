from __future__ import annotations

from typing import Any, Optional

from boat.message import Message
from boat.pdu_db import PduDatabase


# ── Low-level bit unpacking ───────────────────────────────────────────────────

def _unpack_intel(data: bytes, start_bit: int, length: int) -> int:
    """Extract `length` bits at Intel start_bit from data."""
    raw = 0
    bit = start_bit + length - 1
    for i in range(length):
        byte_idx = bit // 8
        bit_in_byte = bit % 8
        raw = (raw << 1) | ((data[byte_idx] >> bit_in_byte) & 1)
        bit -= 1
    return raw


def _unpack_motorola(data: bytes, start_bit: int, length: int) -> int:
    """Extract `length` bits at Motorola MSB start_bit from data."""
    raw = 0
    sb = start_bit
    for _ in range(length):
        byte_idx = sb // 8
        bit_in_byte = 7 - (sb % 8)
        raw = (raw << 1) | ((data[byte_idx] >> bit_in_byte) & 1)
        if sb % 8 == 0:
            sb += 15
        else:
            sb -= 1
    return raw


def _unpack_signal(data: bytes, sig: dict) -> int:
    if sig["ByteOrder"] == 0:
        return _unpack_intel(data, sig["StartPos"], sig["Length"])
    return _unpack_motorola(data, sig["StartPos"], sig["Length"])


def _raw_to_physical(raw: int, sig: dict) -> float:
    factor = sig["Factor"] if sig["Factor"] != 0 else 1.0
    return float(raw) * factor + sig["Offset"]


def unpack_message(data: bytes, msg: Message) -> dict[str, float]:
    """Unpack raw CAN/Ethernet payload bytes into signal values.

    Respects multiplexing: static signals and the muxor are decoded first,
    then only signals whose MuxValue matches the muxor's decoded value.

    Args:
        data: Raw payload bytes (e.g., from a CAN frame).
        msg:  Message instance whose signal definitions will be used.

    Returns:
        A dict mapping ``signal_name`` → ``physical_value``.
    """
    if len(data) < msg.length:
        data = data + b'\x00' * (msg.length - len(data))
    payload = data[:msg.length]

    # First pass: static signals + muxor.
    values: dict[str, float] = {}
    active_mux: Optional[int] = None
    for name, sig in msg._sigs.items():
        mv = sig.get("MuxValue")
        if mv is not None:
            continue  # skip dynamic signals for now
        raw = _unpack_signal(payload, sig)
        phys = _raw_to_physical(raw, sig)
        values[name] = phys
        if sig.get("IsMuxor", False):
            active_mux = int(round(phys))

    # Second pass: dynamic signals matching the active mux group.
    if active_mux is not None:
        for name, sig in msg._sigs.items():
            mv = sig.get("MuxValue")
            if mv is None or mv != active_mux:
                continue
            raw = _unpack_signal(payload, sig)
            phys = _raw_to_physical(raw, sig)
            values[name] = phys

    return values


# ── PduHelper ────────────────────────────────────────────────────────────────

class PduHelper:
    """High-level PDU helper bridging the test framework and PDU database.

    Usage::

        helper = PduHelper("config/pdu_db_test.json")
        msg = helper.get_message("Motor_1")
        payload = helper.pack("Motor_1", {"MotorSpeed": 1500.0})
        values = helper.unpack("Motor_1", raw_bytes)
    """

    def __init__(self, db_path: str) -> None:
        self._db = PduDatabase(db_path)

    def get_message(self, msg_name: str, bus: Optional[str] = None) -> Message:
        """Look up a message by name.

        Args:
            msg_name: Symbolic message name (e.g. ``"Motor_1"``).
            bus:      Optional bus filter for messages that appear on multiple buses.

        Returns:
            A ``Message`` instance.

        Raises:
            KeyError: If the message name is not found.
        """
        if bus:
            entry = self._db.by_name_and_bus(msg_name, bus)
        else:
            entries = self._db.by_name(msg_name)
            entry = entries[0] if entries else None
        if entry is None:
            raise KeyError(f"Message '{msg_name}' not found in PDU database")
        return Message(entry)

    def get_can_id(self, msg_name: str, bus: Optional[str] = None) -> int:
        """Get the CAN ID for a message."""
        msg = self.get_message(msg_name, bus)
        return msg.db.get("Identifier", 0)

    def pack(self, msg_name: str, signals: dict[str, float],
             bus: Optional[str] = None) -> bytes:
        """Pack signal values into a raw CAN payload.

        Args:
            msg_name: Symbolic message name.
            signals:  Dict of ``{signal_name: physical_value}``.
            bus:      Optional bus filter.

        Returns:
            Packed payload bytes.
        """
        msg = self.get_message(msg_name, bus)
        for name, val in signals.items():
            msg.set(name, val)
        return msg.pack()

    def unpack(self, msg_name: str, data: bytes,
               bus: Optional[str] = None) -> dict[str, float]:
        """Unpack raw payload bytes into signal values.

        Args:
            msg_name: Symbolic message name.
            data:     Raw payload bytes.
            bus:      Optional bus filter.

        Returns:
            Dict of ``{signal_name: physical_value}``.
        """
        msg = self.get_message(msg_name, bus)
        return unpack_message(data, msg)

    def lookup_can_id(self, msg_name: str, bus: Optional[str] = None) -> int:
        """Shortcut to get the CAN identifier for a message."""
        return self.get_can_id(msg_name, bus)

    @property
    def db(self) -> PduDatabase:
        return self._db
