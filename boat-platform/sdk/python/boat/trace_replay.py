"""Python SDK for replaying CAN trace files through the BoAt gateway.

``TraceReplayer.replay()`` supports CAN only (.asc, .blf via python-can) and
sends each frame one-by-one via gRPC CanService, paced in real time by this
process. There is no server-side mode here.

For Ethernet (.pcap) replay, use ``convert_to_binary()`` to produce the
gateway's internal binary trace format, upload it via
ReplayService.ImportTraceData, then play it back with
ReplayService.StartReplay + StreamReplay (this is what the ``boat replay
import`` / ``boat replay start`` / ``boat replay stream`` CLI commands do).

Quick example::

    from boat.trace_replay import TraceReplayer

    # CAN replay
    replayer = TraceReplayer(
        gateway="localhost:50051",
        buses=["vcan0", "vcan1"],
        speed=1.0,
    )
    replayer.replay("recording.asc")

    # Ethernet pcap replay (server-side only, via ReplayService)
    replayer = TraceReplayer(
        replay_src_ip="192.168.1.1",
        replay_dst_ip="192.168.1.100",
    )
    binary_data = replayer.convert_to_binary("capture.pcap")
    # ... upload via ReplayService.ImportTraceData, then StartReplay/StreamReplay
"""
from __future__ import annotations

from collections import namedtuple
import ipaddress
import struct
import time
from pathlib import Path
from typing import Callable, List, Optional

# CAN FD flags (matches gateway constants)
_CANFD_BRS = 0x01   # bit-rate switch
_CANFD_FDF = 0x04   # FD frame

# IPv6 extension header numbers that should be walked to find the actual L4 protocol.
_IPv6_EXTENSION_HEADERS = {0, 43, 44, 50, 51, 60, 135}


EthernetPcapFrame = namedtuple("EthernetPcapFrame", [
    "timestamp", "dst_mac", "src_mac", "ethertype", "payload",
])


class EthernetPcapReader:
    """Iterate Ethernet frames from a standard pcap file (DLT_EN10MB).

    Yields ``EthernetPcapFrame`` per packet record.
    Context-manager compatible.
    """

    def __init__(self, path: str) -> None:
        self._f = open(path, "rb")
        try:
            hdr = self._f.read(24)
            if len(hdr) < 24:
                raise TraceReplayError("Truncated pcap global header")
            _, _, _, _, _, _, dlt = struct.unpack("<IHHiIII", hdr)
            if dlt != 1:
                raise TraceReplayError(
                    f"Unsupported pcap DLT {dlt}, expected DLT_EN10MB (1)"
                )
        except TraceReplayError:
            self._f.close()
            raise
        except Exception as e:
            self._f.close()
            raise TraceReplayError(f"Invalid pcap file: {e}") from e

    def __enter__(self) -> "EthernetPcapReader":
        return self

    def __exit__(self, *args) -> None:
        self._f.close()

    def __iter__(self) -> "EthernetPcapReader":
        return self

    def __next__(self) -> EthernetPcapFrame:
        hdr = self._f.read(16)
        if len(hdr) < 16:
            self._f.close()
            raise StopIteration
        ts_sec, ts_usec, incl_len, _ = struct.unpack("<IIII", hdr)
        frame = self._f.read(incl_len)
        if len(frame) < 14:
            self._f.close()
            raise StopIteration
        ts = ts_sec + ts_usec / 1_000_000
        return EthernetPcapFrame(
            timestamp=ts,
            dst_mac=frame[0:6],
            src_mac=frame[6:12],
            ethertype=(frame[12] << 8) | frame[13],
            payload=frame[14:incl_len],
        )


class TraceReplayError(RuntimeError):
    pass


class TraceReplayer:
    """Replay a CAN/Ethernet trace file through the BoAt gateway via gRPC.

    Args:
        gateway:     gRPC address of the BoAt gateway (host:port).
        buses:       Ordered list of interface names.  For CAN: channel *N*
                     maps to ``buses[N-1]`` (1-based).  For Ethernet: the
                     first bus is the target interface for reconstructed frames.
        speed:       Playback speed multiplier.  ``1.0`` = real-time,
                     ``2.0`` = twice as fast, ``0.5`` = half speed.
                     ``0`` means send as fast as possible (no delay).
        simulation_id: Simulation ID forwarded to the gateway (usually ``""``).
        on_frame:    Optional callback ``(index, msg) -> None`` called for
                     every frame just before it is sent.
        channel_filter: If set, only replay CAN frames from this channel.
        id_filter:   If set, only replay CAN frames with these arbitration IDs.
        eth_iface:   Target Ethernet interface for pcap replay (overrides ``buses[0]``).
        replay_src_ip: Source IP address for reconstructed IP header (Ethernet replay).
        replay_dst_ip: Destination IP address for reconstructed IP header.
        replay_src_mac: Override source MAC (auto-detected from interface if not set).
        replay_dst_mac: Override destination MAC (default: broadcast for IPv4/IPv6 UDP/ICMP).
        ip_filter:      Set of IP addresses to filter by (applied post-rewrite).
                        Only packets whose rewritten src or dst is in this set
                        are replayed. Empty set = no filtering.
        ip_map:         Mapping of original IP → rewritten IP (e.g.
                        ``{"10.10.10.10": "192.168.0.100"}``).  IPs not in the
                        map keep their original value (or ``replay_src_ip`` /
                        ``replay_dst_ip`` fallback).
        ethertype_filter: Set of EtherType values to filter by (pre-rewrite).
                          Only packets whose EtherType is in this set are
                          replayed.  Empty set = no filtering.
        protocol_filter:  Set of IP protocol / IPv6 next-header numbers to
                          filter by (pre-rewrite).  Only packets whose L4
                          protocol is in this set are replayed.  Empty set = no
                          filtering.
        src_ip_filter:  Set of IP addresses to filter the rewritten source by
                        (applied post-rewrite).  Only packets whose rewritten
                        src is in this set are replayed.  Empty set = no
                        filtering.
        dst_ip_filter:  Set of IP addresses to filter the rewritten destination
                        by (applied post-rewrite).  Only packets whose
                        rewritten dst is in this set are replayed.  Empty set
                        = no filtering.
        src_port_filter: Set of UDP/TCP source port numbers to filter by
                         (pre-rewrite).  Only packets whose source port is in
                         this set are replayed.  Only applies to UDP/TCP
                         and only to unfragmented or first-fragment packets.
                         Empty set = no filtering.
        dst_port_filter: Set of UDP/TCP destination port numbers to filter by
                         (pre-rewrite).  Only packets whose destination port
                         is in this set are replayed.  Only applies to UDP/TCP
                         and only to unfragmented or first-fragment packets.
                         Empty set = no filtering.
        mac_map:        Mapping of rewritten IP → MAC address (e.g.
                        ``{"192.168.0.100": "02:de:ad:be:ef:01"}``).  Applied
                        in the C++ forwarder after IP rewriting.  IPs not in
                        the map fall back to the default behavior
                        (source = auto-detected from interface, destination =
                        broadcast).  Empty dict = default behavior.
        tcp_plugin_path: Path to the shared library for TCP replay
                         (e.g. ``"build/debug/src/plugins/tcp/tcp.so"``).
                         When set, TCP packets from the pcap are replayed
                         statefully through the plugin rather than being
                         sent as raw packets.  Protocol filter is applied
                         against next-header 6 (TCP).
    """

    def __init__(
        self,
        gateway: str = "localhost:50051",
        buses: Optional[List[str]] = None,
        speed: float = 1.0,
        simulation_id: str = "",
        on_frame: Optional[Callable] = None,
        channel_filter: Optional[int] = None,
        id_filter: Optional[set[int]] = None,
        eth_iface: Optional[str] = None,
        replay_src_ip: Optional[str] = None,
        replay_dst_ip: Optional[str] = None,
        replay_src_mac: Optional[str] = None,
        replay_dst_mac: Optional[str] = None,
        ip_filter: Optional[set[str]] = None,
        ip_map: Optional[dict[str, str]] = None,
        ethertype_filter: Optional[set[int]] = None,
        protocol_filter: Optional[set[int]] = None,
        src_ip_filter: Optional[set[str]] = None,
        dst_ip_filter: Optional[set[str]] = None,
        src_port_filter: Optional[set[int]] = None,
        dst_port_filter: Optional[set[int]] = None,
        mac_map: Optional[dict[str, str]] = None,
        tcp_plugin_path: Optional[str] = None,
    ) -> None:
        self.gateway          = gateway
        self.buses            = buses or []
        self.speed            = speed
        self.simulation_id    = simulation_id
        self.on_frame         = on_frame
        self.channel_filter   = channel_filter
        self.id_filter        = id_filter or set()
        self.eth_iface        = eth_iface
        self.replay_src_ip    = replay_src_ip
        self.replay_dst_ip    = replay_dst_ip
        self.replay_src_mac   = replay_src_mac
        self.replay_dst_mac   = replay_dst_mac
        self.ip_filter        = ip_filter or set()
        self.ip_map           = ip_map or {}
        self.ethertype_filter = ethertype_filter or set()
        self.protocol_filter  = protocol_filter or set()
        self.src_ip_filter    = src_ip_filter or set()
        self.dst_ip_filter    = dst_ip_filter or set()
        self.src_port_filter  = src_port_filter or set()
        self.dst_port_filter  = dst_port_filter or set()
        self.mac_map          = mac_map or {}
        self.tcp_plugin_path  = tcp_plugin_path
        self._tcp_plugin      = None  # lazy-loaded TcpHandle
        self._tcp_streams     = {}    # key -> list of (payload, is_fin)
        self._stub            = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def replay(self, path: str | Path, loop: int | None = None) -> int:
        """Replay a CAN trace file, sending each frame individually via gRPC.

        Args:
            path: Path to a ``.asc`` or ``.blf`` file.
            loop: If set, replay the file in a loop with N ms gap between the
                  last message of one run and the first message of the next run.
                  ``None`` or omitted means play once.

        Returns:
            Total number of frames sent (across all loop iterations).

        Raises:
            TraceReplayError: If the file cannot be opened, is a ``.pcap``
            (Ethernet replay is server-side only — use ``convert_to_binary()``
            + ReplayService, i.e. ``boat replay import`` + ``boat replay
            start``/``stream``), or a gRPC error occurs.
        """
        path = Path(path)
        if path.suffix.lower() == ".pcap":
            raise TraceReplayError(
                "Ethernet/.pcap replay is not supported by TraceReplayer.replay() "
                "(direct per-frame CAN injection only). Use convert_to_binary() + "
                "ReplayService.ImportTraceData/StartReplay/StreamReplay instead "
                "(the `boat replay import` + `boat replay start`/`stream` CLI "
                "commands do this)."
            )

        stub = self._get_stub()
        total = 0
        last_msg_wall: float | None = None
        while True:
            offset = 0.0
            if last_msg_wall is not None and loop is not None:
                target = last_msg_wall + loop / 1000.0
                now = time.monotonic()
                offset = max(0.0, target - now)
            run_sent, last_msg_wall = self._replay_once(
                stub, path, initial_offset_sec=offset
            )
            total += run_sent
            if loop is None:
                break
        return total

    # ── Internals ──────────────────────────────────────────────────────────────

    def convert_to_binary(self, path: Path) -> bytes:
        """Convert a trace file to the gateway's internal binary trace format.

        Each event is serialized as a length-delimited ``boat.v1.Frame``
        protobuf message (``uint32 length`` + ``bytes``).  The Frame carries
        the full bus-agnostic metadata (timestamps, payload, CAN/Ethernet/PDU
        metadata) so the replay engine can dispatch directly through
        ``PluginManager::DispatchFrame``.

        Import is hardware-independent: CAN records store the original
        trace ``channel`` (not a resolved interface), and Ethernet records
        leave ``iface`` unset unless ``eth_iface``/``buses`` were passed to
        this ``TraceReplayer``. Target interfaces (and MAC addresses, via
        ``mac_map``) are resolved at replay time from ``--buses``/
        ``--eth-iface``/``--mac-map`` on ``boat replay start``/``stream``,
        so the same import can be replayed on different hardware without
        re-importing.

        Handles both CAN (ASC/BLF) and Ethernet (pcap) sources.
        """
        from boat.v1 import frame_pb2

        reader = self._open_reader(path)
        result = bytearray()
        is_eth = isinstance(reader, EthernetPcapReader)

        with reader:
            for msg in reader:
                if is_eth:
                    if self.ethertype_filter and msg.ethertype not in self.ethertype_filter:
                        continue
                    payload_bytes = msg.payload
                    is_tcp = False
                    if self.tcp_plugin_path:
                        if msg.ethertype == 0x0800 and len(payload_bytes) >= 20:
                            is_tcp = (payload_bytes[9] == 6)
                        elif msg.ethertype == 0x86DD and len(payload_bytes) >= 40:
                            is_tcp = (payload_bytes[6] == 6)
                    if is_tcp:
                        self._buffer_tcp_frame(msg)
                        continue
                    raw = self._reconstruct_ip_packet(msg)
                    if not raw:
                        continue

                    # Extract rewritten IPs from the reconstructed header.
                    if msg.ethertype == 0x0800 and len(raw) >= 20:
                        src_ip = raw[12:16]
                        dst_ip = raw[16:20]
                        ip_ver = 4
                    elif msg.ethertype == 0x86DD and len(raw) >= 40:
                        src_ip = raw[8:24]
                        dst_ip = raw[24:40]
                        ip_ver = 6
                    else:
                        src_ip = b""
                        dst_ip = b""
                        ip_ver = 0

                    eth_iface = self.eth_iface or (self.buses[0] if self.buses else "")

                    proto = frame_pb2.Frame(
                        bus_type=frame_pb2.Frame.ETHERNET,
                        iface=eth_iface,
                        timestamp_ns=int(msg.timestamp * 1_000_000_000),
                        payload=raw,
                        eth=frame_pb2.EthMetadata(
                            dst_mac=bytes(msg.dst_mac),
                            src_mac=bytes(msg.src_mac),
                            ethertype=msg.ethertype,
                            vlan_id=0,
                            src_ip=src_ip,
                            dst_ip=dst_ip,
                            ip_version=ip_ver,
                        ),
                    )
                else:
                    ch = getattr(msg, "channel", None)
                    if self.channel_filter is not None and ch != self.channel_filter:
                        continue
                    if self.id_filter and msg.arbitration_id not in self.id_filter:
                        continue
                    raw = bytes(msg.data)

                    flags = 0
                    if getattr(msg, "is_fd", False):
                        flags |= _CANFD_FDF
                    if getattr(msg, "bitrate_switch", False):
                        flags |= _CANFD_BRS

                    # Store the original channel, not a resolved interface --
                    # target interface is a replay-time decision (--buses on
                    # `boat replay start`/`stream`), so the same import can be
                    # replayed on different hardware without re-importing.
                    channel = getattr(msg, "channel", 1) or 1

                    proto = frame_pb2.Frame(
                        bus_type=frame_pb2.Frame.CANFD if flags else frame_pb2.Frame.CAN,
                        timestamp_ns=int(msg.timestamp * 1_000_000_000),
                        payload=raw,
                        can=frame_pb2.CanMetadata(
                            can_id=msg.arbitration_id,
                            dlc=len(msg.data),
                            flags=flags,
                            channel=channel,
                        ),
                    )

                result.extend(self._pack_frame_record(proto))

        if self._tcp_streams:
            self._replay_tcp_streams()
        return bytes(result)

    @staticmethod
    def _pack_frame_record(frame) -> bytes:
        """Pack one ``boat.v1.Frame`` as a length-delimited binary trace record."""
        data = frame.SerializeToString()
        return struct.pack("<I", len(data)) + data

    @classmethod
    def frames_to_binary(cls, frames: List) -> bytes:
        """Serialize ``boat.v1.Frame`` messages to the gateway's binary trace format.

        Inverse of :meth:`parse_binary`. Shares the per-record encoding used
        by :meth:`convert_to_binary`, so a trace parsed with ``parse_binary``
        and re-encoded here round-trips byte-for-byte.
        """
        return b"".join(cls._pack_frame_record(frame) for frame in frames)

    @staticmethod
    def parse_binary(data: bytes) -> List:
        """Parse the gateway's length-delimited ``boat.v1.Frame`` binary trace format.

        Inverse of :meth:`frames_to_binary`. Each record is ``uint32 length``
        (little-endian) followed by that many bytes of a serialized
        ``boat.v1.Frame`` protobuf, matching the format the replay engine
        reads (``src/replay/replay_engine/replay_engine.cpp``).
        """
        from boat.v1 import frame_pb2

        frames = []
        offset = 0
        n = len(data)
        while offset < n:
            if offset + 4 > n:
                raise TraceReplayError(f"Truncated length prefix at offset {offset}")
            (length,) = struct.unpack_from("<I", data, offset)
            offset += 4
            if offset + length > n:
                raise TraceReplayError(f"Truncated frame record at offset {offset}")
            frame = frame_pb2.Frame()
            frame.ParseFromString(data[offset:offset + length])
            frames.append(frame)
            offset += length
        return frames

    def _buffer_tcp_frame(self, frame: EthernetPcapFrame) -> None:
        """Buffer a TCP frame by stream for later stateful replay."""
        payload = frame.payload
        if frame.ethertype == 0x0800:
            src_ip = payload[12:16]
            dst_ip = payload[16:20]
            protocol = payload[9]
            ihl = (payload[0] & 0x0F) * 4
            tcp_start = ihl
        else:
            src_ip = payload[8:24]
            dst_ip = payload[24:40]
            protocol = payload[6]
            tcp_start = 40

        if protocol != 6 or len(payload) < tcp_start + 20:
            return

        tcp = payload[tcp_start:]
        src_port = (tcp[0] << 8) | tcp[1]
        dst_port = (tcp[2] << 8) | tcp[3]
        data_off = ((tcp[12] >> 4) & 0x0F) * 4
        tcp_data = tcp[data_off:]
        flags = tcp[13]
        is_fin = bool(flags & 0x01)
        is_syn = bool(flags & 0x02)

        # Apply IP map to get the rewritten IPs
        orig_src_str = str(ipaddress.ip_address(src_ip))
        orig_dst_str = str(ipaddress.ip_address(dst_ip))
        mapped_src = self.ip_map.get(orig_src_str, self.replay_src_ip or orig_src_str)
        mapped_dst = self.ip_map.get(orig_dst_str, self.replay_dst_ip or orig_dst_str)

        # IP + port based stream key (after rewrite)
        key = (mapped_src, mapped_dst, src_port, dst_port)
        if key not in self._tcp_streams:
            self._tcp_streams[key] = {
                "src_ip": mapped_src,
                "dst_ip": mapped_dst,
                "src_port": src_port,
                "dst_port": dst_port,
                "payloads": [],
                "syn": is_syn,
                "fin": is_fin,
            }
        if tcp_data:
            self._tcp_streams[key]["payloads"].append(tcp_data)

    def _replay_tcp_streams(self) -> None:
        """Replay buffered TCP streams through the TCP plugin."""
        if not self._tcp_streams:
            return
        from boat.tcp import TcpHandle as _TcpHandle
        self._tcp_plugin = _TcpHandle(self.tcp_plugin_path)

        for key, stream in self._tcp_streams.items():
            conn_id = self._tcp_plugin.connect(
                stream["src_ip"], stream["src_port"],
                stream["dst_ip"], stream["dst_port"],
            )
            if conn_id < 0:
                continue

            for data in stream["payloads"]:
                self._tcp_plugin.send(conn_id, data)

            if stream["fin"]:
                self._tcp_plugin.close(conn_id)

    def _reconstruct_ip_packet(self, frame: EthernetPcapFrame) -> bytes:
        """Reconstruct an IP packet with user-specified addresses.

        Walks IPv6 extension headers to find the actual L4 protocol,
        then dispatches to the version-specific handler.
        """
        payload = frame.payload
        if frame.ethertype == 0x86DD:
            if len(payload) < 40:
                return b""
            protocol, _, _, _ = self._walk_ipv6_extensions(payload)
        else:
            if len(payload) < 20:
                return b""
            protocol = payload[9]

        if self.protocol_filter and protocol not in self.protocol_filter:
            return b""

        if frame.ethertype == 0x86DD:
            return self._reconstruct_ip6_packet(frame)
        return self._reconstruct_ip4_packet(frame)

    def _reconstruct_ip4_packet(self, frame: EthernetPcapFrame) -> bytes:
        """Reconstruct an IPv4 packet with user-specified addresses.

        Handles port filter (UDP/TCP), IP fragmentation, and full L4 rebuild
        for non-fragmented packets.
        """
        payload = frame.payload
        if len(payload) < 20:
            return b""

        # Parse IPv4 header
        version_ihl = payload[0]
        ihl = (version_ihl & 0x0F) * 4
        if ihl < 20 or ihl > len(payload):
            return b""
        total_len = (payload[2] << 8) | payload[3]
        identification = (payload[4] << 8) | payload[5]
        flags_frag = (payload[6] << 8) | payload[7]
        ttl = payload[8]
        protocol = payload[9]
        orig_src = payload[12:16]
        orig_dst = payload[16:20]

        frag_offset = (flags_frag & 0x1FFF) * 8
        more_frags  = bool(flags_frag & 0x2000)
        is_fragmented = frag_offset > 0 or more_frags

        header_end = ihl
        transport_payload = payload[header_end:total_len]

        # Port filter (only UDP/TCP, and only when L4 header is present)
        if protocol in (6, 17) and len(transport_payload) >= 4 and not is_fragmented:
            pkt_src_port = struct.unpack("!H", transport_payload[0:2])[0]
            pkt_dst_port = struct.unpack("!H", transport_payload[2:4])[0]
            if self.src_port_filter and pkt_src_port not in self.src_port_filter:
                return b""
            if self.dst_port_filter and pkt_dst_port not in self.dst_port_filter:
                return b""

        # First fragment (offset 0) still has the L4 header for port parsing
        if is_fragmented and frag_offset == 0 and protocol in (6, 17) and len(transport_payload) >= 4:
            pkt_src_port = struct.unpack("!H", transport_payload[0:2])[0]
            pkt_dst_port = struct.unpack("!H", transport_payload[2:4])[0]
            if self.src_port_filter and pkt_src_port not in self.src_port_filter:
                return b""
            if self.dst_port_filter and pkt_dst_port not in self.dst_port_filter:
                return b""

        orig_src_str = str(ipaddress.IPv4Address(orig_src))
        orig_dst_str = str(ipaddress.IPv4Address(orig_dst))
        mapped_src = self.ip_map.get(orig_src_str, self.replay_src_ip or orig_src_str)
        mapped_dst = self.ip_map.get(orig_dst_str, self.replay_dst_ip or orig_dst_str)
        if self.ip_filter and mapped_src not in self.ip_filter and mapped_dst not in self.ip_filter:
            return b""
        if self.src_ip_filter and mapped_src not in self.src_ip_filter:
            return b""
        if self.dst_ip_filter and mapped_dst not in self.dst_ip_filter:
            return b""
        src_ip_bytes = self._parse_ip(mapped_src)
        dst_ip_bytes = self._parse_ip(mapped_dst)

        # For fragmented packets: rebuild IP header only, keep payload as-is
        if is_fragmented:
            ip_header = struct.pack("!BBHHHBBH4s4s",
                0x45, 0, total_len, identification, flags_frag,
                ttl, protocol, 0, src_ip_bytes, dst_ip_bytes)
            ip_csum = self._checksum(ip_header)
            ip_header = ip_header[:10] + struct.pack("!H", ip_csum) + ip_header[12:]
            return ip_header + transport_payload

        # Full L4 rebuild for non-fragmented packets
        if protocol == 17 and len(transport_payload) >= 8:
            src_port = struct.unpack("!H", transport_payload[0:2])[0]
            dst_port = struct.unpack("!H", transport_payload[2:4])[0]
            udp_len = len(transport_payload[8:]) + 8
            udp_data = transport_payload[8:]
            new_udp = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
            new_udp += udp_data
            transport = new_udp
            total_len = 20 + len(new_udp)
        elif protocol == 1 and len(transport_payload) >= 4:
            icmp_type = transport_payload[0:1]
            icmp_code = transport_payload[1:2]
            icmp_rest = transport_payload[4:]
            new_icmp = icmp_type + icmp_code + b"\x00\x00"
            new_icmp += icmp_rest
            transport = new_icmp
            total_len = 20 + len(new_icmp)
        else:
            transport = transport_payload
            total_len = 20 + len(transport)

        ip_header = struct.pack("!BBHHHBBH4s4s",
            0x45, 0, total_len, identification, flags_frag,
            ttl, protocol, 0, src_ip_bytes, dst_ip_bytes)
        ip_csum = self._checksum(ip_header)
        ip_header = ip_header[:10] + struct.pack("!H", ip_csum) + ip_header[12:]

        result = ip_header + transport

        if protocol == 17:
            pseudo = src_ip_bytes + dst_ip_bytes + b"\x00" + struct.pack("!BH", 17, len(transport))
            udp_offset = 20
            udp_hdr = result[udp_offset:udp_offset + 8]
            udp_csum = self._checksum(pseudo + udp_hdr + transport[8:])
            if udp_csum == 0:
                udp_csum = 0xFFFF
            result = result[:udp_offset + 6] + struct.pack("!H", udp_csum) + result[udp_offset + 8:]

        if protocol == 1:
            icmp_offset = 20
            icmp_csum = self._checksum(result[icmp_offset:])
            result = result[:icmp_offset + 2] + struct.pack("!H", icmp_csum) + result[icmp_offset + 4:]

        return result

    def _walk_ipv6_extensions(self, payload: bytes) -> tuple[int, int, bytes, bool]:
        """Walk IPv6 extension headers to find the actual L4 protocol.

        Returns ``(actual_protocol, l4_offset, ext_bytes, is_fragmented)``.
        """
        offset = 40
        next_header = payload[6]
        ext_bytes = bytearray()
        is_fragmented = False

        while next_header in _IPv6_EXTENSION_HEADERS:
            if offset + 2 > len(payload):
                return (next_header, offset, bytes(ext_bytes), is_fragmented)
            nh = payload[offset]
            if next_header == 44:  # Fragment
                hdr_len = 8
                is_fragmented = True
            elif next_header == 51:  # Authentication Header
                hdr_len = (payload[offset + 1] + 1) * 4
            else:  # HBH (0), Routing (43), Destination (60), Mobility (135)
                hdr_len = (payload[offset + 1] + 1) * 8
            if offset + hdr_len > len(payload):
                return (next_header, offset, bytes(ext_bytes), is_fragmented)
            ext_bytes.extend(payload[offset:offset + hdr_len])
            offset += hdr_len
            next_header = nh

        return (next_header, offset, bytes(ext_bytes), is_fragmented)

    def _reconstruct_ip6_packet(self, frame: EthernetPcapFrame) -> bytes:
        """Reconstruct an IPv6 packet with user-specified addresses.

        Walks IPv6 extension headers, applies port filter, handles
        fragmentation, and rebuilds L4 with mandatory checksums.
        """
        payload = frame.payload
        if len(payload) < 40:
            return b""

        actual_protocol, l4_offset, ext_bytes, is_fragmented = self._walk_ipv6_extensions(payload)

        orig_src = payload[8:24]
        orig_dst = payload[24:40]
        hop_limit = payload[7]

        transport_payload = payload[l4_offset:]

        # Port filter (only UDP/TCP, and only when L4 header is present)
        if actual_protocol in (6, 17) and len(transport_payload) >= 4 and not is_fragmented:
            pkt_src_port = struct.unpack("!H", transport_payload[0:2])[0]
            pkt_dst_port = struct.unpack("!H", transport_payload[2:4])[0]
            if self.src_port_filter and pkt_src_port not in self.src_port_filter:
                return b""
            if self.dst_port_filter and pkt_dst_port not in self.dst_port_filter:
                return b""

        # First fragment (offset 0) still has L4 header for port parsing
        frag_offset = 0
        more_frags = False
        if is_fragmented:
            # Parse Fragment header (44) for offset and M flag
            # Find Fragment header in ext_bytes
            fh_offset = 0
            nh = payload[6]
            while nh != 44 and fh_offset < len(ext_bytes):
                fh_offset += 8 if nh == 51 else (payload[fh_offset + 1] + 1) * 8 if nh != 44 else 8
            if fh_offset < len(ext_bytes):
                fh_data = ext_bytes[fh_offset:fh_offset + 8]
                fh_info = struct.unpack("!H", fh_data[2:4])[0]
                frag_offset = (fh_info & 0xFFF8) >> 3  # in 8-byte units
                more_frags = bool(fh_info & 0x0001)
            if is_fragmented and frag_offset == 0 and actual_protocol in (6, 17) and len(transport_payload) >= 4:
                pkt_src_port = struct.unpack("!H", transport_payload[0:2])[0]
                pkt_dst_port = struct.unpack("!H", transport_payload[2:4])[0]
                if self.src_port_filter and pkt_src_port not in self.src_port_filter:
                    return b""
                if self.dst_port_filter and pkt_dst_port not in self.dst_port_filter:
                    return b""

        orig_src_str = str(ipaddress.IPv6Address(orig_src))
        orig_dst_str = str(ipaddress.IPv6Address(orig_dst))
        mapped_src = self.ip_map.get(orig_src_str, self.replay_src_ip or orig_src_str)
        mapped_dst = self.ip_map.get(orig_dst_str, self.replay_dst_ip or orig_dst_str)
        if self.ip_filter and mapped_src not in self.ip_filter and mapped_dst not in self.ip_filter:
            return b""
        if self.src_ip_filter and mapped_src not in self.src_ip_filter:
            return b""
        if self.dst_ip_filter and mapped_dst not in self.dst_ip_filter:
            return b""
        src_ip_bytes = self._parse_ip(mapped_src)
        dst_ip_bytes = self._parse_ip(mapped_dst)

        # For fragmented packets: rebuild IPv6 header + extension bytes + payload as-is
        if is_fragmented:
            payload_len = len(ext_bytes) + len(transport_payload)
            v_tc_flow = payload[0:4]
            ip6_header = v_tc_flow + struct.pack("!HBB",
                payload_len, payload[6], hop_limit) + src_ip_bytes + dst_ip_bytes
            return ip6_header + ext_bytes + transport_payload

        # Full L4 rebuild for non-fragmented packets
        if actual_protocol == 17 and len(transport_payload) >= 8:
            src_port = struct.unpack("!H", transport_payload[0:2])[0]
            dst_port = struct.unpack("!H", transport_payload[2:4])[0]
            udp_data = transport_payload[8:]
            udp_len = len(udp_data) + 8
            new_udp = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
            new_udp += udp_data
            transport = new_udp
        elif actual_protocol == 58 and len(transport_payload) >= 4:
            icmp6_type = transport_payload[0:1]
            icmp6_code = transport_payload[1:2]
            icmp6_rest = transport_payload[4:]
            new_icmp6 = icmp6_type + icmp6_code + b"\x00\x00"
            new_icmp6 += icmp6_rest
            transport = new_icmp6
        else:
            transport = transport_payload

        # The first next header type in the chain (or actual protocol if no extensions)
        first_nh = payload[6] if ext_bytes else actual_protocol

        v_tc_flow = payload[0:4]
        ip6_header = v_tc_flow + struct.pack("!HBB",
            len(ext_bytes) + len(transport),
            first_nh,
            hop_limit,
        ) + src_ip_bytes + dst_ip_bytes

        result = ip6_header + ext_bytes + transport
        l4_offset_in_result = 40 + len(ext_bytes)

        if actual_protocol == 17:
            pseudo = src_ip_bytes + dst_ip_bytes + struct.pack("!I", len(transport))
            pseudo += b"\x00\x00\x00" + struct.pack("!B", 17)
            udp_hdr = result[l4_offset_in_result:l4_offset_in_result + 8]
            udp_csum = self._checksum(pseudo + udp_hdr + transport[8:])
            if udp_csum == 0:
                udp_csum = 0xFFFF
            result = result[:l4_offset_in_result + 6] + struct.pack("!H", udp_csum) + result[l4_offset_in_result + 8:]

        if actual_protocol == 58:
            pseudo = src_ip_bytes + dst_ip_bytes + struct.pack("!I", len(transport))
            pseudo += b"\x00\x00\x00" + struct.pack("!B", 58)
            icmp6_csum = self._checksum(pseudo + result[l4_offset_in_result:])
            result = result[:l4_offset_in_result + 2] + struct.pack("!H", icmp6_csum) + result[l4_offset_in_result + 4:]

        return result

    @staticmethod
    def _parse_ip(ip_str: str) -> bytes:
        return ipaddress.ip_address(ip_str).packed

    @staticmethod
    def _checksum(data: bytes) -> int:
        s = 0
        for i in range(0, len(data), 2):
            word = (data[i] << 8) | (data[i + 1] if i + 1 < len(data) else 0)
            s += word
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        return (~s) & 0xFFFF

    def _get_stub(self):
        if self._stub is not None:
            return self._stub
        try:
            import grpc
            from boat.v1 import can_pb2_grpc
        except ImportError as e:
            raise TraceReplayError(f"Cannot import boat gRPC stubs: {e}") from e
        channel     = grpc.insecure_channel(self.gateway)
        self._stub  = can_pb2_grpc.CanServiceStub(channel)
        return self._stub

    def _iface_for_channel(self, channel: int) -> str:
        """Map a 1-based trace channel number to a bus interface name."""
        if not self.buses:
            return "vcan0"
        idx = max(0, channel - 1)
        return self.buses[min(idx, len(self.buses) - 1)]

    def _open_reader(self, path: Path):
        """Return a reader appropriate for *path*.

        Supports:
          - ``.pcap`` (DLT_EN10MB) → ``EthernetPcapReader``
          - ``.asc`` / ``.blf`` → python-can reader
        """
        suffix = path.suffix.lower()
        if suffix == ".pcap":
            return EthernetPcapReader(str(path))

        try:
            import can
        except ImportError as e:
            raise TraceReplayError(
                "python-can is required for CAN trace replay: "
                "pip install python-can"
            ) from e
        if suffix == ".asc":
            return can.ASCReader(str(path))
        if suffix == ".blf":
            return can.BLFReader(str(path))
        raise TraceReplayError(
            f"Unsupported trace format '{suffix}'. "
            "Supported: .pcap, .asc, .blf"
        )

    def _replay_once(self, stub, path: Path, initial_offset_sec: float = 0.0) -> tuple[int, float | None]:
        """Stream one pass through *path*.

        Args:
            initial_offset_sec: Seconds to wait before the first message so that
                it fires at the correct offset from a previous run's last message.

        Returns:
            (frame_count, last_msg_wall_time) where *last_msg_wall_time* is
            ``time.monotonic()`` when the last message was sent, or *None* if
            no messages were sent.
        """
        from boat.v1 import can_pb2

        sent            = 0
        prev_trace_ts: Optional[float] = None
        prev_wall_ts:  Optional[float] = None

        try:
            reader = self._open_reader(path)
        except Exception as e:
            raise TraceReplayError(f"Cannot open trace file '{path}': {e}") from e

        with reader:
            for msg in reader:
                # ── filters ───────────────────────────────────────────────────
                ch = getattr(msg, "channel", None)
                if self.channel_filter is not None and ch != self.channel_filter:
                    continue
                if self.id_filter and msg.arbitration_id not in self.id_filter:
                    continue

                # ── timing ────────────────────────────────────────────────────
                if self.speed > 0 and prev_trace_ts is not None:
                    delta_trace = msg.timestamp - prev_trace_ts
                    delta_wall  = time.monotonic() - prev_wall_ts  # type: ignore[operator]
                    wait        = delta_trace / self.speed - delta_wall
                    if wait > 0:
                        time.sleep(wait)
                elif self.speed > 0 and initial_offset_sec > 0:
                    time.sleep(initial_offset_sec)

                prev_trace_ts = msg.timestamp
                prev_wall_ts  = time.monotonic()

                # ── build CAN frame ───────────────────────────────────────────
                iface  = self._iface_for_channel(getattr(msg, "channel", 1) or 1)
                flags  = 0
                if getattr(msg, "is_fd", False):
                    flags |= _CANFD_FDF
                if getattr(msg, "bitrate_switch", False):
                    flags |= _CANFD_BRS

                frame = can_pb2.CanFrame(
                    can_id       = msg.arbitration_id,
                    dlc          = len(msg.data),
                    data         = bytes(msg.data),
                    timestamp_ns = int(msg.timestamp * 1_000_000_000),
                    iface        = iface,
                    flags        = flags,
                )

                if self.on_frame is not None:
                    self.on_frame(sent, msg)

                # ── send ──────────────────────────────────────────────────────
                try:
                    stub.SendCanFrame(
                        can_pb2.SendCanFrameRequest(
                            simulation_id = self.simulation_id,
                            frame         = frame,
                        )
                    )
                except Exception as e:
                    raise TraceReplayError(
                        f"gRPC error sending frame {sent}: {e}"
                    ) from e

                sent += 1

        return sent, prev_wall_ts
