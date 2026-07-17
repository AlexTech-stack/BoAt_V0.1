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
    # Other channel(s) this same arbitration ID was also observed on, with
    # their frame counts -- populated only when _resolve_multi_channel_ids()
    # decided this channel is the original source and the others are
    # gateway/relay duplicates excluded from cycle time and signal analysis.
    duplicate_channels: dict[int, int] = field(default_factory=dict)


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
        """Read the trace file and compute per-ID CAN statistics.

        Supports ``.blf``/``.asc`` (via python-can -- the same
        ``BLFReader``/``ASCReader`` classes ``trace_replay.py`` already uses
        for the same purpose) and ``.trace`` (the gateway's own binary
        format, via :meth:`TraceReplayer.parse_binary`). ``.trace`` files
        may contain non-CAN frames (Ethernet/TCP/PDU); those are counted and
        reported in ``analysis.errors``, not analyzed -- this tool is
        CAN-focused. ``.pcap`` (Ethernet-only) is rejected outright.

        A CAN ID observed on more than one channel is first tracked
        per-channel, then collapsed to a single entry via
        :meth:`_resolve_multi_channel_ids` -- see its docstring for why.
        """
        suffix = self._path.suffix.lower()
        analysis = TraceAnalysis(path=str(self._path))
        per_channel_stats: dict[tuple[int, int], CanIdStats] = {}

        if suffix in (".blf", ".asc"):
            self._read_python_can(suffix, analysis, per_channel_stats)
        elif suffix == ".trace":
            self._read_trace_binary(analysis, per_channel_stats)
        elif suffix == ".pcap":
            raise ValueError(
                ".pcap captures are Ethernet-only and not analyzed by this CAN-focused tool"
            )
        else:
            raise ValueError(f"Unsupported format: {suffix} (expected .blf, .asc, or .trace)")

        analysis.can_stats = self._resolve_multi_channel_ids(per_channel_stats, analysis)
        analysis.unique_ids = len(analysis.can_stats)
        self._analysis = analysis

        self._detect_cycle_times(analysis)
        self._compute_bit_liveness(analysis)
        return analysis

    def _read_python_can(
        self, suffix: str, analysis: TraceAnalysis, stats: dict[tuple[int, int], CanIdStats]
    ) -> None:
        import can as python_can

        reader_cls = python_can.BLFReader if suffix == ".blf" else python_can.ASCReader
        reader = reader_cls(str(self._path))
        with reader:
            for msg in reader:
                analysis.total_frames += 1
                aid = msg.arbitration_id
                ch = getattr(msg, "channel", 1) or 1
                key = (aid, ch)
                if key not in stats:
                    stats[key] = CanIdStats(
                        channel=ch,
                        arbitration_id=aid,
                        is_extended=getattr(msg, "is_extended_id", False),
                        is_fd=getattr(msg, "is_fd", False),
                    )
                s = stats[key]
                s.count += 1
                s.dlc_values.append(len(msg.data))
                s.payload_samples.append(bytes(msg.data))
                s.timestamps.append(msg.timestamp)
                analysis.channels.add(ch)

    def _read_trace_binary(
        self, analysis: TraceAnalysis, stats: dict[tuple[int, int], CanIdStats]
    ) -> None:
        from boat.trace_replay import TraceReplayer
        from boat.v1 import frame_pb2

        frames = TraceReplayer.parse_binary(self._path.read_bytes())
        skipped = 0
        for frame in frames:
            analysis.total_frames += 1
            if frame.bus_type not in (frame_pb2.Frame.CAN, frame_pb2.Frame.CANFD):
                skipped += 1
                continue
            aid = frame.can.can_id
            ch = frame.can.channel or 1
            key = (aid, ch)
            if key not in stats:
                stats[key] = CanIdStats(
                    channel=ch,
                    arbitration_id=aid,
                    is_extended=aid > 0x7FF,
                    is_fd=frame.bus_type == frame_pb2.Frame.CANFD,
                )
            s = stats[key]
            s.count += 1
            s.dlc_values.append(len(frame.payload))
            s.payload_samples.append(bytes(frame.payload))
            s.timestamps.append(frame.timestamp_ns / 1e9)  # ns -> seconds, matches python-can's convention
            analysis.channels.add(ch)
        if skipped:
            analysis.errors.append(
                f"skipped {skipped} non-CAN frame(s) (ETHERNET/TCP/PDU) -- not analyzed by this tool"
            )

    # ── Multi-channel duplicate resolution ──────────────────────────────

    @staticmethod
    def _resolve_multi_channel_ids(
        per_channel_stats: dict[tuple[int, int], CanIdStats],
        analysis: TraceAnalysis,
    ) -> dict[int, CanIdStats]:
        """Collapse per-(ID, channel) stats down to one entry per CAN ID.

        A CAN ID observed on only one channel passes through unchanged. An
        ID observed on multiple channels is assumed to be the same logical
        message relayed across buses (e.g. by a gateway ECU, sometimes at a
        slower or delayed cycle) -- :meth:`_select_original_channel` picks
        whichever channel's payload changes *lead* the others' as the
        original source, and only that channel's data is used for cycle
        time detection and signal reverse-engineering. The other channel(s)
        are recorded on the winner's `duplicate_channels` and reported as a
        warning, not silently merged or silently dropped.
        """
        by_id: dict[int, dict[int, CanIdStats]] = defaultdict(dict)
        for (aid, ch), s in per_channel_stats.items():
            by_id[aid][ch] = s

        resolved: dict[int, CanIdStats] = {}
        duplicate_notes: list[str] = []
        for aid, candidates in by_id.items():
            if len(candidates) == 1:
                ch, s = next(iter(candidates.items()))
                resolved[aid] = s
                continue

            winner_ch, winner_stats = TraceAnalyzer._select_original_channel(candidates)
            winner_stats.duplicate_channels = {
                ch: s.count for ch, s in candidates.items() if ch != winner_ch
            }
            resolved[aid] = winner_stats
            duplicate_notes.append(f"0x{aid:X} (channel {winner_ch} selected)")

        if duplicate_notes:
            preview = ", ".join(duplicate_notes[:10])
            more = f", and {len(duplicate_notes) - 10} more" if len(duplicate_notes) > 10 else ""
            analysis.errors.append(
                f"{len(duplicate_notes)} CAN ID(s) seen on multiple channels -- only the "
                f"apparent original channel was used for cycle time / signal analysis for "
                f"each, the rest were treated as relay duplicates and ignored: {preview}{more}"
            )

        return resolved

    @staticmethod
    def _select_original_channel(
        candidates: dict[int, CanIdStats],
    ) -> tuple[int, CanIdStats]:
        """Among several channels carrying the same CAN ID, pick the one
        whose payload changes lead the others' -- the presumed original
        source, with the rest being a gateway/relay forwarding the same
        signal.

        For every payload value that changes on more than one channel,
        whichever channel's timestamp for that value is earliest scores a
        "lead" point against the others; the channel with the most lead
        points wins. If no comparable transitions exist at all (e.g. the
        channels' value sets never overlap, or every channel is constant),
        falls back to whichever channel has the most distinct value
        changes, then to the lowest channel number for determinism.
        """
        # Per-channel change events: (timestamp, payload) whenever payload
        # differs from the previous frame *on that channel*.
        change_events: dict[int, list[tuple[float, bytes]]] = {}
        for ch, s in candidates.items():
            events: list[tuple[float, bytes]] = []
            prev: bytes | None = None
            for ts, payload in zip(s.timestamps, s.payload_samples):
                if payload != prev:
                    events.append((ts, payload))
                    prev = payload
            change_events[ch] = events

        # Earliest time each payload value appeared as a change, per channel.
        first_seen: dict[bytes, dict[int, float]] = defaultdict(dict)
        for ch, events in change_events.items():
            for ts, payload in events:
                if ch not in first_seen[payload] or ts < first_seen[payload][ch]:
                    first_seen[payload][ch] = ts

        lead_score: dict[int, int] = {ch: 0 for ch in candidates}
        for payload, per_channel_ts in first_seen.items():
            if len(per_channel_ts) < 2:
                continue  # this value only appeared as a change on one channel -- no comparison possible
            leader = min(per_channel_ts, key=per_channel_ts.get)
            lead_score[leader] += 1

        if any(lead_score.values()):
            winner = max(candidates, key=lambda ch: (lead_score[ch], len(change_events[ch]), -ch))
        else:
            winner = max(candidates, key=lambda ch: (len(change_events[ch]), -ch))

        return winner, candidates[winner]

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
                analysis.cycle_times_ms[aid] = TraceAnalyzer._snap_to_canonical_cycle_time(median_gap)

    # Standard automotive scheduling raster values. A raw inter-frame gap is
    # noisy (arbitration delays, bus load, timer granularity), so a message
    # actually intended to run at e.g. 50ms typically measures as something
    # like 49.8 or 50.1ms in a real capture -- reporting that raw number
    # instead of the raster it's clearly jittering around is misleading.
    _CANONICAL_CYCLE_TIMES_MS = (
        1, 2, 5, 10, 20, 25, 40, 50, 80, 100, 200, 250, 400, 450, 500, 1000, 2000, 5000,
    )

    @staticmethod
    def _snap_to_canonical_cycle_time(value_ms: float, tolerance: float = 0.1) -> float:
        """Snap to the nearest standard cycle time if within `tolerance`
        (10% by default) of it; otherwise report the raw measured value
        unchanged, since forcing an unrelated gap onto a raster would be
        just as misleading as reporting jitter as if it were exact."""
        closest = min(TraceAnalyzer._CANONICAL_CYCLE_TIMES_MS, key=lambda c: abs(c - value_ms))
        if abs(closest - value_ms) <= closest * tolerance:
            return float(closest)
        return round(value_ms, 1)

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
