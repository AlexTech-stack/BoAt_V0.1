"""Message instance with signal packing.

A Message is created from a PDU database entry and holds the current
physical signal values.  Call pack() to produce the raw byte payload
ready for transmission.

Signal packing supports Intel (little-endian) and Motorola (big-endian)
bit layouts as used in CAN databases.

Physical → raw conversion:  raw = round((physical - Offset) / Factor)
Raw → physical conversion:  physical = raw * Factor + Offset
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional


# ── bit-packing helpers ───────────────────────────────────────────────────────

def _pack_intel(buf: bytearray, start_bit: int, length: int, raw: int) -> None:
    """Write `raw` (unsigned, `length` bits) at Intel start_bit into buf."""
    mask = (1 << length) - 1
    raw  = int(raw) & mask
    bit  = start_bit
    rem  = length
    while rem > 0:
        byte_idx    = bit // 8
        bit_in_byte = bit % 8
        chunk       = min(8 - bit_in_byte, rem)
        buf[byte_idx] |= (raw & ((1 << chunk) - 1)) << bit_in_byte
        raw >>= chunk
        bit += chunk
        rem -= chunk


def _pack_motorola(buf: bytearray, start_bit: int, length: int, raw: int) -> None:
    """Write `raw` (unsigned, `length` bits) at Motorola MSB start_bit into buf.

    Motorola start_bit = MSB position using the Vector/CANdb++ bit numbering:
      byte_index = start_bit // 8, bit_in_byte = 7 - (start_bit % 8).
    Bits continue downward (wrapping to the next byte's MSB when the byte
    boundary is crossed).
    """
    mask = (1 << length) - 1
    raw  = int(raw) & mask

    # Build list of (byte_index, bit_in_byte) for each bit, MSB first.
    positions = []
    sb        = start_bit
    for _ in range(length):
        byte_idx    = sb // 8
        bit_in_byte = 7 - (sb % 8)
        positions.append((byte_idx, bit_in_byte))
        # Advance to next bit in Motorola order.
        if (sb % 8) == 0:
            sb += 15          # jump to MSB of next byte
        else:
            sb -= 1

    for i, (byte_idx, bit_in_byte) in enumerate(positions):
        bit_val = (raw >> (length - 1 - i)) & 1
        buf[byte_idx] |= bit_val << bit_in_byte


def _pack_signal(buf: bytearray, sig: dict, raw: int) -> None:
    if sig["ByteOrder"] == 0:
        _pack_intel(buf, sig["StartPos"], sig["Length"], raw)
    else:
        _pack_motorola(buf, sig["StartPos"], sig["Length"], raw)


# ── Message ───────────────────────────────────────────────────────────────────

class Message:
    """Live message instance backed by a PDU database entry.

    Args:
        db_entry: A message dict from PduDatabase (the raw JSON object).
    """

    def __init__(self, db_entry: dict) -> None:
        self._db       = db_entry
        self._values:  Dict[str, float] = {}
        self._sigs:    Dict[str, dict]  = {}
        self._muxor: Optional[str] = None  # name of the multiplexor signal, if any

        for sig in db_entry.get("signals", []):
            name = sig["SignalName"]
            self._sigs[name]   = sig
            self._values[name] = float(sig["InitValue"])
            if sig.get("IsMuxor", False):
                self._muxor = name

    # ------------------------------------------------------------------
    # Properties

    @property
    def name(self) -> str:
        return self._db["MessageName"]

    @property
    def db_id(self) -> int:
        return self._db["DbId"]

    @property
    def bus_type(self) -> str:
        return self._db["BusType"]

    @property
    def bus(self) -> str:
        return self._db["Bus"]

    @property
    def length(self) -> int:
        return self._db["Length"]

    @property
    def db(self) -> dict:
        return self._db

    # ------------------------------------------------------------------
    # Signal access

    def set(self, signal_name: str, physical_value: float) -> None:
        """Set a signal by physical value.  Raises KeyError if name unknown."""
        sig = self._sigs.get(signal_name)
        if sig is None:
            raise KeyError(
                f"Signal '{signal_name}' not found in message '{self.name}'. "
                f"Available: {list(self._sigs)}"
            )
        self._values[signal_name] = float(physical_value)

    def get(self, signal_name: str) -> float:
        """Return current physical value of a signal."""
        if signal_name not in self._values:
            raise KeyError(f"Signal '{signal_name}' not found in '{self.name}'")
        return self._values[signal_name]

    def signal_names(self) -> list:
        return list(self._sigs.keys())

    # ------------------------------------------------------------------
    # Packing

    def pack(self) -> bytes:
        """Pack signal values into a raw byte payload, respecting multiplexing.

        If the message has a multiplexor signal (IsMuxor=true), only static
        signals (no MuxValue) and signals whose MuxValue matches the
        multiplexor's current value are packed.

        Returns:
            bytes of length self.length, ready for transmission.
        """
        # Determine active mux group.
        active_mux: Optional[int] = None
        if self._muxor is not None:
            active_mux = int(round(self._values[self._muxor]))

        buf = bytearray(self.length)
        for name, sig in self._sigs.items():
            # Skip signals belonging to a non-active mux group.
            mv = sig.get("MuxValue")
            if mv is not None and active_mux is not None and mv != active_mux:
                continue
            phys  = self._values[name]
            # physical → raw
            factor = sig["Factor"] if sig["Factor"] != 0 else 1.0
            raw    = round((phys - sig["Offset"]) / factor)
            # clamp to unsigned bit range
            max_raw = (1 << sig["Length"]) - 1
            raw = max(0, min(raw, max_raw))
            _pack_signal(buf, sig, raw)
        return bytes(buf)

    # ------------------------------------------------------------------
    # Display

    def __repr__(self) -> str:
        mux_tag = f" muxor={self._muxor}" if self._muxor else ""
        lines = [f"<Message '{self.name}' DbId={self.db_id} BusType={self.bus_type}{mux_tag}>"]
        for name, sig in self._sigs.items():
            phys = self._values[name]
            unit = sig.get("Unit", "")
            raw  = round((phys - sig["Offset"]) / (sig["Factor"] or 1.0))
            mux_info = ""
            mv = sig.get("MuxValue")
            if mv is not None:
                mux_info = f" [mux={mv}]"
            lines.append(f"  .{name:30s} = {phys:10g} {unit}{mux_info}  (raw={raw})")
        return "\n".join(lines)
