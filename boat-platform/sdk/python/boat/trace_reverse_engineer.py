"""Reverse engineering engine for CAN trace signal discovery.

Builds on :mod:`boat.trace_analyzer` to discover signal boundaries,
value types, scaling factors, enumerations, counters, and checksums
from raw CAN payload observations.

Usage::

    from boat.trace_analyzer import TraceAnalyzer
    from boat.trace_reverse_engineer import TraceReverseEngineer

    analyzer = TraceAnalyzer("trace.blf")
    analyzer.analyze()

    engineer = TraceReverseEngineer(analyzer)
    results = engineer.reverse_engineer()
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from boat.trace_analyzer import CanIdStats, TraceAnalysis, TraceAnalyzer

_HAS_NUMPY = False
try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]


@dataclass
class DiscoveredSignal:
    """A signal discovered through trace reverse engineering."""
    id: int
    name: str
    start_pos: int
    length: int
    byte_order: int
    value_type: str
    factor: float
    offset: float
    min_val: float
    max_val: float
    unit: str
    enum_values: dict[str, str] | None
    is_counter: bool
    is_checksum: bool
    confidence: float
    raw_values: list[int] = field(default_factory=list)
    physical_values: list[float] = field(default_factory=list)
    # Set only when this checksum was identified by the dedicated AUTOSAR CRC
    # scan (find_crcs()) rather than the older, weaker XOR-based heuristic in
    # _detect_checksum() -- crc_algorithm names exactly which of the six
    # AUTOSAR_SWS_CRCLibrary-defined algorithms matched every observed frame.
    crc_algorithm: str | None = None
    crc_data_id: int | None = None


@dataclass
class ReverseEngineeredMessage:
    """A message with reverse-engineered signals."""
    can_id: int
    channel: int
    db_id: int
    message_name: str
    bus: str
    bus_type: str
    identifier: int
    is_extended: bool
    is_fd: bool
    length: int
    cycle_time_ms: float
    send_type: str
    signals: list[DiscoveredSignal] = field(default_factory=list)
    # Best-effort AUTOSAR E2E Profile label (e.g. "E2E_Profile_2"), set only
    # when both a counter and a CRC were found for this message and their
    # combination matches a well-known standard profile definition -- see
    # _E2E_PROFILE_HINTS. A hint, not a verified classification: it isn't
    # checked against the full AUTOSAR E2E protocol spec, only the CRC
    # algorithm parameters themselves are spec-verified.
    e2e_profile: str | None = None


@dataclass
class ReverseEngineeringResult:
    """Full reverse engineering result."""
    messages: list[ReverseEngineeredMessage] = field(default_factory=list)
    total_can_ids: int = 0
    total_signals_discovered: int = 0
    numpy_available: bool = False


# ── AUTOSAR CRC engine ──────────────────────────────────────────────────
#
# Bit-by-bit (MSB-first, no lookup table -- candidate counts here are small
# enough that table-driven speed doesn't matter) implementation of the six
# byte-oriented CRC algorithms defined in AUTOSAR_SWS_CRCLibrary (R22-11).
# Every parameter below is verified against *every* "Check" value (CRC of
# ASCII "123456789") and every full worked test-vector table in the spec --
# 100% match. CRC64 (ECMA) is defined there too but deliberately out of
# scope: a 64-bit field is vanishingly rare in a CAN payload and would
# balloon the brute-force search space for little practical benefit.
_AUTOSAR_CRC_ALGORITHMS: dict[str, dict[str, int | bool]] = {
    "CRC8":     dict(width=8,  poly=0x1D,       init=0xFF,       refin=False, refout=False, xorout=0xFF),
    "CRC8H2F":  dict(width=8,  poly=0x2F,       init=0xFF,       refin=False, refout=False, xorout=0xFF),
    "CRC16":    dict(width=16, poly=0x1021,     init=0xFFFF,     refin=False, refout=False, xorout=0x0000),
    "CRC16ARC": dict(width=16, poly=0x8005,     init=0x0000,     refin=True,  refout=True,  xorout=0x0000),
    "CRC32":    dict(width=32, poly=0x04C11DB7, init=0xFFFFFFFF, refin=True,  refout=True,  xorout=0xFFFFFFFF),
    "CRC32P4":  dict(width=32, poly=0xF4ACFB13, init=0xFFFFFFFF, refin=True,  refout=True,  xorout=0xFFFFFFFF),
}

# Sample size for the cheap first pass (variability prefilter, "no Data ID"
# check, and the bounded Data-ID brute force) before a promising candidate
# is re-verified against the *entire* trace. Both this and the match
# threshold bound the otherwise-expensive Data-ID search to a manageable
# cost per candidate position.
_CRC_SAMPLE_SIZE = 20
_CRC_MATCH_THRESHOLD = 0.95

# AUTOSAR E2E's Data ID is only folded in as "one extra input byte" this
# simply for the 1-byte-wide CRC profiles (Profile 1/2's Crc_CalculateCRC8
# is called a second time with the first result as its start value, over
# the Data ID's low byte -- mathematically identical to appending it to the
# input). Wider CRCs' Data ID handling varies by profile and isn't folded
# in this uniformly, so brute-forcing it here would be both more expensive
# and less trustworthy -- only these two get the brute force.
_CRC_DATA_ID_ALGOS = ("CRC8", "CRC8H2F")


def _reflect(value: int, width: int) -> int:
    """Bit-reverse `value` within `width` bits (CRC refin/refout)."""
    r = 0
    for _ in range(width):
        r = (r << 1) | (value & 1)
        value >>= 1
    return r


def _crc_autosar(data: bytes, algo: str, extra_byte: int | None = None) -> int:
    """Compute the named AUTOSAR CRC algorithm over `data`. `extra_byte`,
    when given, models a 1-byte Data ID folded in after `data` -- see
    _CRC_DATA_ID_ALGOS.
    """
    params = _AUTOSAR_CRC_ALGORITHMS[algo]
    width = int(params["width"])
    poly = int(params["poly"])
    mask = (1 << width) - 1
    top_bit = 1 << (width - 1)
    crc = int(params["init"]) & mask
    payload = data if extra_byte is None else bytes(data) + bytes([extra_byte])
    for byte in payload:
        b = _reflect(byte, 8) if params["refin"] else byte
        crc ^= (b << (width - 8)) & mask
        for _ in range(8):
            if crc & top_bit:
                crc = ((crc << 1) ^ poly) & mask
            else:
                crc = (crc << 1) & mask
    if params["refout"]:
        crc = _reflect(crc, width)
    return (crc ^ int(params["xorout"])) & mask


# Best-effort AUTOSAR E2E Profile hint from (counter_length_bits,
# crc_algorithm) -- NOT verified against the full E2E protocol spec, only
# the CRC algorithm parameters themselves are (_AUTOSAR_CRC_ALGORITHMS).
# Confidence varies by entry: (4, CRC8) and (4, CRC8H2F) are unambiguous,
# single-profile combinations straight from the profile definitions; the
# rest share their CRC algorithm across profiles that differ in ways (exact
# Data ID convention, framing) not distinguishable from a passive capture
# alone, so treat those as plausible hints, not a firm classification.
_E2E_PROFILE_HINTS: dict[tuple[int, str], str] = {
    (4, "CRC8"): "E2E_Profile_1",
    (4, "CRC8H2F"): "E2E_Profile_2",
    (16, "CRC32P4"): "E2E_Profile_4",
    (8, "CRC16"): "E2E_Profile_5",
    (8, "CRC16ARC"): "E2E_Profile_6",
    (8, "CRC32P4"): "E2E_Profile_7",
}

# A message pairing a counter with *some* checksum right next to it is
# itself strong evidence of E2E protection, independent of whether the
# exact profile number could be pinned down -- either the checksum's
# algorithm was identified but the (counter width, algorithm) combination
# isn't one of the standard profiles above, or it was only recognized by
# _looks_like_checksum()'s behavioral fallback (formula unknown entirely).
# Kept distinct from a real "E2E_Profile_N" string so callers (e.g. the
# PDU DB export's `isE2E`, which is a real profile number or 0) can tell
# "protected, profile unknown" apart from an actual identified profile.
E2E_UNKNOWN_PROFILE = "E2E_Unknown"


def guess_e2e_profile(counter: DiscoveredSignal, crc: DiscoveredSignal) -> str:
    """Best-effort AUTOSAR E2E Profile hint for a (counter, CRC/checksum)
    pair found on the same message -- see _E2E_PROFILE_HINTS for per-entry
    confidence caveats. Returns the specific profile name when the
    (counter width, CRC algorithm) combination matches a known profile,
    otherwise E2E_UNKNOWN_PROFILE -- callers already only call this once
    both a counter and a checksum have been found on the same message, so
    there's always *something* to report here.
    """
    if crc.crc_algorithm is not None:
        hint = _E2E_PROFILE_HINTS.get((counter.length, crc.crc_algorithm))
        if hint:
            return hint
    return E2E_UNKNOWN_PROFILE


def _e2e_profile_number(e2e_profile: str | None) -> int:
    """Extract the bare profile number from a "E2E_Profile_N" hint string
    for the PDU DB schema's `isE2E` field (an AUTOSAR E2E profile number,
    or 0 if none) -- 0 if no hint was set or it doesn't parse.
    """
    if not e2e_profile:
        return 0
    suffix = e2e_profile.rsplit("_", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


class TraceReverseEngineer:
    """Reverse-engineer signal definitions from CAN trace data.

    Args:
        analyzer: A :class:`~boat.trace_analyzer.TraceAnalyzer` instance
                  that has already been run via ``analyze()``.
        min_confidence: Minimum confidence (0-1) to include a signal.
    """

    def __init__(
        self,
        analyzer: TraceAnalyzer,
        min_confidence: float = 0.3,
    ) -> None:
        self._analyzer = analyzer
        self._analysis: TraceAnalysis | None = analyzer._analysis
        self._min_confidence = min_confidence

    def reverse_engineer(self) -> ReverseEngineeringResult:
        """Run the full reverse engineering pipeline (all stages together,
        in order). For the staged web UI -- where each stage is run and
        timed independently -- call :meth:`find_counters`,
        :meth:`find_crcs`, and :meth:`find_application_signals` directly
        instead; this method is the right entry point for
        non-interactive/CLI use (:meth:`to_pdu_db`, :meth:`save_pdu_db`).
        """
        if self._analysis is None:
            raise RuntimeError("Call analyzer.analyze() before reverse_engineer()")

        counters_by_id = self.find_counters()
        crcs_by_id = self.find_crcs(counters_by_id)
        app_signals_by_id = self.find_application_signals(counters_by_id, crcs_by_id)
        return self.combine_results(counters_by_id, app_signals_by_id, crcs_by_id)

    def combine_results(
        self,
        counters_by_id: dict[int, list[DiscoveredSignal]] | None = None,
        app_signals_by_id: dict[int, list[DiscoveredSignal]] | None = None,
        crcs_by_id: dict[int, list[DiscoveredSignal]] | None = None,
    ) -> ReverseEngineeringResult:
        """Merge staged results into a :class:`ReverseEngineeringResult`
        covering every CAN ID -- the same shape :meth:`reverse_engineer`
        returns, but usable directly with whatever's actually been computed
        so far. A caller that ran :meth:`find_counters`/:meth:`find_crcs`/
        :meth:`find_application_signals` independently (e.g. the staged web
        UI, caching each stage's result as it completes) calls this to
        assemble an exportable result without recomputing anything; any
        argument may be omitted if that stage hasn't run yet. When both a
        counter and a CRC are present for a message, also sets
        :attr:`ReverseEngineeredMessage.e2e_profile` via
        :func:`guess_e2e_profile`.
        """
        if self._analysis is None:
            raise RuntimeError("Call analyzer.analyze() before combine_results()")

        counters_by_id = counters_by_id or {}
        app_signals_by_id = app_signals_by_id or {}
        crcs_by_id = crcs_by_id or {}

        result = ReverseEngineeringResult(numpy_available=_HAS_NUMPY)

        for aid in sorted(self._analysis.can_stats.keys()):
            s = self._analysis.can_stats[aid]
            counters = counters_by_id.get(aid, [])
            crcs = crcs_by_id.get(aid, [])
            # Each stage already post-processed (named/ordered) its own
            # signals independently, so e.g. a genuinely counter-shaped
            # value _merge_adjacent_smooth_signals() assembled in stage 3
            # (find_application_signals) and the real counter stage 2
            # (find_counters) found would both be independently named
            # "Counter_1" -- re-running _post_process_signals on the fully
            # merged, cross-stage list renumbers everything from scratch so
            # names stay unique and sequential in the combined result.
            discovered_signals = self._post_process_signals(
                counters + crcs + app_signals_by_id.get(aid, []), s
            )

            length = max(s.dlc_values) if s.dlc_values else 8
            cycle_ms = self._analysis.cycle_times_ms.get(aid, 0)

            e2e_profile = None
            if counters and crcs:
                e2e_profile = guess_e2e_profile(counters[0], crcs[0])

            msg = ReverseEngineeredMessage(
                can_id=aid,
                channel=s.channel,
                db_id=aid + 1,
                message_name=f"Msg_0x{aid:X}",
                bus=f"CAN_{s.channel}",
                bus_type="CANFD" if s.is_fd else "CAN",
                identifier=aid & 0x1FFFFFFF,
                is_extended=s.is_extended,
                is_fd=s.is_fd,
                length=length,
                cycle_time_ms=cycle_ms,
                send_type="Cyclic" if cycle_ms > 0 else "Spontaneous",
                signals=discovered_signals,
                e2e_profile=e2e_profile,
            )
            result.messages.append(msg)
            result.total_signals_discovered += len(discovered_signals)

        result.total_can_ids = len(result.messages)
        return result

    # ── Staged analysis: independently runnable, independently cacheable ──
    #
    # Split so a caller (the web UI in particular) can run and time each
    # stage on its own instead of one long blocking call: stage 2 (counters,
    # then CRCs) has an exact, directly-checkable signature and is
    # deliberately run before stage 3 (generic clustering) so those bits
    # never get re-absorbed or re-split by the statistical clustering pass.
    # find_crcs() runs after find_counters() and takes its result as input --
    # CRC fields are searched *relative to* counter positions, not
    # independently (see find_crcs()'s docstring).

    def find_counters(self) -> dict[int, list[DiscoveredSignal]]:
        """Stage 2: dedicated counter scan across every CAN ID.

        Returns discovered counters keyed by CAN ID (IDs with none found are
        omitted). Independent of :meth:`find_application_signals` -- can run
        before it, after it, or not at all.
        """
        if self._analysis is None:
            raise RuntimeError("Call analyzer.analyze() before find_counters()")

        result: dict[int, list[DiscoveredSignal]] = {}
        for aid, s in self._analysis.can_stats.items():
            if not s.payload_samples or s.count < 2:
                continue
            raw_values = self._compute_raw_values(s)
            max_len = max(len(p) for p in s.payload_samples)
            total_bits = max_len * 8

            counter_signals, _claimed = self._scan_for_counters(s, raw_values, total_bits, 1)
            counter_signals = [
                sig for sig in counter_signals if sig.confidence >= self._min_confidence
            ]
            if counter_signals:
                result[aid] = self._post_process_signals(counter_signals, s)
        return result

    def find_application_signals(
        self,
        counters_by_id: dict[int, list[DiscoveredSignal]] | None = None,
        crcs_by_id: dict[int, list[DiscoveredSignal]] | None = None,
    ) -> dict[int, list[DiscoveredSignal]]:
        """Stage 3: generic bit-correlation clustering for application
        signals across every CAN ID.

        `counters_by_id`/`crcs_by_id` (typically :meth:`find_counters`'s
        and :meth:`find_crcs`'s own return values) exclude each ID's
        already-claimed counter/CRC bits from clustering; omit either (or
        both) to cluster those bits too, same as if that stage hadn't run.
        """
        if self._analysis is None:
            raise RuntimeError("Call analyzer.analyze() before find_application_signals()")

        counters_by_id = counters_by_id or {}
        crcs_by_id = crcs_by_id or {}
        result: dict[int, list[DiscoveredSignal]] = {}
        for aid, s in self._analysis.can_stats.items():
            if not s.payload_samples or s.count < 2:
                continue

            claimed: set[int] = set()
            next_sig_id = 1
            for sig in counters_by_id.get(aid, []) + crcs_by_id.get(aid, []):
                claimed.update(range(sig.start_pos, sig.start_pos + sig.length))
                next_sig_id += 1

            raw_values = self._compute_raw_values(s)
            app_signals = self._cluster_application_signals(s, raw_values, claimed, next_sig_id)
            app_signals = [
                sig for sig in app_signals if sig.confidence >= self._min_confidence
            ]
            if app_signals:
                result[aid] = self._post_process_signals(app_signals, s)
        return result

    def find_crcs(
        self, counters_by_id: dict[int, list[DiscoveredSignal]]
    ) -> dict[int, list[DiscoveredSignal]]:
        """Stage 2.5: AUTOSAR CRC scan, run after and informed by
        :meth:`find_counters` -- CRC fields are searched byte-aligned near
        each counter's own byte position (AUTOSAR convention keeps them
        adjacent within a message), not independently across the whole
        payload, since an unconstrained byte-aligned x algorithm x Data-ID
        search over every CAN ID would be far too expensive. IDs with no
        counter are skipped entirely: without a counter position to anchor
        the search there's nowhere sensible to look.
        """
        if self._analysis is None:
            raise RuntimeError("Call analyzer.analyze() before find_crcs()")

        result: dict[int, list[DiscoveredSignal]] = {}
        for aid, counters in counters_by_id.items():
            if not counters:
                continue
            stats = self._analysis.can_stats.get(aid)
            if not stats or not stats.payload_samples:
                continue
            active_len = self._active_payload_length(stats)
            claimed = {
                b for c in counters for b in range(c.start_pos, c.start_pos + c.length)
            }

            crc_signals: list[DiscoveredSignal] = []
            sig_id = 1
            for counter in counters:
                sig = self._find_crc_for_counter(stats, counter, claimed, active_len, sig_id)
                if sig is not None:
                    crc_signals.append(sig)
                    claimed.update(range(sig.start_pos, sig.start_pos + sig.length))
                    sig_id += 1

            crc_signals = [
                sig for sig in crc_signals if sig.confidence >= self._min_confidence
            ]
            if crc_signals:
                result[aid] = self._post_process_signals(crc_signals, stats)
        return result

    def _cluster_application_signals(
        self,
        stats: CanIdStats,
        raw_values: list[dict[str, Any]],
        exclude: set[int],
        start_sig_id: int,
    ) -> list[DiscoveredSignal]:
        """The generic correlation-clustering pass, over whatever bits
        `exclude` (a counter scan's claimed bits, typically) doesn't cover.
        """
        signals: list[DiscoveredSignal] = []
        sig_id = start_sig_id

        bit_matrix = self._build_bit_matrix(stats)
        if bit_matrix is None or len(bit_matrix) < 2:
            return signals

        clusters = self._cluster_correlated_bits(bit_matrix, exclude=exclude)

        grouped: dict[int, list[int]] = {}
        for bit_idx, cluster_id in clusters.items():
            grouped.setdefault(cluster_id, []).append(bit_idx)

        for cluster_id in sorted(grouped.keys()):
            if cluster_id == -1:
                continue  # "not active enough to cluster" bucket -- not a real signal group
            bits = sorted(grouped[cluster_id])
            if not bits:
                continue

            contiguous = self._find_contiguous_groups(bits)
            if contiguous and len(contiguous) > 1:
                for group in contiguous:
                    sig = self._build_signal(sig_id, group, raw_values, stats)
                    if sig:
                        signals.append(sig)
                        sig_id += 1
            else:
                sig = self._build_signal(sig_id, bits, raw_values, stats)
                if sig:
                    signals.append(sig)
                    sig_id += 1

        return self._merge_adjacent_smooth_signals(signals, raw_values, stats, start_sig_id)

    @staticmethod
    def _is_smoothly_varying(raw_values: list[int], length: int) -> bool:
        """True if a (combined) multi-bit value's frame-to-frame steps are
        small relative to its full range -- the signature of one coherent
        changing quantity (a counter, sequence number, or an ordinary
        slowly-ramping physical signal), as opposed to unrelated bits that
        just happen to sit next to each other, or a high-entropy field
        (random-looking flags, a checksum) that jumps around.

        Uses *circular* distance (the shorter way around the value's own
        modulus), not a literal absolute difference -- a rolling value's
        wrap (e.g. 15 -> 0 for a 4-bit field) is a smooth +1 step, not a
        huge jump, exactly like :meth:`_detect_counter`'s masked diff.
        """
        if len(raw_values) < 5:
            return False
        full_range = (1 << length) - 1
        modulus = 1 << length
        if full_range <= 0:
            return False
        diffs = []
        for i in range(1, len(raw_values)):
            d = (raw_values[i] - raw_values[i - 1]) % modulus
            diffs.append(min(d, modulus - d))
        mean_delta = sum(diffs) / len(diffs) if diffs else 0.0
        return (mean_delta / full_range) < 0.1

    def _merge_adjacent_smooth_signals(
        self,
        signals: list[DiscoveredSignal],
        raw_values: list[dict[str, Any]],
        stats: CanIdStats,
        start_sig_id: int,
    ) -> list[DiscoveredSignal]:
        """Merge bit-adjacent clustered signals when their COMBINED value
        changes smoothly -- catches the case where correlation-based
        clustering (:meth:`_cluster_correlated_bits`) splits the
        constituent bits of one coherent multi-bit quantity into several
        spurious single-bit "flags". A binary counter's own bits don't
        pairwise correlate the way clustering looks for: each bit toggles
        at a *different* rate (related by carry, not by co-occurring
        transitions -- the LSB flips every step, the next bit every other
        step, ...), so two bits of the very same counter can easily fail
        both the numpy path's Pearson-correlation threshold and the
        pure-Python path's Jaccard-similarity-of-transitions threshold,
        landing in separate clusters (or singleton bit-adjacent clusters)
        despite obviously belonging together once you read them as one
        number. Greedily tries widening each signal into its immediate
        right-hand neighbor (both must already be plain, non-counter,
        non-checksum clustered signals) and keeps the merge only if the
        wider value is itself smoothly-varying -- a merge across two truly
        unrelated adjacent fields would show large jumps whenever either
        one moves independently, so this is self-correcting rather than
        indiscriminately merging every neighboring pair.
        """
        if len(signals) < 2:
            return signals

        ordered = sorted(signals, key=lambda s: s.start_pos)
        merged: list[DiscoveredSignal] = []
        i = 0
        while i < len(ordered):
            current = ordered[i]
            j = i + 1
            while (
                j < len(ordered)
                and not current.is_counter
                and not current.is_checksum
                and not ordered[j].is_counter
                and not ordered[j].is_checksum
                and ordered[j].start_pos == current.start_pos + current.length
                and current.length + ordered[j].length <= 32
            ):
                combined_bits = list(range(current.start_pos, ordered[j].start_pos + ordered[j].length))
                # Judge smoothness on the *full* extraction, not the 100-sample
                # preview DiscoveredSignal.raw_values is truncated to -- a real
                # capture easily runs to thousands of frames, and a jump past
                # frame 100 would otherwise go unnoticed.
                byte_order = self._detect_byte_order(combined_bits, stats)
                full_raw = self._extract_raw_numbers(combined_bits, byte_order, stats)
                if not full_raw or not self._is_smoothly_varying(full_raw, len(combined_bits)):
                    break
                candidate = self._build_signal(current.id, combined_bits, raw_values, stats)
                if candidate is None:
                    break
                current = candidate
                j += 1
            merged.append(current)
            i = j if j > i + 1 else i + 1

        for offset, sig in enumerate(merged):
            sig.id = start_sig_id + offset
        return merged

    # ── Dedicated counter scan (stage 2) ───────────────────────────────

    _COUNTER_WIDTHS = (32, 8, 4)  # widest first: an 8-bit counter's low
    # nibble also independently looks like a valid 4-bit counter, so a
    # narrower width must not get the chance to claim it out from under a
    # genuinely wider counter.

    def _quick_counter_check(self, bits: list[int], stats: CanIdStats, length: int) -> bool:
        """Cheap pre-filter for :meth:`_scan_for_counters`: just extract raw
        numbers and run :meth:`_detect_counter`, skipping the expensive parts
        of :meth:`_build_signal` (byte-order smoothness heuristic, checksum
        detection, confidence scoring) that only matter once a candidate is
        already known to be a counter. Byte order is irrelevant for 4/8-bit
        widths (a single nibble/byte has no ordering ambiguity); for 32-bit,
        try both -- still far cheaper than the smoothness heuristic, which
        itself extracts raw numbers twice per candidate.
        """
        orders = (0,) if length in (4, 8) else (0, 1)
        for byte_order in orders:
            raw_nums = self._extract_raw_numbers(bits, byte_order, stats)
            if raw_nums and self._detect_counter(raw_nums, length):
                return True
        return False

    def _scan_for_counters(
        self,
        stats: CanIdStats,
        raw_values: list[dict[str, Any]],
        total_bits: int,
        start_sig_id: int,
    ) -> tuple[list[DiscoveredSignal], set[int]]:
        """Scan every byte/nibble-aligned candidate position for a 4/8/32-bit
        AUTOSAR counter. Returns the signals found and the set of bit
        positions they claim (for the clustering pass to exclude).

        :meth:`_quick_counter_check` pre-filters candidates cheaply; the full
        :meth:`_build_signal` (and its authoritative `is_counter` check) still
        runs before anything is accepted, so this changes *when* work happens,
        not the detection result.
        """
        found: list[DiscoveredSignal] = []
        claimed: set[int] = set()
        sig_id = start_sig_id

        for length in self._COUNTER_WIDTHS:
            for start in range(0, total_bits - length + 1, length):
                bits = list(range(start, start + length))
                if any(b in claimed for b in bits):
                    continue
                if not self._quick_counter_check(bits, stats, length):
                    continue
                sig = self._build_signal(sig_id, bits, raw_values, stats)
                if sig is not None and sig.is_counter:
                    found.append(sig)
                    claimed.update(bits)
                    sig_id += 1

        return found, claimed

    # ── Dedicated CRC scan (stage 2.5) ─────────────────────────────────

    @staticmethod
    def _crc_candidate_positions(
        counter_byte_start: int, counter_byte_end: int, width_bytes: int, max_len: int
    ) -> list[int]:
        """Byte-aligned start positions to try for a CRC field: a 2-byte
        window immediately before/after the counter's own bytes (per the
        user's domain observation that AUTOSAR keeps the two adjacent),
        excluding any overlap with the counter itself.
        """
        candidates = []
        lo = max(0, counter_byte_start - 2)
        hi = min(max_len - width_bytes, counter_byte_end + 2)
        for start in range(lo, hi + 1):
            end = start + width_bytes
            if end > max_len:
                continue
            if start < counter_byte_end and end > counter_byte_start:
                continue  # overlaps the counter's own bytes
            candidates.append(start)
        return candidates

    @staticmethod
    def _active_payload_length(stats: CanIdStats) -> int:
        """Highest byte index (+1) that ever varies across the *entire*
        capture, used to exclude CAN FD DLC padding from the CRC's "other
        bytes" input (the transmitting ECU's own CRC calculation never
        saw padding it never sent).

        Only trims when `max_len > 8`: every DLC from 0-8 is directly
        achievable in both classic CAN and CAN FD, so there's no such
        thing as "padding" there -- a trailing constant byte in an
        8-byte-or-smaller frame is just as likely a genuinely reserved
        (if currently-unexercised) field as it is padding, and trimming
        it broke real matches (found live on message 0x40: an always-0
        trailing byte that WAS part of its real CRC input). Padding as a
        structural phenomenon only exists once DLC jumps into CAN FD's
        non-linear bucket sizes (12, 16, 20, 24, 32, 48, 64) -- there,
        a message using only a couple of bytes in a 32-byte frame (as
        seen on 0xB6) is unambiguous.
        """
        if not stats.payload_samples:
            return 0
        max_len = max(len(p) for p in stats.payload_samples)
        if max_len <= 8:
            return max_len
        first = stats.payload_samples[0]
        for idx in range(max_len - 1, -1, -1):
            first_val = first[idx] if idx < len(first) else 0
            if any((p[idx] if idx < len(p) else 0) != first_val for p in stats.payload_samples):
                return idx + 1
        return max_len

    @staticmethod
    def _crc_candidate_looks_variable(
        stats: CanIdStats, start_byte: int, width_bytes: int
    ) -> bool:
        """Cheap prefilter: a real CRC changes on nearly every frame (it's
        a function of the counter, which itself changes every frame), so a
        candidate position with few distinct values across the sample
        can't be one -- skip it before paying for any CRC computation.
        """
        sample = stats.payload_samples[:_CRC_SAMPLE_SIZE]
        seen = set()
        for p in sample:
            if len(p) >= start_byte + width_bytes:
                seen.add(bytes(p[start_byte:start_byte + width_bytes]))
        return len(seen) >= max(3, int(0.5 * len(sample)))

    # Tiny all-or-nothing probe checked before scoring each Data-ID guess
    # during the brute force: a wrong guess has only a 1/256 chance of
    # matching any single frame, so it almost always fails on the very
    # first one -- exiting immediately there (instead of always scoring a
    # full _CRC_SAMPLE_SIZE-frame fraction) is what keeps the 256-guess
    # brute force cheap in the common case where no CRC is actually present.
    _CRC_PROBE_SIZE = 6

    @staticmethod
    def _crc_probe_matches(
        frames: list,
        start_byte: int,
        width_bytes: int,
        algo: str,
        data_id: int | None,
        big_endian: bool,
        active_len: int,
    ) -> bool:
        for payload in frames:
            if len(payload) < start_byte + width_bytes:
                continue
            observed = int.from_bytes(
                bytes(payload[start_byte:start_byte + width_bytes]),
                "big" if big_endian else "little",
            )
            other = bytes(payload[:start_byte]) + bytes(payload[start_byte + width_bytes:active_len])
            if _crc_autosar(other, algo, extra_byte=data_id) != observed:
                return False
        return True

    @staticmethod
    def _crc_match_fraction(
        frames: list,
        start_byte: int,
        width_bytes: int,
        algo: str,
        data_id: int | None,
        big_endian: bool,
        active_len: int,
    ) -> float:
        """Fraction of `frames` where computed CRC(algo, other bytes,
        data_id) matches the observed bytes at [start_byte, start_byte +
        width_bytes) interpreted as `big_endian`/little. "Other bytes" is
        bounded to `active_len` (see :meth:`_active_payload_length`) so
        CAN FD padding never gets fed into the CRC computation. Shared by
        the sample-level search and the full-trace verification pass.
        """
        matches = 0
        total = 0
        for payload in frames:
            if len(payload) < start_byte + width_bytes:
                continue
            observed = int.from_bytes(
                bytes(payload[start_byte:start_byte + width_bytes]),
                "big" if big_endian else "little",
            )
            other = bytes(payload[:start_byte]) + bytes(payload[start_byte + width_bytes:active_len])
            computed = _crc_autosar(other, algo, extra_byte=data_id)
            total += 1
            if computed == observed:
                matches += 1
        return matches / total if total else 0.0

    def _match_crc_algorithm(
        self, stats: CanIdStats, start_byte: int, width_bytes: int, algo: str, active_len: int
    ) -> tuple[float, int | None, bool] | None:
        """Find the best-scoring (data_id, byte_order) for `algo` at this
        position, checked against a small sample first. Tries "no Data ID"
        (cheap: one CRC per frame per byte order) before brute-forcing a
        1-byte Data ID, which is bounded to :data:`_CRC_DATA_ID_ALGOS`;
        each guess is first rejected cheaply via :meth:`_crc_probe_matches`
        (see its docstring) before paying for a full-sample score, and the
        loop breaks as soon as the threshold is cleared. Returns None if
        nothing clears :data:`_CRC_MATCH_THRESHOLD` on the sample; the
        caller must still re-verify against the full trace before
        accepting.
        """
        sample = stats.payload_samples[:_CRC_SAMPLE_SIZE]

        best: tuple[float, int | None, bool] = (0.0, None, True)
        for big_endian in (True, False):
            frac = self._crc_match_fraction(
                sample, start_byte, width_bytes, algo, None, big_endian, active_len
            )
            if frac > best[0]:
                best = (frac, None, big_endian)

        if best[0] < _CRC_MATCH_THRESHOLD and algo in _CRC_DATA_ID_ALGOS:
            probe = sample[: self._CRC_PROBE_SIZE]
            for data_id in range(256):
                for big_endian in (True, False):
                    if not self._crc_probe_matches(
                        probe, start_byte, width_bytes, algo, data_id, big_endian, active_len
                    ):
                        continue
                    frac = self._crc_match_fraction(
                        sample, start_byte, width_bytes, algo, data_id, big_endian, active_len
                    )
                    if frac > best[0]:
                        best = (frac, data_id, big_endian)
                if best[0] >= _CRC_MATCH_THRESHOLD:
                    break

        return best if best[0] >= _CRC_MATCH_THRESHOLD else None

    @staticmethod
    def _looks_like_checksum(
        raw_values: list[int],
        length: int,
        stats: CanIdStats,
        field_bytes: range,
    ) -> bool:
        """Behavioral fallback for a candidate that doesn't match any known
        AUTOSAR CRC algorithm exactly (a non-standard/proprietary checksum,
        or one whose Data ID/init scheme isn't covered by the brute force
        in :meth:`_match_crc_algorithm`). Rather than searching for the
        exact formula, recognize a checksum from how it *behaves*, the way
        a human looking at a trace would: an ordinary physical signal
        (speed, temperature, torque, ...) is scaled so its real-world
        range sits comfortably inside the field's bit-width headroom (an
        8-bit speed signal practically never reaches 255) and changes
        gradually frame to frame; a checksum has no such natural ceiling,
        is a near-injective function of the rest of the payload, and jumps
        around unpredictably. Three checks, all must pass:

          1. Uses close to its full bit-width range (unlike a physical
             signal, which rarely approaches its field's extremes).
          2. Changes on almost every frame where anything ELSE in the
             payload changes (a physical signal varies independently of
             unrelated fields; a checksum is a function of them).
          3. Frame-to-frame deltas are spread widely, not clustered near
             zero (a slow physical ramp) -- a real counter would already
             have been claimed by :meth:`_scan_for_counters` and excluded
             via `claimed`, so a small, camped delta here is a physical
             signal, not this.
        """
        if len(raw_values) < 10 or len(stats.payload_samples) < 10:
            return False

        full_range = (1 << length) - 1
        if full_range <= 0:
            return False

        span = max(raw_values) - min(raw_values)
        if span / full_range < 0.6:
            return False

        other_changed = 0
        this_changed_given_other = 0
        for i in range(1, len(stats.payload_samples)):
            prev, cur = stats.payload_samples[i - 1], stats.payload_samples[i]
            width = max(len(prev), len(cur))
            other_diff = any(
                (prev[b] if b < len(prev) else 0) != (cur[b] if b < len(cur) else 0)
                for b in range(width)
                if b not in field_bytes
            )
            if not other_diff:
                continue
            other_changed += 1
            if i < len(raw_values) and raw_values[i] != raw_values[i - 1]:
                this_changed_given_other += 1
        if other_changed == 0 or this_changed_given_other / other_changed < 0.9:
            return False

        diffs = [abs(raw_values[i] - raw_values[i - 1]) for i in range(1, len(raw_values))]
        mean_delta = sum(diffs) / len(diffs) if diffs else 0.0
        if mean_delta / full_range < 0.15:
            return False

        return True

    def _find_crc_for_counter(
        self,
        stats: CanIdStats,
        counter: DiscoveredSignal,
        claimed: set[int],
        active_len: int,
        sig_id: int,
    ) -> DiscoveredSignal | None:
        """Search every algorithm x byte-aligned position near `counter`
        for the single best-matching AUTOSAR CRC field, verified against
        the *entire* trace (not just the search sample) before being
        accepted -- a promising sample match that doesn't hold up on full
        verification is rejected outright, not just down-scored.
        `active_len` (see :meth:`_active_payload_length`) bounds both the
        candidate search and the "other bytes" fed into every CRC
        computation to the payload's real extent, excluding CAN FD
        padding.
        """
        counter_byte_start = counter.start_pos // 8
        counter_byte_end = (counter.start_pos + counter.length + 7) // 8

        # (sample_frac, algo, start_byte, width_bytes, data_id, big_endian)
        best: tuple[float, str, int, int, int | None, bool] | None = None

        for algo, params in _AUTOSAR_CRC_ALGORITHMS.items():
            width_bytes = int(params["width"]) // 8
            for start_byte in self._crc_candidate_positions(
                counter_byte_start, counter_byte_end, width_bytes, active_len
            ):
                bits = set(range(start_byte * 8, (start_byte + width_bytes) * 8))
                if bits & claimed:
                    continue
                if not self._crc_candidate_looks_variable(stats, start_byte, width_bytes):
                    continue

                match = self._match_crc_algorithm(stats, start_byte, width_bytes, algo, active_len)
                if match is None:
                    continue
                frac, data_id, big_endian = match
                if best is None or frac > best[0]:
                    best = (frac, algo, start_byte, width_bytes, data_id, big_endian)

        if best is None:
            return self._find_checksum_by_behavior(
                stats, counter_byte_start, counter_byte_end, claimed, active_len, sig_id
            )

        _sample_frac, algo, start_byte, width_bytes, data_id, big_endian = best
        full_frac = self._crc_match_fraction(
            stats.payload_samples, start_byte, width_bytes, algo, data_id, big_endian, active_len
        )
        if full_frac < _CRC_MATCH_THRESHOLD:
            return None

        raw_nums = [
            int.from_bytes(
                bytes(p[start_byte:start_byte + width_bytes]), "big" if big_endian else "little"
            )
            for p in stats.payload_samples
            if len(p) >= start_byte + width_bytes
        ]

        # Byte order is meaningless for a single-byte field (CRC8/CRC8H2F --
        # in practice the overwhelming majority of AUTOSAR CRC fields found
        # here), and big_endian ties there since int.from_bytes gives the
        # same result either way, so the tie-break would otherwise always
        # export ByteOrder=1 (Motorola) by accident of loop order. Force
        # Intel for those -- its DBC StartPos translation (_to_dbc_start_bit)
        # is verified exact; Motorola's multi-byte translation is not, so
        # only genuinely multi-byte (16/32-bit) CRCs keep the detected order.
        byte_order = 0 if width_bytes == 1 else (1 if big_endian else 0)

        return DiscoveredSignal(
            id=sig_id,
            name=algo,
            start_pos=start_byte * 8,
            length=width_bytes * 8,
            byte_order=byte_order,
            value_type="Unsigned",
            factor=1.0,
            offset=0.0,
            min_val=0.0,
            max_val=float((1 << (width_bytes * 8)) - 1),
            unit="",
            enum_values=None,
            is_counter=False,
            is_checksum=True,
            confidence=min(1.0, 0.5 + full_frac / 2),
            raw_values=raw_nums[:100],
            physical_values=[float(v) for v in raw_nums[:100]],
            crc_algorithm=algo,
            crc_data_id=data_id,
        )

    def _find_checksum_by_behavior(
        self,
        stats: CanIdStats,
        counter_byte_start: int,
        counter_byte_end: int,
        claimed: set[int],
        active_len: int,
        sig_id: int,
    ) -> DiscoveredSignal | None:
        """Fallback for when no candidate near the counter matches a known
        AUTOSAR CRC algorithm exactly: check the same candidate positions
        for checksum-*shaped* behavior instead (see
        :meth:`_looks_like_checksum`). Identifies "this is very likely
        some kind of checksum" without knowing its exact formula --
        `crc_algorithm`/`crc_data_id` are left unset (so no E2E profile
        gets guessed from it) and confidence is capped below what a
        verified exact match gets.
        """
        for width_bytes in (1, 2, 4):
            for start_byte in self._crc_candidate_positions(
                counter_byte_start, counter_byte_end, width_bytes, active_len
            ):
                bits = set(range(start_byte * 8, (start_byte + width_bytes) * 8))
                if bits & claimed:
                    continue
                if not self._crc_candidate_looks_variable(stats, start_byte, width_bytes):
                    continue

                raw_values = [
                    int.from_bytes(bytes(p[start_byte:start_byte + width_bytes]), "big")
                    for p in stats.payload_samples
                    if len(p) >= start_byte + width_bytes
                ]
                field_bytes = range(start_byte, start_byte + width_bytes)
                if not self._looks_like_checksum(raw_values, width_bytes * 8, stats, field_bytes):
                    continue

                return DiscoveredSignal(
                    id=sig_id,
                    name="Checksum",
                    start_pos=start_byte * 8,
                    length=width_bytes * 8,
                    byte_order=0,
                    value_type="Unsigned",
                    factor=1.0,
                    offset=0.0,
                    min_val=0.0,
                    max_val=float((1 << (width_bytes * 8)) - 1),
                    unit="",
                    enum_values=None,
                    is_counter=False,
                    is_checksum=True,
                    confidence=0.55,
                    raw_values=raw_values[:100],
                    physical_values=[float(v) for v in raw_values[:100]],
                    crc_algorithm=None,
                    crc_data_id=None,
                )
        return None

    # ── Bit matrix construction ───────────────────────────────────────

    @staticmethod
    def _build_bit_matrix(stats: CanIdStats) -> list[list[int]] | None:
        """Build a matrix: rows=frames, columns=bit_positions, values=0/1.

        Returns None if samples are too few or too short.
        """
        if not stats.payload_samples:
            return None
        max_len = max(len(p) for p in stats.payload_samples)
        if max_len == 0:
            return None

        matrix: list[list[int]] = []
        for payload in stats.payload_samples:
            row: list[int] = []
            for byte_idx in range(max_len):
                b = payload[byte_idx] if byte_idx < len(payload) else 0
                for bit in range(7, -1, -1):
                    row.append((b >> bit) & 1)
            matrix.append(row)
        return matrix

    # ── Bit correlation clustering ────────────────────────────────────

    @staticmethod
    def _cluster_correlated_bits(
        matrix: list[list[int]], exclude: set[int] | None = None
    ) -> dict[int, int]:
        """Cluster bit positions by change correlation.

        `exclude` -- bit positions already claimed by an earlier dedicated
        scan (e.g. a counter found by :meth:`_scan_for_counters`) -- are
        treated as inactive here, so the generic clustering pass for
        application signals never re-splits or re-absorbs them.

        Returns a dict mapping bit_position → cluster_id.
        """
        n_frames = len(matrix)
        n_bits = len(matrix[0])

        if n_frames <= 1:
            return {}

        if _HAS_NUMPY:
            return TraceReverseEngineer._cluster_numpy(matrix, exclude)
        else:
            return TraceReverseEngineer._cluster_pure_python(matrix, n_frames, n_bits, exclude)

    @staticmethod
    def _active_bit_indices(
        matrix: list[list[int]],
        min_minority_fraction: float = 0.05,
        exclude: set[int] | None = None,
    ) -> list[int]:
        """Bit positions with enough real variability to be signal candidates.

        A bit that's constant, or that only flips a handful of times across
        the whole capture (a stray glitch, a rare fault bit, a reserved bit
        that happens to toggle once), isn't a meaningful signal. Require the
        minority value to occur at least `min_minority_fraction` of the time
        -- "changed at least once" (the old pure-Python filter) or "variance
        above a small fixed threshold" (the old numpy filter, which a single
        flip in a ~30-frame capture already clears) both let far too many
        near-constant bits through, showing up as a wall of trivial
        always-0-except-once Bool "signals". Shared by both clustering paths
        so results don't depend on whether numpy happens to be installed.
        """
        n_frames = len(matrix)
        if n_frames == 0:
            return []
        n_bits = len(matrix[0])
        exclude = exclude or set()
        active = []
        for bit in range(n_bits):
            if bit in exclude:
                continue
            ones = sum(row[bit] for row in matrix)
            minority = min(ones, n_frames - ones)
            if minority / n_frames >= min_minority_fraction:
                active.append(bit)
        return active

    @staticmethod
    def _cluster_numpy(matrix: list[list[int]], exclude: set[int] | None = None) -> dict[int, int]:
        arr = np.array(matrix, dtype=np.int8)
        n_bits = arr.shape[1]

        active = np.array(TraceReverseEngineer._active_bit_indices(matrix, exclude=exclude), dtype=np.int64)
        if len(active) < 2:
            cluster_of = {int(i): i for i in active}
            for i in range(n_bits):
                if i not in cluster_of:
                    cluster_of[i] = -1
            return cluster_of

        active_arr = arr[:, active]
        corr = np.corrcoef(active_arr.T)
        corr = np.nan_to_num(corr, nan=0.0)

        threshold = 0.6
        n_active = len(active)
        visited = set()
        cluster_of: dict[int, int] = {}
        next_cluster = 0

        for i in range(n_active):
            if i in visited:
                continue
            cluster_idx = active[i]
            cluster_of[int(cluster_idx)] = next_cluster
            visited.add(i)
            stack = [i]
            while stack:
                cur = stack.pop()
                for j in range(n_active):
                    if j not in visited and abs(corr[cur, j]) > threshold:
                        visited.add(j)
                        cluster_of[int(active[j])] = next_cluster
                        stack.append(j)
            next_cluster += 1

        for i in range(n_bits):
            if i not in cluster_of:
                cluster_of[i] = -1

        return cluster_of

    @staticmethod
    def _cluster_pure_python(
        matrix: list[list[int]], n_frames: int, n_bits: int, exclude: set[int] | None = None
    ) -> dict[int, int]:
        """Fallback clustering without numpy using Jaccard similarity on bit
        transitions."""
        transitions: dict[int, list[int]] = {}
        for bit in range(n_bits):
            t = []
            prev = None
            for row in matrix:
                val = row[bit]
                if prev is not None and val != prev:
                    t.append(1)
                else:
                    t.append(0)
                prev = val
            transitions[bit] = t

        active_bits = TraceReverseEngineer._active_bit_indices(matrix, exclude=exclude)

        cluster_of: dict[int, int] = {}
        next_cluster = 0
        visited = set()

        def _jaccard(a: list[int], b: list[int]) -> float:
            and_count = sum(1 for i in range(len(a)) if a[i] and b[i])
            or_count = sum(1 for i in range(len(a)) if a[i] or b[i])
            return and_count / or_count if or_count > 0 else 0.0

        for bit in active_bits:
            if bit in visited:
                continue
            cluster_of[bit] = next_cluster
            visited.add(bit)
            stack = [bit]
            while stack:
                cur = stack.pop()
                for other in active_bits:
                    if other not in visited:
                        sim = _jaccard(transitions[cur], transitions[other])
                        if sim > 0.5:
                            visited.add(other)
                            cluster_of[other] = next_cluster
                            stack.append(other)
            next_cluster += 1

        for bit in range(n_bits):
            if bit not in cluster_of:
                cluster_of[bit] = -1

        return cluster_of

    # ── Contiguous group splitting ────────────────────────────────────

    @staticmethod
    def _find_contiguous_groups(bits: list[int]) -> list[list[int]]:
        """Split a sorted list of bit positions into contiguous groups."""
        if not bits:
            return []
        groups: list[list[int]] = []
        current = [bits[0]]
        for i in range(1, len(bits)):
            if bits[i] == bits[i - 1] + 1:
                current.append(bits[i])
            else:
                groups.append(current)
                current = [bits[i]]
        groups.append(current)
        return groups

    # ── Raw value computation ─────────────────────────────────────────

    @staticmethod
    def _compute_raw_values(stats: CanIdStats) -> list[dict[str, Any]]:
        """Compute raw values for each payload byte/position across samples."""
        max_len = max(len(p) for p in stats.payload_samples) if stats.payload_samples else 0
        pos_values: list[list[int]] = [[] for _ in range(max_len * 8)]

        for payload in stats.payload_samples:
            for byte_idx in range(len(payload)):
                b = payload[byte_idx]
                for bit in range(8):
                    pos = byte_idx * 8 + bit
                    pos_values[pos].append((b >> (7 - bit)) & 1)

        raw: list[dict[str, Any]] = []
        for pos, values in enumerate(pos_values):
            raw.append({
                "pos": pos,
                "byte": pos // 8,
                "bit": pos % 8,
                "values": values,
            })
        return raw

    # ── Signal building ───────────────────────────────────────────────

    def _build_signal(
        self,
        sig_id: int,
        bits: list[int],
        raw_values: list[dict[str, Any]],
        stats: CanIdStats,
    ) -> DiscoveredSignal | None:
        """Build a DiscoveredSignal from a group of related bit positions."""
        if not bits:
            return None

        start_pos = min(bits)
        length = len(bits)
        byte_order = self._detect_byte_order(bits, stats)
        raw_nums = self._extract_raw_numbers(
            bits, byte_order, stats
        )

        if not raw_nums:
            return None

        value_type, min_val, max_val, factor, offset, enum_vals, is_signed = (
            self._analyze_values(raw_nums, length)
        )

        is_counter = self._detect_counter(raw_nums, length)
        is_checksum = self._detect_checksum(raw_nums, stats, bits)
        confidence = self._compute_confidence(
            bits, raw_nums, stats, is_counter, is_checksum
        )

        if is_counter:
            # AUTOSAR counters are always unsigned, with a 1:1 raw<->physical
            # mapping across their full valid range (0..2^length-1) -- not
            # whatever range _analyze_values happened to observe in this
            # particular sample window, since a short capture may not have
            # caught the counter's full wrap cycle. Overrides whatever
            # _analyze_values guessed independently (e.g. "Bool" if every
            # sample so far happened to be 0 or 1).
            value_type = "Unsigned"
            is_signed = False
            factor = 1.0
            offset = 0.0
            min_val = 0.0
            max_val = float((1 << length) - 1)
            enum_vals = None

        # physical_values must use the same reference frame factor/offset
        # were derived from: the sign-converted numeric value for signed
        # signals, the raw bit pattern otherwise (always the latter for a
        # counter, since is_signed is forced False above).
        working_nums = (
            self._to_signed(raw_nums, length) if is_signed else raw_nums
        )

        return DiscoveredSignal(
            id=sig_id,
            name=f"Signal_{sig_id}",
            start_pos=start_pos,
            length=length,
            byte_order=byte_order,
            value_type=value_type,
            factor=factor,
            offset=offset,
            min_val=min_val,
            max_val=max_val,
            unit="",
            enum_values=enum_vals,
            is_counter=is_counter,
            is_checksum=is_checksum,
            confidence=confidence,
            raw_values=raw_nums[:100],
            physical_values=[v * factor + offset for v in working_nums[:100]],
        )

    # ── Byte order detection ──────────────────────────────────────────

    @staticmethod
    def _detect_byte_order(
        bits: list[int], stats: CanIdStats
    ) -> int:
        """Detect Intel (0) vs Motorola (1) byte order for a signal.

        Heuristic: if the signal spans multiple bytes, Intel tends
        to produce smoother (less jumpy) sequences than Motorola.
        """
        if not stats.payload_samples or len(bits) <= 8:
            return 0

        intel_raw = TraceReverseEngineer._extract_raw_numbers(
            bits, 0, stats
        )
        motorola_raw = TraceReverseEngineer._extract_raw_numbers(
            bits, 1, stats
        )

        if not intel_raw or not motorola_raw:
            return 0

        def _smoothness(values: list[int]) -> float:
            diffs = [abs(values[i] - values[i - 1]) for i in range(1, len(values))]
            return statistics.mean(diffs) if diffs else 0

        intel_jitter = _smoothness(intel_raw)
        motorola_jitter = _smoothness(motorola_raw)

        if motorola_jitter < intel_jitter * 0.7:
            return 1
        return 0

    # ── Raw number extraction ─────────────────────────────────────────

    @staticmethod
    def _extract_raw_numbers(
        bits: list[int],
        byte_order: int,
        stats: CanIdStats,
    ) -> list[int]:
        """Extract raw integer values for a signal from payload samples.

        Bits are indexed MSB-first (bit 0 = MSB of byte 0).

        Both byte orders build the value by shifting left and OR-ing in bits
        most-significant-first; they differ only in *which byte* is most
        significant -- a byte's own internal bit order (MSB-first) never
        flips. Motorola (big-endian): earlier byte = more significant, so
        ascending ``pos`` order (byte0 MSB..LSB, then byte1 MSB..LSB, ...)
        is already MSB-first end-to-end. Intel (little-endian): later byte
        = more significant, so bytes are visited highest-index-first, but
        each byte's own bits still go MSB-first within it.

        Args:
            bits: Bit positions (MSB-first indexing: bit 0 = byte 0 MSB).
            byte_order: 0=Intel (LSB first), 1=Motorola (MSB first).
            stats: CAN ID statistics with payload samples.

        Returns:
            List of raw integer values, one per frame.
        """
        if not stats.payload_samples:
            return []

        max_len = max(len(p) for p in stats.payload_samples)

        if byte_order == 0:
            ordered_bits = sorted(bits, key=lambda p: (-(p // 8), p))
        else:
            ordered_bits = sorted(bits)

        results: list[int] = []
        for payload in stats.payload_samples:
            padded = bytearray(payload) + b"\x00" * (max_len - len(payload))
            value = 0
            for bit_pos in ordered_bits:
                byte_idx = bit_pos // 8
                bit_idx = bit_pos % 8
                b = padded[byte_idx]
                value = (value << 1) | ((b >> (7 - bit_idx)) & 1)
            results.append(value)

        return results

    # ── Value analysis ────────────────────────────────────────────────

    @staticmethod
    def _to_signed(raw_values: list[int], length: int) -> list[int]:
        """Two's-complement conversion: a raw value >= 2^(length-1) is
        negative. Used so factor/offset/min/max and physical_values are all
        derived from the *same* numeric reference frame for signed signals,
        instead of mixing raw bit patterns with sign-converted numbers."""
        if length <= 0:
            return list(raw_values)
        sign_bit = 1 << (length - 1)
        modulus = 1 << length
        return [v - modulus if v >= sign_bit else v for v in raw_values]

    @staticmethod
    def _signed_reading_is_smoother(raw_values: list[int], length: int) -> bool:
        """Compare frame-to-frame smoothness of the raw (unsigned) values
        against their two's-complement (signed) reinterpretation, using
        plain, non-circular differences -- deliberately not the circular
        distance :meth:`_is_smoothly_varying` uses elsewhere, since the
        point here is exactly to catch the artificial jump two's
        complement introduces where a value crosses the nominal sign bit
        despite nothing having actually happened there (a value that
        smoothly ramps straight through it, e.g. 6, 7, ..., 31 for a
        5-bit field, is *not* the same situation as a value that
        genuinely wraps/rolls over -- see :meth:`_is_smoothly_varying`
        for that case). Only consulted as a tie-breaker once
        :meth:`_analyze_values`'s quadrant-span precondition already
        suggests "maybe signed"; a value that never approaches the sign
        boundary is left alone regardless of what this returns.
        """
        if len(raw_values) < 5:
            return False
        modulus = 1 << length
        sign_bit = modulus >> 1
        signed_values = [v - modulus if v >= sign_bit else v for v in raw_values]
        unsigned_total = sum(abs(raw_values[i] - raw_values[i - 1]) for i in range(1, len(raw_values)))
        signed_total = sum(abs(signed_values[i] - signed_values[i - 1]) for i in range(1, len(signed_values)))
        return signed_total < unsigned_total

    @staticmethod
    def _analyze_values(
        raw_values: list[int],
        length: int,
    ) -> tuple[str, float, float, float, float, dict[str, str] | None, bool]:
        """Determine value type, range, scaling, and enumerations.

        Returns (value_type, min_val, max_val, factor, offset, enum_vals, is_signed).
        """
        if not raw_values:
            return "Unsigned", 0.0, 1.0, 1.0, 0.0, None, False

        unique = list(set(raw_values))
        full_range = (1 << length) - 1

        enum_vals = None
        if len(unique) <= 8 and len(unique) <= full_range * 0.5:
            enum_vals = {}
            for i, v in enumerate(sorted(unique)):
                enum_vals[str(v)] = f"State_{i}"

        if length == 1:
            return "Bool", 0.0, 1.0, 1.0, 0.0, {"0": "False", "1": "True"}, False

        is_bool = all(v in (0, 1) for v in unique)
        if is_bool:
            return "Bool", 0.0, 1.0, 1.0, 0.0, {"0": "Off", "1": "On"}, False

        # A raw value >= 2^(length-1) uses the sign bit in two's complement,
        # but "any single value crosses the midpoint" is a weak signal on
        # its own -- an ordinary unsigned byte legitimately takes values
        # above 127 all the time. Require values comfortably on *both*
        # sides of the wrap (bottom and top quarter of the range) as a
        # necessary precondition, but that alone isn't sufficient: a value
        # that just smoothly ramps straight through the nominal sign
        # boundary (e.g. 6, 7, 8, ..., 31 for a 5-bit field) also spans
        # both quadrants despite never actually going negative -- treating
        # it as signed would introduce an artificial jump right where the
        # raw value crosses 15 -> 16 (found on a real merged application
        # signal: total frame-to-frame "jumpiness" more than doubled under
        # the signed reading versus the plain unsigned one). Only commit to
        # signed if that reinterpretation is actually smoother, not just
        # numerically possible.
        sign_bit = 1 << (length - 1)
        low_threshold = sign_bit // 2
        high_threshold = sign_bit + sign_bit // 2
        is_signed = (
            any(v < low_threshold for v in unique)
            and any(v >= high_threshold for v in unique)
            and TraceReverseEngineer._signed_reading_is_smoother(raw_values, length)
        )

        if length in (32, 64):
            try:
                import struct
                float_candidates: list[float] = []
                for rv in unique:
                    try:
                        if length == 32:
                            v = struct.unpack(">f", struct.pack(">I", rv))[0]
                        else:
                            v = struct.unpack(">d", struct.pack(">Q", rv))[0]
                        if not (math.isnan(v) or math.isinf(v)):
                            float_candidates.append(v)
                    except Exception:
                        pass
                if float_candidates and all(
                    abs(v - round(v)) > 0.001 for v in float_candidates
                ):
                    return "Float", min(float_candidates), max(float_candidates), 1.0, 0.0, None, False
            except Exception:
                pass

        # factor/offset/min/max are all derived from the same "working"
        # values: sign-converted for a signed signal, raw bit pattern
        # otherwise -- and phys_min/phys_max use the identical v*factor+offset
        # formula _build_signal uses for physical_values, so the reported
        # Min/Max always match what the sample data actually shows.
        working = TraceReverseEngineer._to_signed(raw_values, length) if is_signed else raw_values
        w_min, w_max = min(working), max(working)
        if w_max != w_min:
            factor = (w_max - w_min) / full_range
            if factor <= 0:
                factor = 1.0
        else:
            factor = 1.0

        offset = float(w_min)
        phys_min = w_min * factor + offset
        phys_max = w_max * factor + offset

        value_type = "Signed" if is_signed else "Unsigned"
        return value_type, float(phys_min), float(phys_max), float(factor), offset, enum_vals, is_signed

    # ── Counter detection ─────────────────────────────────────────────

    # A wider candidate's "extra" high-order bits (beyond the next-narrower
    # canonical AUTOSAR width) must show real variation somewhere in the
    # capture before it's trusted as genuinely that wide -- see
    # _detect_counter's docstring for why.
    _COUNTER_NARROWER_WIDTH = {32: 8, 8: 4}

    @staticmethod
    def _detect_counter(raw_values: list[int], length: int) -> bool:
        """Detect if signal is a rolling AUTOSAR-style counter.

        A counter increments by 1 each frame and wraps at its own bit
        width -- AUTOSAR counters are always unsigned and only ever 4, 8,
        or 32 bits wide (masking wraparound to a fixed 8 bits regardless of
        the signal's actual length would misjudge any other width's wrap
        transition as a huge jump instead of +1). Restricting to those
        three widths also rules out the degenerate 1-bit case, which is
        indistinguishable from a plain boolean toggle under modular
        arithmetic: incrementing by 1 mod 2 *is* flipping the bit, so an
        ordinary 0,1,0,1,... flag would otherwise trivially score as a
        "counter" too.

        A parallel ambiguity exists one level up: a genuinely 4-bit counter
        packed into a byte whose other nibble is reserved/unused (constant
        0 for the whole capture) satisfies the 8-bit version of this same
        test too, since "+1 mod 256" and "+1 mod 16" are indistinguishable
        while the top nibble never changes. Because the widest-first scan
        order (_COUNTER_WIDTHS) would otherwise let that 8-bit claim win
        every time, require the wider width's extra high-order bits to
        actually take a nonzero value *somewhere* in the capture -- real
        evidence of a wider rollover, not just an unexercised window --
        before accepting it; otherwise defer to the narrower scan that
        runs after this one.
        """
        if length not in (4, 8, 32):
            return False
        if len(raw_values) < 5:
            return False

        mask = (1 << length) - 1
        diffs = [(raw_values[i] - raw_values[i - 1]) & mask for i in range(1, len(raw_values))]
        if not diffs:
            return False

        one_count = sum(1 for d in diffs if d == 1)
        if one_count / len(diffs) <= 0.7:
            return False

        narrower = TraceReverseEngineer._COUNTER_NARROWER_WIDTH.get(length)
        if narrower is not None and max(raw_values) < (1 << narrower):
            return False

        return True

    # ── Checksum candidate detection ──────────────────────────────────

    @staticmethod
    def _detect_checksum(
        raw_values: list[int],
        stats: CanIdStats,
        signal_bits: list[int],
    ) -> bool:
        """Detect if signal might be a checksum (weak heuristic)."""
        if not stats.payload_samples or len(raw_values) < 3:
            return False

        if len(signal_bits) < 8:
            return False

        max_len = max(len(p) for p in stats.payload_samples) if stats.payload_samples else 0
        signal_bytes = {b // 8 for b in signal_bits}
        other_bytes = [i for i in range(max_len) if i not in signal_bytes]

        if not other_bytes:
            return False

        # Check if signal correlates with XOR of other bytes
        xor_correlation = 0
        for i, payload in enumerate(stats.payload_samples):
            if i >= len(raw_values):
                break
            others_xor = 0
            for byte_idx in other_bytes:
                if byte_idx < len(payload):
                    others_xor ^= payload[byte_idx]
            expected = others_xor & ((1 << len(signal_bits)) - 1)
            if expected == raw_values[i]:
                xor_correlation += 1

        if len(raw_values) > 0 and xor_correlation / len(raw_values) > 0.5:
            return True
        return False

    # ── Confidence computation ────────────────────────────────────────

    @staticmethod
    def _compute_confidence(
        bits: list[int],
        raw_values: list[int],
        stats: CanIdStats,
        is_counter: bool,
        is_checksum: bool,
    ) -> float:
        """Compute a confidence score (0-1) for a discovered signal."""
        if not raw_values or not stats.payload_samples:
            return 0.0

        scores: list[float] = []

        if len(bits) >= 2:
            scores.append(0.8)
        else:
            scores.append(0.4)

        unique = len(set(raw_values))
        if unique >= 2:
            scores.append(min(1.0, unique / 10))
        else:
            scores.append(0.2)

        if stats.count >= 10:
            scores.append(1.0)
        elif stats.count >= 5:
            scores.append(0.7)
        else:
            scores.append(0.4)

        if is_counter:
            scores.append(0.9)
        if is_checksum:
            scores.append(0.6)

        return statistics.mean(scores) if scores else 0.5

    # ── Post-processing ───────────────────────────────────────────────

    @staticmethod
    def _post_process_signals(
        signals: list[DiscoveredSignal],
        stats: CanIdStats,
    ) -> list[DiscoveredSignal]:
        """Post-process signals: merge adjacent, fix overlaps, name counters."""
        if not signals:
            return []

        signals.sort(key=lambda s: s.start_pos)

        counter_idx = 0
        for sig in signals:
            if sig.is_counter:
                counter_idx += 1
                sig.name = f"Counter_{counter_idx}"
            elif sig.is_checksum:
                sig.name = sig.crc_algorithm if sig.crc_algorithm else "Checksum"
            elif sig.enum_values and len(sig.enum_values) <= 4:
                sig.name = f"State_{sig.id}"
            elif len(sig.raw_values) >= 3:
                sig.name = f"Signal_{sig.id}"

        return signals

    # ── Export to PDU database ────────────────────────────────────────

    @staticmethod
    def _to_dbc_start_bit(start_pos: int, length: int, byte_order: int) -> int:
        """Translate this module's own internal bit numbering (bit 0 = MSB
        of byte 0, ascending byte-major -- used throughout clustering/
        counter/CRC detection) into the DBC/Vector "StartPos" convention
        the PDU DB schema actually follows: dbc2boatjson.py copies a real
        DBC file's `start_bit` straight through with no reinterpretation,
        and boat/message.py's _pack_intel/_pack_motorola are the canonical
        consumers of that value -- both confirm the classic Vector
        convention, which is *not* the same numbering for both byte
        orders:

        - Motorola (1): StartPos is the signal's MSB position, itself
          numbered byte-major MSB0 -- i.e. exactly this module's own
          internal numbering already. No translation needed.
        - Intel (0): StartPos is instead the signal's *LSB* position,
          numbered LSB0 *within* each byte (byte_idx*8 + bit_offset_from_
          LSB) -- the mirror image, within the byte its low end sits in,
          of this module's MSB0 numbering. Getting this wrong doesn't
          affect internal analysis (which only uses its own numbering
          self-consistently) but silently exports a signal at the wrong
          bit offset for anything that reads the DBC-standard StartPos
          convention (real hardware traces, other DBC tooling).
        """
        if byte_order == 1:
            return start_pos
        low_byte = start_pos // 8
        lsb_internal_pos = min(start_pos + length - 1, low_byte * 8 + 7)
        return low_byte * 8 + (7 - (lsb_internal_pos % 8))

    def to_pdu_db(
        self,
        bus_mapping: dict[int, str] | None = None,
        message_names: dict[int, str] | None = None,
        result: ReverseEngineeringResult | None = None,
    ) -> dict:
        """Export reverse-engineered results as a PDU database dict.

        Args:
            bus_mapping:  Map BLF channel number → bus name.
            message_names: Map CAN arbitration ID → message name.
            result: A pre-computed :class:`ReverseEngineeringResult` (from
                :meth:`reverse_engineer`), to avoid re-running the expensive
                bit-correlation clustering when the caller already has one.
                Runs :meth:`reverse_engineer` itself if omitted.

        Returns:
            A dict matching the PDU database schema.
        """
        result = result if result is not None else self.reverse_engineer()
        bus_mapping = bus_mapping or {}
        message_names = message_names or {}

        messages: list[dict] = []

        # Sequential DbId (matching TraceAnalyzer.to_pdu_db()'s scheme) --
        # deliberately not can_id + 1, so toggling signal reverse-engineering
        # on/off and re-exporting doesn't renumber every message.
        for db_id, msg in enumerate(result.messages, start=1):
            signals_list: list[dict] = []
            for sig in msg.signals:
                signals_list.append({
                    "id": sig.id,
                    "SignalName": sig.name,
                    "Length": sig.length,
                    "StartPos": self._to_dbc_start_bit(sig.start_pos, sig.length, sig.byte_order),
                    "ByteOrder": sig.byte_order,
                    "ValueType": sig.value_type,
                    "SigSendType": sig.is_counter or False,
                    "Repetitions": 0,
                    "InitValue": 0,
                    "Factor": sig.factor,
                    "Offset": sig.offset,
                    "Min": sig.min_val,
                    "Max": sig.max_val,
                    "Unit": sig.unit,
                    "EnumValues": sig.enum_values,
                    "IsMuxor": False,
                    "MuxValue": None,
                    "Comment": "",
                })

            messages.append({
                "DbId": db_id,
                "MessageName": message_names.get(
                    msg.can_id, msg.message_name
                ),
                "Bus": bus_mapping.get(msg.channel, msg.bus),
                "BusType": msg.bus_type,
                "MessageType": 0,
                "Direction": 0,
                "RoutingType": 0,
                "TargetDbIds": None,
                "SourceDbId": None,
                "isE2E": _e2e_profile_number(msg.e2e_profile),
                "SendType": msg.send_type,
                "CycleTime": int(msg.cycle_time_ms),
                "CycleTimeFast": 0,
                "NrOfRepetitions": 0,
                "Identifier": msg.identifier,
                "FrameType": 1 if msg.is_extended else 0,
                "Length": msg.length,
                "BRS": msg.is_fd,
                "signalcount": len(signals_list),
                "signals": signals_list,
                "Comment": "",
                "Node": "",
            })

        return {
            "schema_version": "1.0",
            "messages": messages,
            "signal_routes": [],
        }

    def save_pdu_db(self, path: str | Path, **kwargs) -> Path:
        """Reverse-engineer and save the derived PDU database to a JSON file."""
        import json
        pdu_db = self.to_pdu_db(**kwargs)
        out = Path(path)
        out.write_text(json.dumps(pdu_db, indent=2))
        return out
