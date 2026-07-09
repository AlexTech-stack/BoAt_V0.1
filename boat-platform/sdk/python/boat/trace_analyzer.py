"""BLF trace analyzer — reads BLF files and derives PDU database skeletons.

Usage::

    from boat.trace_analyzer import TraceAnalyzer

    analyzer = TraceAnalyzer("recordings/capture.blf")
    analyzer.analyze()

    pdu_db = analyzer.to_pdu_db(
        bus_mapping={1: "Powertrain_CAN", 2: "Body_CAN"},
        message_names={0x123: "EngineSpeed", 0x456: "CoolantTemp"},
    )

    import json
    print(json.dumps(pdu_db, indent=2))
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CanIdStats:
    """Statistics for a single CAN ID observed in a trace."""
    channel: int
    arbitration_id: int
    is_extended: bool
    is_fd: bool
    count: int = 0
    dlc_values: list[int] = field(default_factory=list)
    payload_samples: list[bytes] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)
    bit_changes: list[set[int]] = field(default_factory=list)


@dataclass
class TraceAnalysis:
    """Result of analyzing a BLF trace file."""
    path: str
    total_frames: int = 0
    unique_ids: int = 0
    channels: set[int] = field(default_factory=set)
    can_stats: dict[int, CanIdStats] = field(default_factory=dict)
    cycle_times_ms: dict[int, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class TraceAnalyzer:
    """Analyze a BLF trace file and produce PDU database skeletons."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._analysis: TraceAnalysis | None = None

    def analyze(self) -> TraceAnalysis:
        """Read the BLF file and compute per-ID statistics."""
        import can as python_can

        analysis = TraceAnalysis(path=str(self._path))
        stats: dict[int, CanIdStats] = {}

        reader = python_can.BLFReader(str(self._path))
        with reader:
            for msg in reader:
                analysis.total_frames += 1
                aid = msg.arbitration_id
                ch = getattr(msg, "channel", 1) or 1
                if aid not in stats:
                    stats[aid] = CanIdStats(
                        channel=ch,
                        arbitration_id=aid,
                        is_extended=getattr(msg, "is_extended_id", False),
                        is_fd=getattr(msg, "is_fd", False),
                    )
                s = stats[aid]
                s.count += 1
                s.dlc_values.append(len(msg.data))
                s.payload_samples.append(bytes(msg.data))
                s.timestamps.append(msg.timestamp)
                analysis.channels.add(ch)

        analysis.can_stats = stats
        analysis.unique_ids = len(stats)
        self._analysis = analysis

        self._detect_cycle_times(analysis)
        self._compute_bit_liveness(analysis)
        return analysis

    @staticmethod
    def _detect_cycle_times(analysis: TraceAnalysis) -> None:
        """Detect periodic messages by analyzing inter-message gaps."""
        for aid, s in analysis.can_stats.items():
            if s.count < 3:
                continue
            gaps = []
            for i in range(1, len(s.timestamps)):
                gap = (s.timestamps[i] - s.timestamps[i - 1]) * 1000.0
                if gap > 0:
                    gaps.append(gap)
            if not gaps:
                continue

            median_gap = statistics.median(gaps)
            mad = statistics.median(abs(g - median_gap) for g in gaps)
            if mad < median_gap * 0.3:
                analysis.cycle_times_ms[aid] = round(median_gap, 1)

    @staticmethod
    def _compute_bit_liveness(analysis: TraceAnalysis) -> None:
        """Track which bit positions ever change per CAN ID."""
        for aid, s in analysis.can_stats.items():
            if s.count < 2:
                continue
            max_len = max(len(p) for p in s.payload_samples)
            changed = [set() for _ in range(max_len)]
            prev_bytes = [None] * max_len
            for payload in s.payload_samples:
                for byte_idx in range(len(payload)):
                    b = payload[byte_idx]
                    if prev_bytes[byte_idx] is not None and b != prev_bytes[byte_idx]:
                        diff = b ^ prev_bytes[byte_idx]
                        for bit in range(8):
                            if diff & (1 << bit):
                                changed[byte_idx].add(bit)
                    prev_bytes[byte_idx] = b
            s.bit_changes = changed

    # ── PDU Database generation ─────────────────────────────────────────

    def to_pdu_db(
        self,
        bus_mapping: dict[int, str] | None = None,
        message_names: dict[int, str] | None = None,
        include_signals: bool = False,
    ) -> dict:
        """Derive a complete PDU database JSON dict from the trace analysis.

        Args:
            bus_mapping:  Map BLF channel number → bus name.
            message_names: Map CAN arbitration ID → message name.
            include_signals: If True, attempt signal discovery (requires
                           numpy and is experimental).

        Returns:
            A dict matching the PDU database schema (schema_version 1.0).
        """
        if self._analysis is None:
            raise RuntimeError("Call analyze() before to_pdu_db()")

        bus_mapping = bus_mapping or {}
        message_names = message_names or {}
        analysis = self._analysis

        messages: list[dict] = []
        next_db_id = 1

        for aid in sorted(analysis.can_stats.keys()):
            s = analysis.can_stats[aid]
            max_dlc = max(s.dlc_values) if s.dlc_values else 8

            msg: dict[str, Any] = {
                "DbId": next_db_id,
                "MessageName": message_names.get(aid, f"Msg_0x{aid:X}"),
                "Bus": bus_mapping.get(s.channel, f"CAN_{s.channel}"),
                "BusType": "CANFD" if s.is_fd else "CAN",
                "MessageType": 0,
                "Direction": 0,
                "RoutingType": 0,
                "TargetDbIds": None,
                "SourceDbId": None,
                "isE2E": 0,
                "SendType": "Cyclic" if aid in analysis.cycle_times_ms else "Spontaneous",
                "CycleTime": int(analysis.cycle_times_ms.get(aid, 0)),
                "CycleTimeFast": 0,
                "NrOfRepetitions": 0,
                "Identifier": aid & 0x1FFFFFFF,
                "FrameType": 1 if s.is_extended else 0,
                "Length": max_dlc,
                "BRS": s.is_fd,
                "signalcount": 0,
                "signals": [],
            }

            if include_signals:
                signals = self._derive_signals(s)
                msg["signals"] = signals
                msg["signalcount"] = len(signals)

            messages.append(msg)
            next_db_id += 1

        return {
            "schema_version": "1.0",
            "messages": messages,
            "signal_routes": [],
        }

    @staticmethod
    def _derive_signals(s: CanIdStats) -> list[dict]:
        """Basic signal derivation from payload samples (placeholder).

        This is intentionally minimal — the real reverse-engineering
        heuristics live in trace_reverse_engineer.py.
        """
        if not s.payload_samples:
            return []
        max_len = max(len(p) for p in s.payload_samples)
        signals = []
        if s.bit_changes and any(ch for ch in s.bit_changes):
            sig_id = 1
            pos = 0
            for byte_idx in range(max_len):
                changed = s.bit_changes[byte_idx] if byte_idx < len(s.bit_changes) else set()
                if not changed:
                    pos += 8
                    continue
                for bit in range(8):
                    if bit in changed:
                        signals.append({
                            "id": sig_id,
                            "SignalName": f"Signal_{sig_id}",
                            "Length": 1,
                            "StartPos": pos + bit,
                            "ByteOrder": 0,
                            "ValueType": "Unsigned",
                            "SigSendType": False,
                            "Repetitions": 0,
                            "InitValue": 0,
                            "Factor": 1.0,
                            "Offset": 0.0,
                            "Min": 0.0,
                            "Max": 1.0,
                            "Unit": "",
                            "EnumValues": None,
                    "IsMuxor": False,
                    "MuxValue": None,
                    "Comment": "",
                        })
                        sig_id += 1
                pos += 8
        return signals

    def save_pdu_db(self, path: str | Path, **kwargs) -> Path:
        """Analyze and save the derived PDU database directly to a JSON file.

        All extra keyword arguments are forwarded to :meth:`to_pdu_db`.
        """
        pdu_db = self.to_pdu_db(**kwargs)
        out = Path(path)
        out.write_text(json.dumps(pdu_db, indent=2))
        return out
