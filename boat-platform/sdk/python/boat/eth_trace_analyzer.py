"""Bulk statistics engine for Ethernet (.pcap/.pcapng) trace analysis.

Mirrors :mod:`boat.trace_analyzer`'s CAN-side design -- one pass over the
capture produces per-flow and per-node statistics instead of attempting
per-signal reverse engineering (that's a CAN-specific problem; Ethernet
payloads already carry their own structure via DoIP/SOME/IP/etc.). What
this *does* produce, analogous to CAN's per-ID cycle time and Cyclic/
Spontaneous classification:

- Protocol identification: EtherType/VLAN histograms, well-known-port
  recognition (DoIP), and payload-shape recognition (SOME/IP header).
- Node/topology inventory: MAC and IP address inventories, which VLAN(s)
  each flow was observed on.
- TCP session reconstruction: SYN/SYN-ACK-based client/server role
  detection, merging both directions of a connection into one session
  record instead of two independent flows.
- Cyclic vs. event-driven classification per UDP flow, using the same
  underlying idea as CAN cycle-time detection (inter-frame gap
  consistency), generalized with a coefficient-of-variation threshold
  instead of a canonical-raster snap (Ethernet flows don't have an
  equivalent of CAN's small set of standard bus cycle times).

Usage::

    from boat.eth_trace_analyzer import EthTraceAnalyzer

    analyzer = EthTraceAnalyzer("capture.pcap")
    analysis = analyzer.analyze()
    summary = analyzer.to_summary()
"""

from __future__ import annotations

import socket
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from boat.trace_replay import EthernetPcapReader, TraceReplayError
from boat.pcapng import PcapngError, PcapngReader

# ── well-known automotive-Ethernet protocol signatures ──────────────────

DOIP_PORT = 13400
SOMEIP_SD_PORT = 30490

# SOME/IP MessageType values (ISO/PRS SOME/IP protocol spec) -- REQUEST,
# REQUEST_NO_RETURN, NOTIFICATION, REQUEST_ACK, their TP (segmented)
# variants (0x20-range), and RESPONSE/ERROR plus their TP variants.
_SOMEIP_MSG_TYPES = {0x00, 0x01, 0x02, 0x04, 0x20, 0x21, 0x22, 0x24, 0x80, 0x81, 0xA0, 0xA1}

ETHERTYPE_NAMES = {
    0x0800: "IPv4",
    0x0806: "ARP",
    0x86DD: "IPv6",
    0x8100: "VLAN",
    0x88A8: "QinQ",
    0x88A4: "EtherCAT",
    0x88F7: "gPTP",
    0x22F0: "AVTP",
    0x8917: "AVDECC",
    0x8892: "PROFINET",
}

IP_PROTO_NAMES = {1: "ICMP", 2: "IGMP", 6: "TCP", 17: "UDP", 58: "ICMPv6"}

# How many inter-frame gaps to keep per flow for cycle-time/jitter
# statistics -- bounded so a huge flow (hundreds of thousands of frames)
# doesn't blow up memory; a few hundred samples is plenty to judge
# periodicity.
_GAP_SAMPLE_LIMIT = 500

# How many frames per flow to run the SOME/IP header-shape check against --
# the header is either fixed-shape or it isn't, so a small sample is
# enough to decide, and it keeps the single analysis pass cheap even on
# flows with hundreds of thousands of frames.
_SOMEIP_CHECK_LIMIT = 20


def _looks_like_someip(payload: bytes) -> bool:
    """Check the fixed 16-byte SOME/IP header shape: MessageID(4) +
    Length(4) + RequestID(4) + ProtocolVersion(1) + InterfaceVersion(1) +
    MessageType(1) + ReturnCode(1). The Length field must exactly match
    the remaining payload size (everything after the Length field itself),
    ProtocolVersion is always 1, and MessageType is drawn from a small
    fixed set -- three independent, cheap checks unlikely to all pass by
    chance on a non-SOME/IP payload of the same size.
    """
    if len(payload) < 16:
        return False
    length_field = int.from_bytes(payload[4:8], "big")
    if length_field != len(payload) - 8:
        return False
    if payload[12] != 1:
        return False
    return payload[14] in _SOMEIP_MSG_TYPES


def someip_header_fields(payload: bytes) -> dict[str, int] | None:
    """Parse SOME/IP header fields if `payload` matches the header shape,
    else None."""
    if not _looks_like_someip(payload):
        return None
    return {
        "service_id": int.from_bytes(payload[0:2], "big"),
        "method_id": int.from_bytes(payload[2:4], "big"),
        "length": int.from_bytes(payload[4:8], "big"),
        "request_id": int.from_bytes(payload[8:12], "big"),
        "protocol_version": payload[12],
        "interface_version": payload[13],
        "message_type": payload[14],
        "return_code": payload[15],
    }


def _is_multicast_ip(ip: str) -> bool:
    return ip.startswith("ff") or ip.startswith("224.") or ip.startswith("23") and "." in ip and int(ip.split(".")[0]) in range(224, 240)


# ── AUTOSAR PDU-multiplex (SoAd PduHeader) detection ─────────────────────
#
# When AUTOSAR's SoAd (Socket Adaptor) is configured with PduHeaderEnable,
# every I-PDU sent over a UDP/TCP socket connection is prefixed with an
# 8-byte header (4-byte Header-ID + 4-byte Length), and multiple small,
# unrelated I-PDUs are commonly packed back-to-back into one datagram this
# way to cut per-message framing overhead. Recognized the same way as
# SOME/IP above: not by matching one lucky frame, but by requiring the
# Length fields to exactly, repeatedly consume the entire payload with no
# leftover bytes -- verified against a real trace: 141 flows matched this
# shape consistently across 30 sampled frames each, with small, sensible
# Header-IDs, while flows that don't actually use this framing fail on the
# very first frame (a wrong Length field almost never happens to leave
# exactly zero bytes over).

_PDU_MUX_HEADER_SIZE = 8  # 4-byte Header-ID + 4-byte Length


def _parse_pdu_multiplex(payload: bytes) -> list[tuple[int, bytes]] | None:
    """Parse `payload` as repeated AUTOSAR SoAd PDU records: 4-byte
    Header-ID + 4-byte Length + `Length` bytes of I-PDU data, repeated.
    Returns [(header_id, pdu_payload), ...] only if the *entire* payload
    is consumed exactly by one or more such records, else None. The
    canonical parser -- both the cheap Stage 1 shape-check
    (_try_parse_pdu_multiplex) and the Stage 2 per-PDU deep dive
    (EthTraceAnalyzer.find_autosar_pdus) use this.
    """
    pos, n = 0, len(payload)
    if n < _PDU_MUX_HEADER_SIZE:
        return None
    records: list[tuple[int, bytes]] = []
    while pos < n:
        if pos + _PDU_MUX_HEADER_SIZE > n:
            return None
        header_id = int.from_bytes(payload[pos:pos + 4], "big")
        length = int.from_bytes(payload[pos + 4:pos + 8], "big")
        pos += _PDU_MUX_HEADER_SIZE
        if length == 0 or pos + length > n:
            return None
        records.append((header_id, payload[pos:pos + length]))
        pos += length
    return records if records else None


def _try_parse_pdu_multiplex(payload: bytes) -> list[tuple[int, int]] | None:
    """Shape-check wrapper around :func:`_parse_pdu_multiplex` returning
    just (header_id, length) pairs -- used in Stage 1's hot path, where
    only confirming the shape (and each record's length) is needed, not
    the actual payload bytes."""
    records = _parse_pdu_multiplex(payload)
    if records is None:
        return None
    return [(hid, len(data)) for hid, data in records]


# How many frames per flow to run the PDU-multiplex shape check against --
# same reasoning as _SOMEIP_CHECK_LIMIT.
_PDU_MUX_CHECK_LIMIT = 20


# ── gPTP (IEEE 802.1AS) ───────────────────────────────────────────────────
#
# Common PTP/gPTP message header (34 bytes) as defined in IEEE 1588-2008 /
# 802.1AS-2011: byte 0's low nibble is messageType, bytes 20-27 are the
# sending port's clockIdentity (an EUI-64, usually derived from a MAC
# address by inserting FF:FE after the OUI -- see _mac_from_clock_identity),
# bytes 28-29 the portNumber, bytes 30-31 a per-(port,messageType)
# sequenceId, byte 33 logMessageInterval (signed, log2 seconds). Verified
# against a real trace: byte offsets for messageLength/clockIdentity/
# sequenceId/logMessageInterval all decoded to self-consistent, sensible
# values (messageLength exactly matching header+body size, clockIdentity
# resolving back to a real observed MAC address, logMessageInterval
# decoding to a plausible interval).

_GPTP_MSGTYPE_NAMES = {
    0x0: "Sync", 0x1: "Delay_Req", 0x2: "Pdelay_Req", 0x3: "Pdelay_Resp",
    0x8: "Follow_Up", 0x9: "Delay_Resp", 0xA: "Pdelay_Resp_Follow_Up",
    0xB: "Announce", 0xC: "Signaling", 0xD: "Management",
}

# Message types that carry a "requesting port identity" in their body,
# used to correlate a Pdelay_Resp / Pdelay_Resp_Follow_Up back to the
# Pdelay_Req that triggered it (see _process_gptp).
_GPTP_PDELAY_REQ = 0x2
_GPTP_PDELAY_RESP = 0x3
_GPTP_PDELAY_RESP_FOLLOW_UP = 0xA
_GPTP_SYNC = 0x0

# How many samples (sync intervals, Pdelay turnaround times, etc.) to keep
# per port/link for statistics -- same bounding rationale as elsewhere.
_GPTP_SAMPLE_LIMIT = 2000


def _parse_ptp_timestamp(b: bytes) -> float:
    """PTP timestamp: 48-bit seconds + 32-bit nanoseconds, both big-endian."""
    seconds = int.from_bytes(b[0:6], "big")
    nanoseconds = int.from_bytes(b[6:10], "big")
    return seconds + nanoseconds / 1e9


def _parse_gptp_header(payload: bytes) -> dict[str, Any] | None:
    if len(payload) < 34:
        return None
    return {
        "msg_type": payload[0] & 0x0F,
        "domain": payload[4],
        "correction_ns": int.from_bytes(payload[8:16], "big", signed=True) / 65536.0,
        "clock_identity": payload[20:28].hex(":"),
        "port_number": int.from_bytes(payload[28:30], "big"),
        "sequence_id": int.from_bytes(payload[30:32], "big"),
        "log_interval": int.from_bytes(payload[33:34], "big", signed=True),
        "body": payload[34:],
    }


def _mac_from_clock_identity(clock_identity: str) -> str | None:
    """Recover the original MAC address from an EUI-64-derived clockIdentity
    (OUI + FF:FE + NIC-specific bytes), or None if it doesn't follow that
    pattern (some stacks use other clockIdentity derivations)."""
    parts = clock_identity.split(":")
    if len(parts) != 8 or parts[3:5] != ["ff", "fe"]:
        return None
    return ":".join(parts[0:3] + parts[5:8])


# ── MLD (Multicast Listener Discovery, RFC 2710/3810) ────────────────────
#
# The only way to observe actual multicast group *membership* (as opposed
# to just who *sends* to a group, which every captured packet already
# shows) from a passive capture: a node that wants to receive traffic for
# a group sends an MLD Report for it. Not exercised by any trace analyzed
# so far (no MLD traffic was present), so this is implemented from the RFC
# rather than verified against a real capture -- degrades safely (fails
# the length/bounds checks and is simply skipped) on anything malformed.

_MLDV1_REPORT = 131
_MLDV2_REPORT = 143


def _parse_mld_report_addresses(icmpv6_body: bytes, icmp_type: int) -> list[str]:
    """Extract the multicast address(es) a node is reporting interest in.
    MLDv1 Report (RFC 2710): one address at a fixed offset. MLDv2 Report
    (RFC 3810): a list of multicast-address-records: best-effort parse of
    each record's own address field, skipping over its source-address list
    and auxiliary data via the record's own length fields.
    """
    addrs: list[str] = []
    if icmp_type == _MLDV1_REPORT:
        if len(icmpv6_body) >= 24:
            addrs.append(socket.inet_ntop(socket.AF_INET6, icmpv6_body[8:24]))
    elif icmp_type == _MLDV2_REPORT:
        if len(icmpv6_body) < 8:
            return addrs
        n_records = int.from_bytes(icmpv6_body[6:8], "big")
        pos = 8
        for _ in range(n_records):
            if pos + 20 > len(icmpv6_body):
                break
            aux_data_len = icmpv6_body[pos + 1]  # in 32-bit words
            n_sources = int.from_bytes(icmpv6_body[pos + 2:pos + 4], "big")
            mcast_addr = icmpv6_body[pos + 4:pos + 20]
            if len(mcast_addr) == 16:
                addrs.append(socket.inet_ntop(socket.AF_INET6, mcast_addr))
            pos += 20 + n_sources * 16 + aux_data_len * 4
    return addrs


# ── per-flow / per-session statistics ────────────────────────────────────

@dataclass
class FlowStats:
    """Statistics for one UDP flow -- a (src_ip, dst_ip, src_port, dst_port)
    4-tuple. UDP is connectionless, so unlike TCP there's no session
    merging: each 4-tuple is its own logical channel (this matches how
    SOME/IP/DoIP-UDP endpoints actually behave -- a sender's port is
    typically fixed per logical channel, not a fresh ephemeral one per
    "connection" the way a new TCP socket would be)."""
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    vlan_ids: set[int] = field(default_factory=set)
    frame_count: int = 0
    byte_count: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    someip_like_count: int = 0
    someip_checked_count: int = 0
    pdu_mux_like_count: int = 0
    pdu_mux_checked_count: int = 0
    pdu_ids_seen: set[int] = field(default_factory=set)
    _last_ts_for_gap: float = field(default=0.0, repr=False)
    _gap_samples: list[float] = field(default_factory=list, repr=False)

    @property
    def is_multicast_dst(self) -> bool:
        return _is_multicast_ip(self.dst_ip)

    @property
    def is_doip_port(self) -> bool:
        return self.src_port == DOIP_PORT or self.dst_port == DOIP_PORT

    @property
    def is_someip_sd(self) -> bool:
        return self.src_port == SOMEIP_SD_PORT or self.dst_port == SOMEIP_SD_PORT

    @property
    def is_someip_like(self) -> bool:
        """True if most sampled payloads on this flow match the SOME/IP
        header shape -- "most" rather than "all" since a flow can carry a
        handful of malformed/truncated frames without that meaning the
        protocol guess is wrong."""
        return self.someip_checked_count > 0 and (
            self.someip_like_count / self.someip_checked_count > 0.8
        )

    @property
    def is_pdu_multiplex(self) -> bool:
        """True if most sampled payloads consistently parse as repeated
        AUTOSAR SoAd (Header-ID + Length + data) records with nothing left
        over -- see _try_parse_pdu_multiplex."""
        return self.pdu_mux_checked_count > 0 and (
            self.pdu_mux_like_count / self.pdu_mux_checked_count > 0.8
        )

    @property
    def duration_s(self) -> float:
        return max(0.0, self.last_ts - self.first_ts)

    @property
    def cycle_time_ms(self) -> float:
        if len(self._gap_samples) < 3:
            return 0.0
        return statistics.mean(self._gap_samples) * 1000

    @property
    def cycle_jitter_cv(self) -> float:
        """Coefficient of variation of inter-frame gaps: near 0 means
        strictly periodic (a "Cyclic" CAN-style signal), high means
        bursty/multiplexed/event-driven traffic."""
        if len(self._gap_samples) < 3:
            return 0.0
        mean = statistics.mean(self._gap_samples)
        if mean <= 0:
            return 0.0
        return statistics.stdev(self._gap_samples) / mean

    @property
    def send_type(self) -> str:
        if self.frame_count < 5:
            return "Spontaneous"
        return "Cyclic" if self.cycle_jitter_cv < 0.3 else "Bursty"


@dataclass
class TcpSession:
    """A reconstructed TCP connection -- both directions merged via
    SYN/SYN-ACK role detection into one record, rather than left as two
    independent, direction-specific flows."""
    endpoint_a: tuple[str, int]
    endpoint_b: tuple[str, int]
    vlan_ids: set[int] = field(default_factory=set)
    frames_a_to_b: int = 0
    frames_b_to_a: int = 0
    bytes_a_to_b: int = 0
    bytes_b_to_a: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    client_endpoint: tuple[str, int] | None = None   # sender of the bare SYN
    server_endpoint: tuple[str, int] | None = None   # sender of the SYN-ACK
    saw_fin_or_rst: bool = False

    @property
    def role_confidence(self) -> str:
        return "confirmed" if self.client_endpoint and self.server_endpoint else "unknown"

    @property
    def is_doip(self) -> bool:
        return self.endpoint_a[1] == DOIP_PORT or self.endpoint_b[1] == DOIP_PORT

    @property
    def total_frames(self) -> int:
        return self.frames_a_to_b + self.frames_b_to_a


@dataclass
class PduChannelStats:
    """Statistics for one AUTOSAR PDU (Header-ID) as observed on ONE
    particular UDP flow -- an intermediate, per-flow view used before
    multi-flow dedup (see PduStats / EthTraceAnalyzer.find_autosar_pdus),
    analogous to CAN's per-(id, channel) stats before
    _select_original_channel picks the real source."""
    header_id: int
    flow_key: tuple
    length_values: list[int] = field(default_factory=list)
    payload_samples: list[bytes] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)
    count: int = 0


@dataclass
class PduStats:
    """Statistics for one AUTOSAR PDU (Header-ID) after multi-flow dedup:
    the winning ("original") flow's own data, with any other flow it was
    also observed on recorded as a likely relay/routed duplicate --
    directly analogous to CAN's CanIdStats.duplicate_channels. No
    signal-level decoding of the payload is attempted here -- Header-ID,
    length, raw payload samples, and sending behavior only.
    """
    header_id: int
    flow_key: tuple
    length_values: list[int] = field(default_factory=list)
    payload_samples: list[bytes] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)
    duplicate_flows: dict[tuple, int] = field(default_factory=dict)  # other flow_key -> frame count

    @property
    def count(self) -> int:
        return len(self.timestamps)

    @property
    def length_is_stable(self) -> bool:
        return len(set(self.length_values)) <= 1

    @property
    def duration_s(self) -> float:
        if len(self.timestamps) < 2:
            return 0.0
        return max(0.0, self.timestamps[-1] - self.timestamps[0])

    @property
    def cycle_time_ms(self) -> float:
        if len(self.timestamps) < 3:
            return 0.0
        gaps = [self.timestamps[i] - self.timestamps[i - 1] for i in range(1, len(self.timestamps))]
        return statistics.mean(gaps) * 1000

    @property
    def cycle_jitter_cv(self) -> float:
        if len(self.timestamps) < 3:
            return 0.0
        gaps = [self.timestamps[i] - self.timestamps[i - 1] for i in range(1, len(self.timestamps))]
        mean = statistics.mean(gaps)
        if mean <= 0:
            return 0.0
        return statistics.stdev(gaps) / mean

    @property
    def send_type(self) -> str:
        if self.count < 5:
            return "Spontaneous"
        return "Cyclic" if self.cycle_jitter_cv < 0.3 else "Bursty"


def _select_original_pdu_flow(candidates: dict[tuple, PduChannelStats]) -> tuple[tuple, PduChannelStats]:
    """Among several UDP flows all carrying the same AUTOSAR PDU
    Header-ID, pick the "original" source -- directly analogous to
    :mod:`boat.trace_analyzer`'s CAN-side ``_select_original_channel``:
    for each distinct payload value observed, note which flow's copy of
    it appeared earliest; the flow that leads most often is the original,
    the rest are downstream relay/routed duplicates (verified on a real
    trace: a gateway node's copy of the same PDU consistently arrives
    10-50ms before a second node's copy of the identical bytes -- the
    same relay pattern found on the CAN side). Falls back to the flow
    with the most frames if no comparable value transitions exist (e.g.
    every candidate's payload happens to be constant throughout).
    """
    if len(candidates) == 1:
        only_key = next(iter(candidates))
        return only_key, candidates[only_key]

    first_seen_by_flow: dict[tuple, dict[bytes, float]] = {}
    for flow_key, stats in candidates.items():
        seen: dict[bytes, float] = {}
        for ts, payload in zip(stats.timestamps, stats.payload_samples):
            if payload not in seen:
                seen[payload] = ts
        first_seen_by_flow[flow_key] = seen

    all_values: set[bytes] = set()
    for seen in first_seen_by_flow.values():
        all_values.update(seen.keys())

    lead_counts: Counter = Counter()
    for value in all_values:
        timings = [
            (flow_key, seen[value]) for flow_key, seen in first_seen_by_flow.items() if value in seen
        ]
        if len(timings) < 2:
            continue
        leader = min(timings, key=lambda kv: kv[1])[0]
        lead_counts[leader] += 1

    if lead_counts:
        winner = lead_counts.most_common(1)[0][0]
    else:
        winner = max(candidates.keys(), key=lambda k: candidates[k].count)

    return winner, candidates[winner]


@dataclass
class MulticastGroupStats:
    """Statistics for one multicast (address, port) channel. Grouped by
    port as well as address, not address alone -- a single multicast IP
    can legitimately carry several distinct logical channels on different
    ports (e.g. SOME/IP-SD conventionally uses both 30490 and 30491 on
    the same group address; treating those as one channel would credit
    each with senders that actually belong to the other). `port` is None
    for multicast traffic that isn't UDP/TCP (e.g. some ICMPv6 types are
    legitimately multicast-addressed).

    A multicast address is a *group*, not a node -- it has no single
    sender/owner, and (short of seeing an MLD Report) a passive capture
    can only observe who *sends* to it, not who's actually listening.
    """
    address: str
    port: int | None
    vlan_ids: set[int] = field(default_factory=set)
    frame_count: int = 0
    byte_count: int = 0
    sender_ips: set[str] = field(default_factory=set)


@dataclass
class GptpPortStats:
    """Statistics for one gPTP port, identified by (clockIdentity,
    portNumber) -- a single device can have multiple ports, each
    independently running the peer-delay mechanism."""
    clock_identity: str
    port_number: int
    msg_type_counts: Counter = field(default_factory=Counter)
    sync_timestamps: list[float] = field(default_factory=list)       # capture ts, bounded
    correction_ns_samples: list[float] = field(default_factory=list)  # from Sync/Follow_Up, bounded
    _last_seq_by_type: dict[int, int] = field(default_factory=dict, repr=False)
    seq_gap_count: int = 0
    seq_total_count: int = 0


@dataclass
class GptpLinkStats:
    """A directly-connected port-to-port link, inferred from a completed
    Pdelay_Req / Pdelay_Resp / Pdelay_Resp_Follow_Up exchange -- gPTP's
    peer-delay mechanism only ever runs between two ports on the same
    physical (or point-to-point logical) link, so a resolved exchange is
    real evidence of a direct connection, not just "these two nodes talked
    at some point"."""
    port_a: tuple[str, int]
    port_b: tuple[str, int]
    exchange_count: int = 0
    turnaround_ns_samples: list[float] = field(default_factory=list)      # exact: t3 - t2, from embedded timestamps
    capture_rtt_ms_samples: list[float] = field(default_factory=list)     # approximate: our own capture-time Req->Resp gap


@dataclass
class EthTraceAnalysis:
    """Result of analyzing an Ethernet .pcap trace file."""
    path: str
    total_frames: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    ethertype_counts: Counter = field(default_factory=Counter)
    vlan_counts: Counter = field(default_factory=Counter)
    mac_src_counts: Counter = field(default_factory=Counter)
    mac_dst_counts: Counter = field(default_factory=Counter)
    ip_proto_counts: Counter = field(default_factory=Counter)
    mac_to_ips: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    ip_frames_sent: Counter = field(default_factory=Counter)
    ip_frames_received: Counter = field(default_factory=Counter)
    ip_vlan_seen: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    udp_flows: dict[tuple, FlowStats] = field(default_factory=dict)
    tcp_sessions: dict[tuple, TcpSession] = field(default_factory=dict)
    someip_catalog: Counter = field(default_factory=Counter)  # (service_id, method_id) -> count
    autosar_pdu_catalog: Counter = field(default_factory=Counter)  # header_id -> frame count
    multicast_groups: dict[tuple, MulticastGroupStats] = field(default_factory=dict)  # (address, port|None) -> stats
    # MLD-confirmed group membership is address-scoped (MLD itself has no
    # concept of "port"), tracked separately from the per-(address,port)
    # channel stats above and merged into every matching channel's
    # summary -- see multicast_group_summary().
    mcast_confirmed_members: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    gptp_ports: dict[tuple, GptpPortStats] = field(default_factory=dict)
    gptp_links: dict[tuple, GptpLinkStats] = field(default_factory=dict)
    # Pending Pdelay_Req / Pdelay_Resp exchanges awaiting their matching
    # message before a link can be resolved (see _process_gptp) -- part of
    # the analysis state, not a result field, but kept here rather than as
    # a separate analyzer-instance attribute so a single EthTraceAnalysis
    # is always self-contained.
    _pending_pdelay_req: dict[tuple, tuple[float, float]] = field(default_factory=dict, repr=False)
    _pending_pdelay_resp: dict[tuple, tuple[str, int, float, float]] = field(default_factory=dict, repr=False)
    errors: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.last_ts - self.first_ts)


class _EthOnlyPcapngReader:
    """Filters a ``PcapngReader`` down to Ethernet-only records.

    This analyzer is Ethernet-only; a ``.pcapng`` file may also carry CAN
    interfaces (mixed-bus is the whole point of pcapng), so CAN records are
    silently skipped here rather than surfaced to ``_process_frame``/
    ``find_autosar_pdus``, which expect an Ethernet-frame shape
    (``.dst_mac``/``.src_mac``/``.ethertype``/``.payload``/``.timestamp``).
    """

    def __init__(self, path: str) -> None:
        self._reader = PcapngReader(path)

    def __enter__(self) -> "_EthOnlyPcapngReader":
        return self

    def __exit__(self, *args) -> None:
        self._reader.__exit__(*args)

    def __iter__(self) -> "_EthOnlyPcapngReader":
        return self

    def __next__(self):
        while True:
            record = next(self._reader)
            if hasattr(record, "ethertype"):
                return record


# ── analyzer ──────────────────────────────────────────────────────────────

class EthTraceAnalyzer:
    """Bulk-analyze an Ethernet .pcap/.pcapng capture.

    Args:
        path: Path to a DLT_EN10MB pcap file, or a pcapng file (only its
              Ethernet interfaces are analyzed; any CAN interfaces are
              skipped).
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._analysis: EthTraceAnalysis | None = None

    def _open_eth_reader(self):
        """Return an Ethernet-frame-yielding, context-manager-compatible
        reader appropriate for ``self._path``'s suffix."""
        if self._path.lower().endswith(".pcapng"):
            return _EthOnlyPcapngReader(self._path)
        return EthernetPcapReader(self._path)

    def analyze(self) -> EthTraceAnalysis:
        """Single pass over the capture. Builds protocol/VLAN histograms,
        node inventories, UDP flow stats (with cyclic/event
        classification and SOME/IP recognition), and TCP session
        reconstruction (with client/server role detection)."""
        result = EthTraceAnalysis(path=self._path)

        try:
            with self._open_eth_reader() as reader:
                for frame in reader:
                    self._process_frame(frame, result)
        except (TraceReplayError, PcapngError) as e:
            result.errors.append(str(e))

        self._analysis = result
        return result

    def _process_frame(self, frame, result: EthTraceAnalysis) -> None:
        result.total_frames += 1
        if result.first_ts == 0.0:
            result.first_ts = frame.timestamp
        result.last_ts = frame.timestamp

        result.mac_src_counts[frame.src_mac] += 1
        result.mac_dst_counts[frame.dst_mac] += 1

        payload, ethertype = frame.payload, frame.ethertype
        vlan_id: int | None = None
        # Peel up to two stacked VLAN tags (QinQ); track the outermost
        # ethertype seen for the histogram, and the last (innermost) VLAN
        # ID as "the" VLAN this frame was observed on.
        for _ in range(2):
            if ethertype == 0x8100 and len(payload) >= 4:
                tci = (payload[0] << 8) | payload[1]
                vlan_id = tci & 0x0FFF
                result.vlan_counts[vlan_id] += 1
                ethertype = (payload[2] << 8) | payload[3]
                payload = payload[4:]
            else:
                break

        result.ethertype_counts[ethertype] += 1

        if ethertype == 0x0800:
            self._process_ipv4(payload, frame.src_mac, vlan_id, frame.timestamp, result)
        elif ethertype == 0x86DD:
            self._process_ipv6(payload, frame.src_mac, vlan_id, frame.timestamp, result)
        elif ethertype == 0x88F7:
            self._process_gptp(payload, frame.timestamp, result)

    def _process_ipv4(self, payload: bytes, src_mac: bytes, vlan_id: int | None, ts: float, result: EthTraceAnalysis) -> None:
        if len(payload) < 20:
            return
        ihl = (payload[0] & 0x0F) * 4
        if ihl < 20 or len(payload) < ihl:
            return
        proto = payload[9]
        src_ip = socket.inet_ntoa(payload[12:16])
        dst_ip = socket.inet_ntoa(payload[16:20])
        self._process_ip_common(payload[ihl:], proto, src_ip, dst_ip, src_mac, vlan_id, ts, result)

    def _process_ipv6(self, payload: bytes, src_mac: bytes, vlan_id: int | None, ts: float, result: EthTraceAnalysis) -> None:
        if len(payload) < 40:
            return
        proto = payload[6]
        src_ip = socket.inet_ntop(socket.AF_INET6, payload[8:24])
        dst_ip = socket.inet_ntop(socket.AF_INET6, payload[24:40])
        self._process_ip_common(payload[40:], proto, src_ip, dst_ip, src_mac, vlan_id, ts, result)

    def _process_ip_common(
        self, l4: bytes, proto: int, src_ip: str, dst_ip: str, src_mac: bytes, vlan_id: int | None, ts: float,
        result: EthTraceAnalysis,
    ) -> None:
        result.ip_proto_counts[proto] += 1
        result.mac_to_ips[src_mac.hex(":")].add(src_ip)

        # Determined up front (not inside _process_udp/_process_tcp) since
        # multicast-group bookkeeping needs it too -- a single multicast
        # address can carry several distinct logical channels on
        # different ports (e.g. SOME/IP-SD's 30490 and 30491 on the same
        # group address), so grouping by address alone would wrongly
        # attribute one channel's senders to the other.
        dst_port: int | None = None
        if proto in (6, 17) and len(l4) >= 4:
            dst_port = (l4[2] << 8) | l4[3]

        is_group = _is_multicast_ip(dst_ip)
        if is_group:
            group_key = (dst_ip, dst_port)
            group = result.multicast_groups.get(group_key)
            if group is None:
                group = MulticastGroupStats(address=dst_ip, port=dst_port)
                result.multicast_groups[group_key] = group
            group.frame_count += 1
            group.byte_count += len(l4)
            group.sender_ips.add(src_ip)
            if vlan_id is not None:
                group.vlan_ids.add(vlan_id)
            # The sender is a real unicast node (it has its own address);
            # only the *destination* here is a group, so only the sender
            # side counts toward the unicast node inventory.
            result.ip_frames_sent[src_ip] += 1
            if vlan_id is not None:
                result.ip_vlan_seen[src_ip].add(vlan_id)
        else:
            result.ip_frames_sent[src_ip] += 1
            result.ip_frames_received[dst_ip] += 1
            if vlan_id is not None:
                result.ip_vlan_seen[src_ip].add(vlan_id)
                result.ip_vlan_seen[dst_ip].add(vlan_id)

        if proto == 17 and len(l4) >= 8:
            self._process_udp(l4, src_ip, dst_ip, vlan_id, ts, result)
        elif proto == 6 and len(l4) >= 14:
            self._process_tcp(l4, src_ip, dst_ip, vlan_id, ts, result)
        elif proto == 58 and len(l4) >= 4:
            self._process_icmpv6(l4, src_ip, result)

    def _process_icmpv6(self, icmpv6: bytes, src_ip: str, result: EthTraceAnalysis) -> None:
        icmp_type = icmpv6[0]
        if icmp_type not in (_MLDV1_REPORT, _MLDV2_REPORT):
            return
        for addr in _parse_mld_report_addresses(icmpv6[4:], icmp_type):
            result.mcast_confirmed_members[addr].add(src_ip)

    def _process_udp(
        self, l4: bytes, src_ip: str, dst_ip: str, vlan_id: int | None, ts: float,
        result: EthTraceAnalysis,
    ) -> None:
        sport = (l4[0] << 8) | l4[1]
        dport = (l4[2] << 8) | l4[3]
        key = (src_ip, dst_ip, sport, dport)
        flow = result.udp_flows.get(key)
        if flow is None:
            flow = FlowStats(src_ip=src_ip, dst_ip=dst_ip, src_port=sport, dst_port=dport, first_ts=ts)
            result.udp_flows[key] = flow

        if flow.frame_count > 0 and len(flow._gap_samples) < _GAP_SAMPLE_LIMIT:
            gap = ts - flow._last_ts_for_gap
            if gap >= 0:
                flow._gap_samples.append(gap)
        flow._last_ts_for_gap = ts
        flow.frame_count += 1
        flow.byte_count += len(l4)
        flow.last_ts = ts
        if vlan_id is not None:
            flow.vlan_ids.add(vlan_id)

        udp_payload = l4[8:]
        matched_someip = False
        if flow.someip_checked_count < _SOMEIP_CHECK_LIMIT:
            flow.someip_checked_count += 1
            fields = someip_header_fields(udp_payload)
            if fields is not None:
                matched_someip = True
                flow.someip_like_count += 1
                result.someip_catalog[(fields["service_id"], fields["method_id"])] += 1

        # Mutually exclusive with SOME/IP -- a frame that already matched
        # SOME/IP's header shape isn't also tested against PDU-multiplex,
        # to avoid a coincidental double-classification.
        if not matched_someip and flow.pdu_mux_checked_count < _PDU_MUX_CHECK_LIMIT:
            flow.pdu_mux_checked_count += 1
            records = _try_parse_pdu_multiplex(udp_payload)
            if records is not None:
                flow.pdu_mux_like_count += 1
                for header_id, _length in records:
                    flow.pdu_ids_seen.add(header_id)
                    result.autosar_pdu_catalog[header_id] += 1

    def _process_tcp(
        self, l4: bytes, src_ip: str, dst_ip: str, vlan_id: int | None, ts: float,
        result: EthTraceAnalysis,
    ) -> None:
        sport = (l4[0] << 8) | l4[1]
        dport = (l4[2] << 8) | l4[3]
        flags = l4[13]
        payload_len = len(l4) - ((l4[12] >> 4) * 4)

        a, b = (src_ip, sport), (dst_ip, dport)
        key = tuple(sorted((a, b)))
        sess = result.tcp_sessions.get(key)
        if sess is None:
            sess = TcpSession(endpoint_a=key[0], endpoint_b=key[1], first_ts=ts)
            result.tcp_sessions[key] = sess

        sess.last_ts = ts
        if vlan_id is not None:
            sess.vlan_ids.add(vlan_id)
        if a == sess.endpoint_a:
            sess.frames_a_to_b += 1
            sess.bytes_a_to_b += max(0, payload_len)
        else:
            sess.frames_b_to_a += 1
            sess.bytes_b_to_a += max(0, payload_len)

        syn = bool(flags & 0x02)
        ack = bool(flags & 0x10)
        fin = bool(flags & 0x01)
        rst = bool(flags & 0x04)
        if syn and not ack and sess.client_endpoint is None:
            sess.client_endpoint = a
        elif syn and ack and sess.server_endpoint is None:
            sess.server_endpoint = a
        if fin or rst:
            sess.saw_fin_or_rst = True

    def _process_gptp(self, payload: bytes, ts: float, result: EthTraceAnalysis) -> None:
        header = _parse_gptp_header(payload)
        if header is None:
            return
        port_key = (header["clock_identity"], header["port_number"])
        port = result.gptp_ports.get(port_key)
        if port is None:
            port = GptpPortStats(clock_identity=header["clock_identity"], port_number=header["port_number"])
            result.gptp_ports[port_key] = port

        msg_type = header["msg_type"]
        port.msg_type_counts[msg_type] += 1

        # sequenceId continuity, per (port, messageType) -- a gap suggests
        # either a lost message on the wire or a frame our own capture
        # missed; can't tell those apart from the capture alone, so this
        # is reported as "gaps observed", not "network problems confirmed".
        port.seq_total_count += 1
        seq_id = header["sequence_id"]
        last_seq = port._last_seq_by_type.get(msg_type)
        if last_seq is not None and (last_seq + 1) & 0xFFFF != seq_id:
            port.seq_gap_count += 1
        port._last_seq_by_type[msg_type] = seq_id

        if msg_type == _GPTP_SYNC and len(port.sync_timestamps) < _GPTP_SAMPLE_LIMIT:
            port.sync_timestamps.append(ts)

        if msg_type in (_GPTP_SYNC, 0x8) and len(port.correction_ns_samples) < _GPTP_SAMPLE_LIMIT:
            port.correction_ns_samples.append(header["correction_ns"])

        body = header["body"]

        if msg_type == _GPTP_PDELAY_REQ and len(body) >= 10:
            t1 = _parse_ptp_timestamp(body[0:10])
            result._pending_pdelay_req[(port_key, seq_id)] = (t1, ts)

        elif msg_type == _GPTP_PDELAY_RESP and len(body) >= 20:
            t2 = _parse_ptp_timestamp(body[0:10])
            requester_key = (body[10:18].hex(":"), int.from_bytes(body[18:20], "big"))
            result._pending_pdelay_resp[(requester_key, seq_id)] = (port_key[0], port_key[1], t2, ts)

        elif msg_type == _GPTP_PDELAY_RESP_FOLLOW_UP and len(body) >= 20:
            t3 = _parse_ptp_timestamp(body[0:10])
            requester_key = (body[10:18].hex(":"), int.from_bytes(body[18:20], "big"))
            corr_key = (requester_key, seq_id)
            req = result._pending_pdelay_req.pop(corr_key, None)
            resp = result._pending_pdelay_resp.pop(corr_key, None)
            if req is not None and resp is not None:
                t1, req_ts = req
                responder_clock, responder_port, t2, resp_ts = resp
                link_key = tuple(sorted([requester_key, (responder_clock, responder_port)]))
                link = result.gptp_links.get(link_key)
                if link is None:
                    link = GptpLinkStats(port_a=link_key[0], port_b=link_key[1])
                    result.gptp_links[link_key] = link
                link.exchange_count += 1
                if len(link.turnaround_ns_samples) < _GPTP_SAMPLE_LIMIT:
                    link.turnaround_ns_samples.append((t3 - t2) * 1e9)
                    link.capture_rtt_ms_samples.append((ts - req_ts) * 1000)

    # ── node inventory (derived view, not tracked during the pass) ──────

    def doip_servers(self, result: EthTraceAnalysis | None = None) -> list[str]:
        """IPs that responded to a DoIP (port 13400) SYN with a SYN-ACK --
        i.e. confirmed DoIP servers, not just anything that ever touched
        the port."""
        result = result or self._analysis
        if result is None:
            raise RuntimeError("Call analyze() first")
        servers = set()
        for sess in result.tcp_sessions.values():
            if sess.server_endpoint and sess.server_endpoint[1] == DOIP_PORT:
                servers.add(sess.server_endpoint[0])
        return sorted(servers)

    def doip_clients(self, result: EthTraceAnalysis | None = None, min_distinct_servers: int = 3) -> dict[str, int]:
        """IPs that opened a client-role connection to at least
        `min_distinct_servers` distinct confirmed DoIP servers -- a
        systematic sweep across many ECUs is a strong behavioral
        signature of a diagnostic tester, as opposed to an ECU that just
        happens to also dial one other ECU's DoIP port. Returns
        {ip: distinct_server_count}.
        """
        result = result or self._analysis
        if result is None:
            raise RuntimeError("Call analyze() first")
        servers_by_client: dict[str, set[str]] = defaultdict(set)
        for sess in result.tcp_sessions.values():
            if sess.client_endpoint and sess.server_endpoint and sess.server_endpoint[1] == DOIP_PORT:
                servers_by_client[sess.client_endpoint[0]].add(sess.server_endpoint[0])
        return {ip: len(s) for ip, s in servers_by_client.items() if len(s) >= min_distinct_servers}

    def _build_node_labels(self, result: EthTraceAnalysis) -> dict[str, str]:
        """Stable short labels ("N1", "N2", ...) for every unicast IP,
        ordered by total traffic descending (ties broken by address, so
        the assignment is deterministic across repeated calls) -- so the
        busiest nodes get the lowest, easiest-to-remember numbers."""
        ips = set(result.ip_frames_sent) | set(result.ip_frames_received)
        ordered = sorted(
            ips,
            key=lambda ip: (-(result.ip_frames_sent.get(ip, 0) + result.ip_frames_received.get(ip, 0)), ip),
        )
        return {ip: f"N{i + 1}" for i, ip in enumerate(ordered)}

    def node_inventory(self, result: EthTraceAnalysis | None = None) -> list[dict[str, Any]]:
        """Per-IP *unicast* node summary: a stable short label, frames
        sent/received, VLAN(s) observed on, and behavioral role hints
        (DoIP server/tester, SOME/IP participant, gPTP port) derived from
        the rest of the analysis. Multicast destination addresses are
        never included here -- a multicast address is a group, not a
        node; see :meth:`multicast_group_summary`.
        """
        result = result or self._analysis
        if result is None:
            raise RuntimeError("Call analyze() first")

        labels = self._build_node_labels(result)
        servers = set(self.doip_servers(result))
        clients = self.doip_clients(result)

        someip_ips: set[str] = set()
        for flow in result.udp_flows.values():
            if flow.is_someip_like or flow.is_someip_sd:
                someip_ips.add(flow.src_ip)
                someip_ips.add(flow.dst_ip)

        # Deliberately NOT cross-referencing gPTP ports to IPs here (see
        # gptp_summary()'s docstring on why mac_to_ips can't reliably do
        # that in a routed network -- a router legitimately sources IP
        # traffic for many different addresses through its own MAC, and
        # the reverse is just as unreliable: a MAC seen with exactly one
        # associated IP can still be a mid-path routing artifact, not
        # that device's own address). gPTP identity is reported by MAC
        # only, in gptp_summary(), rather than risk a wrong node
        # attribution here.

        nodes = []
        for ip, label in labels.items():
            role_hints = []
            if ip in servers:
                role_hints.append("DoIP Server")
            if ip in clients:
                role_hints.append(f"Likely Diagnostic Tester ({clients[ip]} DoIP servers contacted)")
            if ip in someip_ips:
                role_hints.append("SOME/IP participant")
            nodes.append({
                "ip": ip,
                "label": label,
                "frames_sent": result.ip_frames_sent.get(ip, 0),
                "frames_received": result.ip_frames_received.get(ip, 0),
                "vlan_ids": sorted(result.ip_vlan_seen.get(ip, set())),
                "role_hints": role_hints,
            })
        nodes.sort(key=lambda n: -(n["frames_sent"] + n["frames_received"]))
        return nodes

    def multicast_group_summary(self, result: EthTraceAnalysis | None = None) -> list[dict[str, Any]]:
        """Per-(address, port) multicast channel summary. A channel's
        *senders* are directly observed (every packet to it names its own
        source address); its *members* generally are not -- a passive
        capture only learns who's listening if a listener happens to
        (re-)send an MLD Report while the capture is running. MLD
        membership is address-scoped (not port-scoped), so a confirmed
        member is attached to *every* channel sharing that address, not
        just one. Node identities in both lists use the same short labels
        as :meth:`node_inventory`.
        """
        result = result or self._analysis
        if result is None:
            raise RuntimeError("Call analyze() first")
        labels = self._build_node_labels(result)

        groups = []
        for (addr, port), g in result.multicast_groups.items():
            members = result.mcast_confirmed_members.get(addr, set())
            groups.append({
                "address": addr,
                "port": port,
                "vlan_ids": sorted(g.vlan_ids),
                "frame_count": g.frame_count,
                "byte_count": g.byte_count,
                "sender_labels": sorted(labels.get(ip, ip) for ip in g.sender_ips),
                "confirmed_member_labels": sorted(labels.get(ip, ip) for ip in members),
            })
        groups.sort(key=lambda g: -g["frame_count"])
        return groups

    def gptp_summary(self, result: EthTraceAnalysis | None = None) -> dict[str, Any]:
        """gPTP (IEEE 802.1AS) analysis:

        - Per-port message-type counts, Sync send-interval mean/jitter (a
          "clock health" proxy from *this capture's own* perspective --
          NOT a measurement of how accurately synchronized any receiver's
          clock actually is, which needs the receiver's own internal
          state and can't be observed passively), mean correctionField
          (accumulated residence/propagation time, a path-quality
          indicator), and sequenceId gap count (a possible-loss signal --
          could equally be a frame this capture itself missed, not
          necessarily a real network problem).
        - Resolved Pdelay_Req/Resp/Follow_Up exchanges as topology edges:
          gPTP's peer-delay mechanism only ever runs between two directly
          connected ports, so a completed exchange is real evidence of a
          physical (or point-to-point logical) link -- with two delay
          metrics each: the exact responder turnaround time (t3-t2, from
          the messages' own embedded timestamps) and an *approximate*
          round-trip time from this capture's own frame arrival times
          (explicitly not how gPTP itself computes path delay, which
          needs a fourth timestamp -- the requester's local receipt time
          -- that's never transmitted on the wire at all).

        Ports/links are identified by MAC address (recovered from the
        clockIdentity where it follows the EUI-64-from-MAC pattern), never
        by borrowing one of :meth:`node_inventory`'s IP-based "N#" labels:
        verified against a real trace that this would have been actively
        wrong on a router/gateway MAC, which legitimately re-sources IP
        traffic for many different addresses through its own MAC address
        (and, symmetrically, a MAC seen with only one associated IP can
        still just be a mid-path routing artifact, not that device's own
        address) -- there's no way to reliably tell "this device's own
        address" apart from "an address merely routed through it" using
        L2+L3 headers alone. `associated_ips` is included per port purely
        as raw, unfiltered supporting context for the reader's own
        judgement, not a claimed identity.
        """
        result = result or self._analysis
        if result is None:
            raise RuntimeError("Call analyze() first")

        def _port_label(clock_identity: str) -> str:
            mac = _mac_from_clock_identity(clock_identity)
            return mac if mac is not None else clock_identity

        ports = []
        for (clock_identity, port_number), p in result.gptp_ports.items():
            sync_interval_ms = sync_jitter_ms = 0.0
            if len(p.sync_timestamps) >= 3:
                gaps = [(p.sync_timestamps[i] - p.sync_timestamps[i - 1]) * 1000 for i in range(1, len(p.sync_timestamps))]
                sync_interval_ms = statistics.mean(gaps)
                sync_jitter_ms = statistics.stdev(gaps) if len(gaps) > 1 else 0.0
            mac = _mac_from_clock_identity(clock_identity)
            associated_ips = sorted(result.mac_to_ips.get(mac, ())) if mac else []
            ports.append({
                "clock_identity": clock_identity,
                "port_number": port_number,
                "label": _port_label(clock_identity),
                "associated_ips": associated_ips[:5],
                "associated_ip_count": len(associated_ips),
                "message_counts": {
                    _GPTP_MSGTYPE_NAMES.get(t, f"unknown_0x{t:X}"): c for t, c in p.msg_type_counts.items()
                },
                "sync_interval_ms": round(sync_interval_ms, 3),
                "sync_jitter_ms": round(sync_jitter_ms, 3),
                "mean_correction_ns": round(statistics.mean(p.correction_ns_samples), 1) if p.correction_ns_samples else 0.0,
                "sequence_gap_count": p.seq_gap_count,
                "sequence_total_count": p.seq_total_count,
            })
        ports.sort(key=lambda p: -sum(p["message_counts"].values()))

        links = []
        for (a, b), link in result.gptp_links.items():
            ta = link.turnaround_ns_samples
            rtt = link.capture_rtt_ms_samples
            links.append({
                "port_a": f"{_port_label(a[0])} (port {a[1]})",
                "port_b": f"{_port_label(b[0])} (port {b[1]})",
                "exchange_count": link.exchange_count,
                "turnaround_ns_mean": round(statistics.mean(ta), 1) if ta else 0.0,
                "turnaround_ns_stdev": round(statistics.stdev(ta), 1) if len(ta) > 1 else 0.0,
                "capture_rtt_ms_mean": round(statistics.mean(rtt), 4) if rtt else 0.0,
                "capture_rtt_ms_stdev": round(statistics.stdev(rtt), 4) if len(rtt) > 1 else 0.0,
            })
        links.sort(key=lambda l: -l["exchange_count"])

        return {"ports": ports, "links": links}

    # ── Stage 2: AUTOSAR PDU-multiplex deep dive ─────────────────────────
    #
    # Deliberately a *second* pass over the capture, not folded into
    # analyze(): Stage 1 only samples up to _PDU_MUX_CHECK_LIMIT frames
    # per UDP flow to *classify* it as PDU-multiplex-shaped, so it never
    # builds a full per-PDU timeline. Mirrors the CAN side's staged
    # design (find_counters()/find_application_signals() as separate,
    # independently-timed passes rather than one long blocking call).

    def find_autosar_pdus(self, result: EthTraceAnalysis | None = None) -> dict[int, PduStats]:
        """Stage 2 for AUTOSAR PDU-multiplex traffic identified in Stage 1
        (FlowStats.is_pdu_multiplex / EthTraceAnalysis.autosar_pdu_catalog):
        re-reads the capture, restricted to already-known PDU-multiplex
        flows, to collect each Header-ID's full history, then:

          1. Eliminates routed/relayed duplicates -- the same Header-ID
             often appears on more than one UDP flow (a gateway
             forwarding the same PDU onward to a different multicast
             group/VLAN, the same relay pattern already handled on the
             CAN side) -- kept only the flow :func:`_select_original_pdu_
             flow` picks as the original source.
          2. Reports Header-ID, observed length(s), and raw payload
             samples -- no signal-level decoding of the payload.
          3. Classifies sending behavior (Cyclic/Bursty/Spontaneous) per
             PDU, not per flow -- a multiplexed datagram's own set of
             bundled PDUs isn't necessarily identical frame to frame, so
             a given PDU's own cadence can differ from its flow's.
        """
        result = result or self._analysis
        if result is None:
            raise RuntimeError("Call analyze() first")

        pdu_flow_keys = {key for key, f in result.udp_flows.items() if f.is_pdu_multiplex}
        if not pdu_flow_keys:
            return {}

        per_channel: dict[int, dict[tuple, PduChannelStats]] = defaultdict(dict)

        with self._open_eth_reader() as reader:
            for frame in reader:
                payload, ethertype = frame.payload, frame.ethertype
                for _ in range(2):
                    if ethertype == 0x8100 and len(payload) >= 4:
                        ethertype = (payload[2] << 8) | payload[3]
                        payload = payload[4:]
                    else:
                        break
                if ethertype != 0x86DD or len(payload) < 40 or payload[6] != 17:
                    continue
                src_ip = socket.inet_ntop(socket.AF_INET6, payload[8:24])
                dst_ip = socket.inet_ntop(socket.AF_INET6, payload[24:40])
                l4 = payload[40:]
                if len(l4) < 8:
                    continue
                sport = (l4[0] << 8) | l4[1]
                dport = (l4[2] << 8) | l4[3]
                flow_key = (src_ip, dst_ip, sport, dport)
                if flow_key not in pdu_flow_keys:
                    continue
                records = _parse_pdu_multiplex(l4[8:])
                if records is None:
                    continue
                for header_id, pdu_payload in records:
                    stats = per_channel[header_id].get(flow_key)
                    if stats is None:
                        stats = PduChannelStats(header_id=header_id, flow_key=flow_key)
                        per_channel[header_id][flow_key] = stats
                    stats.count += 1
                    stats.length_values.append(len(pdu_payload))
                    stats.timestamps.append(frame.timestamp)
                    stats.payload_samples.append(pdu_payload)

        pdus: dict[int, PduStats] = {}
        for header_id, candidates in per_channel.items():
            winner_key, winner_stats = _select_original_pdu_flow(candidates)
            duplicate_flows = {k: v.count for k, v in candidates.items() if k != winner_key}
            pdus[header_id] = PduStats(
                header_id=header_id,
                flow_key=winner_key,
                length_values=winner_stats.length_values,
                payload_samples=winner_stats.payload_samples,
                timestamps=winner_stats.timestamps,
                duplicate_flows=duplicate_flows,
            )
        return pdus

    def pdu_summary(self, pdus: dict[int, PduStats], result: EthTraceAnalysis | None = None) -> list[dict[str, Any]]:
        """JSON-serializable view of :meth:`find_autosar_pdus`'s result."""
        result = result or self._analysis
        if result is None:
            raise RuntimeError("Call analyze() first")
        labels = self._build_node_labels(result)

        def _flow_label(flow_key: tuple) -> str:
            src_ip, dst_ip, sport, dport = flow_key
            return f"{labels.get(src_ip, src_ip)}:{sport} -> {dst_ip}:{dport}"

        rows = []
        for header_id, p in pdus.items():
            rows.append({
                "header_id": f"0x{header_id:X}",
                "flow": _flow_label(p.flow_key),
                "count": p.count,
                "length_values": sorted(set(p.length_values)),
                "length_is_stable": p.length_is_stable,
                "duration_s": round(p.duration_s, 3),
                "cycle_time_ms": round(p.cycle_time_ms, 3),
                "send_type": p.send_type,
                "sample_payloads": [b.hex() for b in p.payload_samples[:5]],
                "duplicate_flows": [
                    {"flow": _flow_label(fk), "count": c} for fk, c in p.duplicate_flows.items()
                ],
            })
        rows.sort(key=lambda r: -r["count"])
        return rows

    def to_summary(self, result: EthTraceAnalysis | None = None) -> dict[str, Any]:
        """JSON-serializable summary for the web UI / export."""
        result = result or self._analysis
        if result is None:
            raise RuntimeError("Call analyze() first")

        udp_flows = []
        for flow in result.udp_flows.values():
            udp_flows.append({
                "proto": "UDP",
                "src_ip": flow.src_ip,
                "dst_ip": flow.dst_ip,
                "src_port": flow.src_port,
                "dst_port": flow.dst_port,
                "vlan_ids": sorted(flow.vlan_ids),
                "frame_count": flow.frame_count,
                "byte_count": flow.byte_count,
                "duration_s": round(flow.duration_s, 3),
                "cycle_time_ms": round(flow.cycle_time_ms, 3),
                "send_type": flow.send_type,
                "is_multicast_dst": flow.is_multicast_dst,
                "is_doip_port": flow.is_doip_port,
                "is_someip_sd": flow.is_someip_sd,
                "is_someip_like": flow.is_someip_like,
                "is_pdu_multiplex": flow.is_pdu_multiplex,
                "pdu_ids": sorted(f"0x{pid:X}" for pid in flow.pdu_ids_seen) if flow.is_pdu_multiplex else [],
            })
        udp_flows.sort(key=lambda f: -f["frame_count"])

        tcp_sessions = []
        for sess in result.tcp_sessions.values():
            tcp_sessions.append({
                "proto": "TCP",
                "endpoint_a": f"{sess.endpoint_a[0]}:{sess.endpoint_a[1]}",
                "endpoint_b": f"{sess.endpoint_b[0]}:{sess.endpoint_b[1]}",
                "client": f"{sess.client_endpoint[0]}:{sess.client_endpoint[1]}" if sess.client_endpoint else None,
                "server": f"{sess.server_endpoint[0]}:{sess.server_endpoint[1]}" if sess.server_endpoint else None,
                "role_confidence": sess.role_confidence,
                "vlan_ids": sorted(sess.vlan_ids),
                "total_frames": sess.total_frames,
                "bytes_a_to_b": sess.bytes_a_to_b,
                "bytes_b_to_a": sess.bytes_b_to_a,
                "duration_s": round(max(0.0, sess.last_ts - sess.first_ts), 3),
                "is_doip": sess.is_doip,
                "saw_fin_or_rst": sess.saw_fin_or_rst,
            })
        tcp_sessions.sort(key=lambda s: -s["total_frames"])

        someip_catalog = [
            {"service_id": f"0x{sid:04X}", "method_id": f"0x{mid:04X}", "count": count}
            for (sid, mid), count in result.someip_catalog.most_common()
        ]
        autosar_pdu_catalog = [
            {"header_id": f"0x{hid:X}", "count": count}
            for hid, count in result.autosar_pdu_catalog.most_common()
        ]

        mld_observed = bool(result.mcast_confirmed_members)

        return {
            "schema_version": "1.0",
            "path": result.path,
            "total_frames": result.total_frames,
            "duration_s": round(result.duration_s, 3),
            "ethertypes": [
                {"ethertype": f"0x{et:04X}", "name": ETHERTYPE_NAMES.get(et, "unknown"), "count": c}
                for et, c in result.ethertype_counts.most_common()
            ],
            "vlans": [{"vlan_id": v, "count": c} for v, c in result.vlan_counts.most_common()],
            "ip_protocols": [
                {"proto": IP_PROTO_NAMES.get(p, f"proto_{p}"), "count": c}
                for p, c in result.ip_proto_counts.most_common()
            ],
            "nodes": self.node_inventory(result),
            "multicast_groups": self.multicast_group_summary(result),
            "mld_observed": mld_observed,
            "doip_servers": self.doip_servers(result),
            "udp_flows": udp_flows,
            "tcp_sessions": tcp_sessions,
            "someip_catalog": someip_catalog,
            "autosar_pdu_catalog": autosar_pdu_catalog,
            "gptp": self.gptp_summary(result),
            "warnings": result.errors,
        }
