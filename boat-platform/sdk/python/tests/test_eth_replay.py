"""Tests for Ethernet pcap replay conversion and IP packet reconstruction."""
from __future__ import annotations

import struct
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))

from boat.v1 import frame_pb2


def _make_pcap(frames: list[bytes]) -> bytes:
    """Build a valid pcap file (DLT_EN10MB) with the given Ethernet frames."""
    buf = io.BytesIO()
    # Global header
    buf.write(struct.pack("<IHHiIII",
        0xa1b2c3d4,  # magic
        2, 3,         # version
        0,            # thiszone
        0,            # sigfigs
        65535,        # snaplen
        1,            # DLT_EN10MB
    ))
    ts = 0.0
    for frame in frames:
        ts_sec = int(ts)
        ts_usec = int((ts - ts_sec) * 1_000_000)
        buf.write(struct.pack("<IIII", ts_sec, ts_usec, len(frame), len(frame)))
        buf.write(frame)
        ts += 0.1
    return buf.getvalue()


def _udp_packet(src_ip: bytes, dst_ip: bytes, src_port: int, dst_port: int,
                payload: bytes, ttl: int = 64) -> bytes:
    """Build a raw Ethernet frame containing an IPv4+UDP packet."""
    # UDP header
    udp_len = 8 + len(payload)
    udp_hdr = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
    # IP header — total_len must include IP header + UDP header + payload
    total_len = 20 + udp_len
    ip_hdr = struct.pack("!BBHHHBBH4s4s",
        0x45, 0, total_len, 0x1234, 0, ttl, 17, 0, src_ip, dst_ip)
    # IP checksum
    s = 0
    for i in range(0, len(ip_hdr), 2):
        word = struct.unpack("!H", ip_hdr[i:i+2])[0]
        s += word
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    ip_csum = (~s) & 0xFFFF
    ip_hdr = ip_hdr[:10] + struct.pack("!H", ip_csum) + ip_hdr[12:]
    # Ethernet frame
    eth = (
        b"\x00\x01\x02\x03\x04\x05"  # dst_mac
        b"\x06\x07\x08\x09\x0a\x0b"  # src_mac
        b"\x08\x00"                    # ethertype IPv4
        + ip_hdr + udp_hdr + payload
    )
    return eth


def _icmp_packet(src_ip: bytes, dst_ip: bytes, payload: bytes, ttl: int = 64) -> bytes:
    """Build a raw Ethernet frame containing an IPv4+ICMP echo packet."""
    icmp_type = 8  # echo request
    icmp_code = 0
    icmp_ident = 0x1234
    icmp_seq = 1
    icmp_hdr = struct.pack("!BBHHH", icmp_type, icmp_code, 0, icmp_ident, icmp_seq)
    icmp_payload = payload
    icmp_data = icmp_hdr + icmp_payload
    # ICMP checksum
    s = 0
    for i in range(0, len(icmp_data), 2):
        word = struct.unpack("!H", icmp_data[i:i+2])[0]
        s += word
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    icmp_csum = (~s) & 0xFFFF
    icmp_data = icmp_hdr[:2] + struct.pack("!H", icmp_csum) + icmp_data[4:]
    # IP header
    total_len = 20 + len(icmp_data)
    ip_hdr = struct.pack("!BBHHHBBH4s4s",
        0x45, 0, total_len, 0x5678, 0, ttl, 1, 0, src_ip, dst_ip)
    s = 0
    for i in range(0, len(ip_hdr), 2):
        word = struct.unpack("!H", ip_hdr[i:i+2])[0]
        s += word
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    ip_csum = (~s) & 0xFFFF
    ip_hdr = ip_hdr[:10] + struct.pack("!H", ip_csum) + ip_hdr[12:]
    # Ethernet frame
    eth = (
        b"\x00\x01\x02\x03\x04\x05"
        b"\x06\x07\x08\x09\x0a\x0b"
        b"\x08\x00"
        + ip_hdr + icmp_data
    )
    return eth


def _ipv4_frag(src_ip: bytes, dst_ip: bytes, payload: bytes,
               identification: int, frag_offset: int, more_frags: bool,
               protocol: int = 17, ttl: int = 64) -> bytes:
    """Build a raw Ethernet frame containing a fragmented IPv4+UDP datagram."""
    total_len = 20 + len(payload)
    flags = (1 << 13) if more_frags else 0  # bit 13 = MF
    frag_field = flags | frag_offset
    ip_hdr = struct.pack("!BBHHHBBH4s4s",
        0x45, 0, total_len, identification, frag_field, ttl, protocol, 0,
        src_ip, dst_ip)
    ip_csum = _checksum(ip_hdr)
    ip_hdr = ip_hdr[:10] + struct.pack("!H", ip_csum) + ip_hdr[12:]
    return b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x02\x08\x00" + ip_hdr + payload


def _ipv6_with_hbh(src_ip: bytes, dst_ip: bytes, src_port: int, dst_port: int,
                    payload: bytes, hop_limit: int = 64) -> bytes:
    """Build a raw Ethernet frame containing an IPv6+HBH+UDP packet."""
    udp_len = 8 + len(payload)
    udp_hdr = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
    udp_data = udp_hdr + payload
    # UDP checksum with IPv6 pseudo-header
    pseudo = src_ip + dst_ip + struct.pack("!I", udp_len) + b"\x00\x00\x00\x17"
    udp_csum = _checksum(pseudo + udp_data)
    if udp_csum == 0:
        udp_csum = 0xFFFF
    udp_hdr = struct.pack("!HHHH", src_port, dst_port, udp_len, udp_csum)
    udp_data = udp_hdr + payload
    # Hop-by-Hop extension header (next header = 17=UDP, hdr_ext_len = 0 = 8 bytes)
    hbh = struct.pack("!BB", 17, 0) + b"\x00" * 6  # 8 bytes total
    # IPv6 header (next header = 0 = Hop-by-Hop)
    v_tc_flow = 0x60000000
    ip6_len = len(hbh) + len(udp_data)
    ip6_hdr = struct.pack("!IHBB", v_tc_flow, ip6_len, 0, hop_limit)
    ip6_hdr += src_ip + dst_ip
    return b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x86\xdd" + ip6_hdr + hbh + udp_data


def _checksum(data: bytes) -> int:
    """Compute the Internet checksum over *data*."""
    s = 0
    for i in range(0, len(data), 2):
        word = (data[i] << 8) | (data[i + 1] if i + 1 < len(data) else 0)
        s += word
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def _udp6_packet(src_ip: bytes, dst_ip: bytes, src_port: int, dst_port: int,
                 payload: bytes, hop_limit: int = 64) -> bytes:
    """Build a raw Ethernet frame containing an IPv6+UDP packet."""
    udp_len = 8 + len(payload)
    udp_hdr = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
    udp_data = udp_hdr + payload
    # UDP checksum with IPv6 pseudo-header (mandatory)
    pseudo = src_ip + dst_ip + struct.pack("!I", udp_len)
    pseudo += b"\x00\x00\x00" + struct.pack("!B", 17)
    udp_csum = _checksum(pseudo + udp_data)
    if udp_csum == 0:
        udp_csum = 0xFFFF
    udp_hdr = struct.pack("!HHHH", src_port, dst_port, udp_len, udp_csum)
    udp_data = udp_hdr + payload
    # IPv6 header
    v_tc_flow = 0x60000000  # version=6, tc=0, flow=0
    ip6_hdr = struct.pack("!IHBB", v_tc_flow, len(udp_data), 17, hop_limit)
    ip6_hdr += src_ip + dst_ip
    # Ethernet frame
    eth = (
        b"\x00\x01\x02\x03\x04\x05"
        b"\x06\x07\x08\x09\x0a\x0b"
        b"\x86\xdd"
        + ip6_hdr + udp_data
    )
    return eth


def _icmp6_packet(src_ip: bytes, dst_ip: bytes, payload: bytes, hop_limit: int = 64) -> bytes:
    """Build a raw Ethernet frame containing an IPv6+ICMPv6 echo packet."""
    icmp6_type = 128  # echo request
    icmp6_code = 0
    icmp6_ident = 0x1234
    icmp6_seq = 1
    icmp6_hdr = struct.pack("!BBHHH", icmp6_type, icmp6_code, 0, icmp6_ident, icmp6_seq)
    icmp6_data = icmp6_hdr + payload
    # ICMPv6 checksum with IPv6 pseudo-header (mandatory — unlike ICMPv4)
    pseudo = src_ip + dst_ip + struct.pack("!I", len(icmp6_data))
    pseudo += b"\x00\x00\x00" + struct.pack("!B", 58)
    icmp6_csum = _checksum(pseudo + icmp6_data)
    icmp6_data = icmp6_hdr[:2] + struct.pack("!H", icmp6_csum) + icmp6_data[4:]
    # IPv6 header
    v_tc_flow = 0x60000000
    ip6_hdr = struct.pack("!IHBB", v_tc_flow, len(icmp6_data), 58, hop_limit)
    ip6_hdr += src_ip + dst_ip
    # Ethernet frame
    eth = (
        b"\x00\x01\x02\x03\x04\x05"
        b"\x06\x07\x08\x09\x0a\x0b"
        b"\x86\xdd"
        + ip6_hdr + icmp6_data
    )
    return eth


class TestEthernetPcapReader:
    def test_reads_pcap_global_header(self, tmp_path):
        from boat.trace_replay import EthernetPcapReader

        data = _make_pcap([])
        p = tmp_path / "empty.pcap"
        p.write_bytes(data)
        frames = list(EthernetPcapReader(str(p)))
        assert frames == []

    def test_reads_single_udp_frame(self, tmp_path):
        from boat.trace_replay import EthernetPcapReader

        payload = b"HELLO"
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
            12345, 30490, payload,
        )
        data = _make_pcap([eth])
        p = tmp_path / "single.pcap"
        p.write_bytes(data)
        frames = list(EthernetPcapReader(str(p)))

        assert len(frames) == 1
        assert frames[0].ethertype == 0x0800
        assert frames[0].timestamp == 0.0
        # Ethernet payload = IP+UDP+payload without L2 header
        assert len(frames[0].payload) == 20 + 8 + len(payload)

    def test_reads_multiple_frames(self, tmp_path):
        from boat.trace_replay import EthernetPcapReader

        frames_data = []
        for i in range(3):
            eth = _udp_packet(
                b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
                12345, 30490, b"data" + bytes([i]),
            )
            frames_data.append(eth)
        data = _make_pcap(frames_data)
        p = tmp_path / "multi.pcap"
        p.write_bytes(data)
        frames = list(EthernetPcapReader(str(p)))

        assert len(frames) == 3
        for i in range(3):
            assert frames[i].ethertype == 0x0800
            assert abs(frames[i].timestamp - 0.1 * i) < 0.001

    def test_rejects_non_en10mb(self, tmp_path):
        from boat.trace_replay import EthernetPcapReader, TraceReplayError

        p = tmp_path / "bad.pcap"
        hdr = struct.pack("<IHHiIII", 0xa1b2c3d4, 2, 3, 0, 0, 65535, 0)  # DLT=0
        p.write_bytes(hdr)
        try:
            list(EthernetPcapReader(str(p)))
        except TraceReplayError as e:
            assert "DLT" in str(e)


class TestReconstructIpPacket:
    def _make_replayer(self, replay_src_ip="192.168.1.1",
                       replay_dst_ip="192.168.1.100"):
        from boat.trace_replay import TraceReplayer
        return TraceReplayer(
            buses=["eth0"],
            speed=1.0,
            replay_src_ip=replay_src_ip,
            replay_dst_ip=replay_dst_ip,
        )

    def test_udp_packet_reconstructed_with_new_ips(self):
        replayer = self._make_replayer("10.0.0.1", "10.0.0.2")

        app_data = b"Hello from UDP!"
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
            12345, 30490, app_data,
        )

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x0800,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)

        # Verify IP header
        assert result[0] == 0x45  # version=4, ihl=5
        assert result[9] == 17    # protocol = UDP
        # Source IP should be rewritten
        assert result[12:16] == b"\x0a\x00\x00\x01"  # 10.0.0.1
        # Dest IP should be rewritten
        assert result[16:20] == b"\x0a\x00\x00\x02"  # 10.0.0.2

        # Verify IP checksum is valid
        s = 0
        ip_hdr = result[:20]
        for i in range(0, 20, 2):
            word = struct.unpack("!H", ip_hdr[i:i+2])[0]
            s += word
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        assert s == 0xFFFF, f"IP checksum invalid: {s:#x}"

        # Verify UDP header
        assert result[20:22] == struct.pack("!H", 12345)  # src_port preserved
        assert result[22:24] == struct.pack("!H", 30490)  # dst_port preserved
        udp_len = struct.unpack("!H", result[24:26])[0]
        assert udp_len == 8 + len(app_data)

        # Verify UDP checksum is valid (non-zero since we calculate it)
        udp_csum = struct.unpack("!H", result[26:28])[0]
        assert udp_csum != 0

        # Verify payload
        assert result[28:] == app_data

    def test_icmp_packet_reconstructed_with_new_ips(self):
        replayer = self._make_replayer("10.0.0.1", "10.0.0.2")

        app_data = b"Ping payload"
        eth = _icmp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
            app_data,
        )

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x0800,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)

        # Verify IP header
        assert result[0] == 0x45
        assert result[9] == 1  # protocol = ICMP
        assert result[12:16] == b"\x0a\x00\x00\x01"
        assert result[16:20] == b"\x0a\x00\x00\x02"

        # Verify ICMP type/code preserved (echo request)
        assert result[20] == 8   # type
        assert result[21] == 0   # code

        # Verify ICMP checksum is valid
        s = 0
        icmp_data = result[20:]
        for i in range(0, len(icmp_data), 2):
            if i + 1 < len(icmp_data):
                word = struct.unpack("!H", icmp_data[i:i+2])[0]
            else:
                word = icmp_data[i] << 8
            s += word
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        assert s == 0xFFFF, f"ICMP checksum invalid: {s:#x}"

    def test_ttl_preserved(self):
        replayer = self._make_replayer("10.0.0.1", "10.0.0.2")

        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
            12345, 30490, b"data", ttl=128,
        )

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x0800,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)
        assert result[8] == 128  # TTL preserved

    def test_unknown_protocol_passthrough(self):
        replayer = self._make_replayer("10.0.0.1", "10.0.0.2")

        # Build a frame with protocol 0xFD (experimental)
        payload_bytes = b"\x01\x02\x03\x04"
        total_len = 20 + len(payload_bytes)
        ip_hdr = struct.pack("!BBHHHBBH4s4s",
            0x45, 0, total_len, 0xAAAA, 0, 64, 0xFD, 0,
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02")
        s = 0
        for i in range(0, 20, 2):
            word = struct.unpack("!H", ip_hdr[i:i+2])[0]
            s += word
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        ip_hdr = ip_hdr[:10] + struct.pack("!H", (~s) & 0xFFFF) + ip_hdr[12:]

        eth = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x08\x00" + ip_hdr + payload_bytes

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x0800,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)
        assert result[9] == 0xFD
        assert result[20:] == payload_bytes


class TestConvertToBinary:
    def _make_replayer(self, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {
            "buses": ["eth0"],
            "speed": 1.0,
            "replay_src_ip": "192.168.1.1",
            "replay_dst_ip": "192.168.1.100",
        }
        params.update(kwargs)
        return TraceReplayer(**params)

    def _pcap_bytes(self, eth_frames: list[bytes]) -> Path:
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".pcap", delete=False)
        f.write(_make_pcap(eth_frames))
        f.close()
        return Path(f.name)

    def test_converts_pcap_to_binary_format(self, tmp_path):
        replayer = self._make_replayer()
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
            12345, 30490, b"SOMEIP_DATA",
        )
        p = self._pcap_bytes([eth])
        binary = replayer.convert_to_binary(p)
        p.unlink()

        offset = 0
        record_len = struct.unpack_from("<I", binary, offset)[0]
        offset += 4
        frame = frame_pb2.Frame()
        frame.ParseFromString(binary[offset:offset + record_len])
        offset += record_len

        assert frame.bus_type == frame_pb2.Frame.ETHERNET
        assert frame.eth.ethertype == 0x0800
        assert frame.timestamp_ns // 1_000_000 == 0
        assert len(frame.payload) > 0

        payload = frame.payload
        # Verify it's a valid IP packet with rewritten IPs
        assert payload[12:16] == b"\xc0\xa8\x01\x01"  # 192.168.1.1
        assert payload[16:20] == b"\xc0\xa8\x01\x64"  # 192.168.1.100

    def test_converts_multiple_pcap_frames(self, tmp_path):
        replayer = self._make_replayer()
        frames = [
            _udp_packet(b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"a"),
            _udp_packet(b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12346, 30491, b"bb"),
        ]
        p = self._pcap_bytes(frames)
        binary = replayer.convert_to_binary(p)
        p.unlink()

        # Should have 2 trace records
        offset = 0
        records = 0
        while offset < len(binary):
            record_len = struct.unpack_from("<I", binary, offset)[0]
            offset += 4
            frame = frame_pb2.Frame()
            frame.ParseFromString(binary[offset:offset + record_len])
            offset += record_len
            records += 1

        assert records == 2


class TestReconstructIp6Packet:
    def _make_replayer(self, replay_src_ip="2001:db8::1",
                       replay_dst_ip="2001:db8::100"):
        from boat.trace_replay import TraceReplayer
        return TraceReplayer(
            buses=["eth0"],
            speed=1.0,
            replay_src_ip=replay_src_ip,
            replay_dst_ip=replay_dst_ip,
        )

    def test_udp6_packet_reconstructed_with_new_ips(self):
        replayer = self._make_replayer("2001:db8::ff00:42:8329",
                                       "2001:db8::ff00:42:9300")

        app_data = b"Hello from IPv6 UDP!"
        eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, app_data,
        )

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x86DD,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)

        # Verify IPv6 header
        assert result[0] >> 4 == 6  # version
        assert result[6] == 17     # next header = UDP
        # Source IP should be rewritten
        assert result[8:24] == b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\xff\x00\x00\x42\x83\x29"
        # Dest IP should be rewritten
        assert result[24:40] == b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\xff\x00\x00\x42\x93\x00"

        # Verify UDP header
        assert result[40:42] == struct.pack("!H", 12345)  # src_port preserved
        assert result[42:44] == struct.pack("!H", 30490)  # dst_port preserved
        udp_len = struct.unpack("!H", result[44:46])[0]
        assert udp_len == 8 + len(app_data)

        # Verify UDP checksum is valid (mandatory for IPv6, non-zero)
        udp_csum = struct.unpack("!H", result[46:48])[0]
        assert udp_csum != 0
        # Verify UDP checksum correctness — one's complement sum should be 0xFFFF
        pseudo = result[8:24] + result[24:40] + struct.pack("!I", udp_len)
        pseudo += b"\x00\x00\x00" + struct.pack("!B", 17)
        s = 0
        data = pseudo + result[40:]
        for i in range(0, len(data), 2):
            word = struct.unpack("!H", data[i:i+2])[0]
            s += word
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        assert s == 0xFFFF

        # Verify payload
        assert result[48:] == app_data

    def test_icmp6_packet_reconstructed_with_new_ips(self):
        replayer = self._make_replayer("2001:db8::1", "2001:db8::100")

        app_data = b"IPv6 ping payload"
        eth = _icmp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            app_data,
        )

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x86DD,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)

        # Verify IPv6 header
        assert result[0] >> 4 == 6
        assert result[6] == 58     # next header = ICMPv6
        assert result[8:24] == b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01"
        assert result[24:40] == b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00"

        # Verify ICMPv6 type/code preserved (echo request)
        assert result[40] == 128  # type
        assert result[41] == 0    # code

        # Verify ICMPv6 checksum is valid (mandatory with pseudo-header)
        pseudo = result[8:24] + result[24:40] + struct.pack("!I", len(result[40:]))
        pseudo += b"\x00\x00\x00" + struct.pack("!B", 58)
        assert _checksum(pseudo + result[40:]) == 0

    def test_ipv6_hop_limit_preserved(self):
        replayer = self._make_replayer("2001:db8::1", "2001:db8::100")

        eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"data", hop_limit=128,
        )

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x86DD,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)
        assert result[7] == 128  # hop limit preserved

    def test_ipv6_unknown_next_header_passthrough(self):
        replayer = self._make_replayer("2001:db8::1", "2001:db8::100")

        payload_bytes = b"\x01\x02\x03\x04"
        # Build IPv6+unknown (next header 0xFD)
        v_tc_flow = 0x60000000
        ip6_hdr = struct.pack("!IHBB", v_tc_flow, len(payload_bytes), 0xFD, 64)
        ip6_hdr += (
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01" +
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02"
        )
        eth = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x86\xdd" + ip6_hdr + payload_bytes

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=eth[0:6],
            src_mac=eth[6:12],
            ethertype=0x86DD,
            payload=eth[14:],
        )

        result = replayer._reconstruct_ip_packet(frame)
        assert result[6] == 0xFD  # next header preserved
        assert result[40:] == payload_bytes

    def test_short_ipv6_payload_returns_empty(self):
        replayer = self._make_replayer()

        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(
            timestamp=1.0,
            dst_mac=b"\x00" * 6,
            src_mac=b"\x00" * 6,
            ethertype=0x86DD,
            payload=b"\x00" * 10,  # too short for IPv6 header
        )

        result = replayer._reconstruct_ip_packet(frame)
        assert result == b""


class TestConvertToBinaryIp6:
    def _make_replayer(self, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {
            "buses": ["eth0"],
            "speed": 1.0,
            "replay_src_ip": "2001:db8::1",
            "replay_dst_ip": "2001:db8::100",
        }
        params.update(kwargs)
        return TraceReplayer(**params)

    def _pcap_bytes(self, eth_frames: list[bytes]) -> Path:
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=".pcap", delete=False)
        f.write(_make_pcap(eth_frames))
        f.close()
        return Path(f.name)

    def test_converts_ipv6_pcap_to_binary_format(self, tmp_path):
        replayer = self._make_replayer()
        eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"IPV6_DATA",
        )
        p = self._pcap_bytes([eth])
        binary = replayer.convert_to_binary(p)
        p.unlink()

        offset = 0
        record_len = struct.unpack_from("<I", binary, offset)[0]
        offset += 4
        frame = frame_pb2.Frame()
        frame.ParseFromString(binary[offset:offset + record_len])
        offset += record_len

        assert frame.bus_type == frame_pb2.Frame.ETHERNET
        assert frame.eth.ethertype == 0x86DD
        assert frame.timestamp_ns // 1_000_000 == 0
        assert len(frame.payload) > 0

        payload = frame.payload
        assert len(payload) == len(frame.payload)
        assert payload[0] >> 4 == 6  # IPv6 version
        assert payload[6] == 17      # UDP

    def test_converts_mixed_ipv4_ipv6_pcap(self, tmp_path):
        replayer = self._make_replayer(
            replay_src_ip="2001:db8::1",
            replay_dst_ip="2001:db8::100",
        )
        ipv4_eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02",
            11111, 22222, b"v4data",
        )
        ipv6_eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            33333, 44444, b"v6data",
        )
        p = self._pcap_bytes([ipv4_eth, ipv6_eth])
        binary = replayer.convert_to_binary(p)
        p.unlink()

        offset = 0
        records = []
        while offset < len(binary):
            record_len = struct.unpack_from("<I", binary, offset)[0]
            offset += 4
            frame = frame_pb2.Frame()
            frame.ParseFromString(binary[offset:offset + record_len])
            offset += record_len
            records.append((frame, len(frame.payload)))

        assert len(records) == 2
        assert records[0][0].eth.ethertype == 0x0800  # IPv4
        assert records[1][0].eth.ethertype == 0x86DD  # IPv6
        payload4 = records[0][0].payload
        assert payload4[0] >> 4 == 4
        payload6 = records[1][0].payload
        assert payload6[0] >> 4 == 6
        assert payload6[6] == 17  # UDP over IPv6


class TestIpFilter:
    """Tests for the ip_filter parameter (post-rewrite filtering)."""

    def _make_replayer(self, ip_filter: set[str] | None = None, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0, "replay_src_ip": "192.168.0.100"}
        params.update(kwargs)
        params["ip_filter"] = ip_filter
        return TraceReplayer(**params)

    def test_filter_matches_src(self):
        """Packet whose rewritten src is in the filter set is replayed."""
        replayer = self._make_replayer(ip_filter={"192.168.0.100"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0
        assert result[12:16] == b"\xc0\xa8\x00\x64"  # 192.168.0.100

    def test_filter_matches_dst(self):
        """Packet whose rewritten dst is in the filter set is replayed."""
        replayer = self._make_replayer(
            replay_src_ip=None, replay_dst_ip="192.168.0.101",
            ip_filter={"192.168.0.101"},
        )
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0
        assert result[16:20] == b"\xc0\xa8\x00\x65"  # 192.168.0.101

    def test_filter_no_match_returns_empty(self):
        """Packet whose rewritten IPs do not match the filter is dropped."""
        replayer = self._make_replayer(ip_filter={"10.0.0.99"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result == b""

    def test_filter_empty_set_no_filtering(self):
        """Empty filter set = no filtering, all packets pass through."""
        replayer = self._make_replayer(ip_filter=set())
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0

    def test_filter_preserves_original_ips_when_no_rewrite(self):
        """Filter still works when no global rewrite is set — checks original IPs."""
        replayer = self._make_replayer(
            replay_src_ip=None, replay_dst_ip=None,
            ip_filter={"10.0.0.1"},
        )
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0
        assert result[12:16] == b"\x0a\x00\x00\x01"  # original IP preserved

    def test_filter_icmp_packet(self):
        """ICMP packets are also filtered by post-rewrite IP."""
        replayer = self._make_replayer(ip_filter={"192.168.0.100"})
        eth = _icmp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", b"ping",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0
        assert result[12:16] == b"\xc0\xa8\x00\x64"


class TestIpMap:
    """Tests for the ip_map parameter (per-IP rewriting)."""

    def _make_replayer(self, ip_map: dict[str, str] | None = None, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0}
        params.update(kwargs)
        params["ip_map"] = ip_map
        return TraceReplayer(**params)

    def test_map_src_ip(self):
        """Source IP from the map is rewritten; dest IP is preserved."""
        replayer = self._make_replayer(ip_map={"10.0.0.1": "192.168.0.100"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[12:16] == b"\xc0\xa8\x00\x64"  # mapped src
        assert result[16:20] == b"\x0a\x00\x00\x02"  # original dst

    def test_map_dst_ip(self):
        """Dest IP from the map is rewritten; source IP is preserved."""
        replayer = self._make_replayer(ip_map={"10.0.0.2": "192.168.0.101"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[12:16] == b"\x0a\x00\x00\x01"  # original src
        assert result[16:20] == b"\xc0\xa8\x00\x65"  # mapped dst

    def test_map_both_ips(self):
        """Both src and dst are independently rewritten via the map."""
        replayer = self._make_replayer(ip_map={
            "10.0.0.1": "192.168.0.100",
            "10.0.0.2": "192.168.0.101",
        })
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[12:16] == b"\xc0\xa8\x00\x64"
        assert result[16:20] == b"\xc0\xa8\x00\x65"

    def test_map_unknown_ip_preserved(self):
        """IPs not in the map keep their original value."""
        replayer = self._make_replayer(ip_map={"99.99.99.99": "1.2.3.4"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[12:16] == b"\x0a\x00\x00\x01"  # original preserved
        assert result[16:20] == b"\x0a\x00\x00\x02"

    def test_map_unknown_ip_falls_back_to_global(self):
        """IPs not in the map fall back to replay_src_ip / replay_dst_ip if set."""
        replayer = self._make_replayer(
            replay_src_ip="10.10.10.10", replay_dst_ip="10.10.10.11",
            ip_map={"99.99.99.99": "1.2.3.4"},  # doesn't match src or dst
        )
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[12:16] == b"\x0a\x0a\x0a\x0a"  # global fallback
        assert result[16:20] == b"\x0a\x0a\x0a\x0b"

    def test_map_with_icmp(self):
        """ICMP packets are properly rewritten via the map."""
        replayer = self._make_replayer(ip_map={"10.0.0.1": "192.168.0.100"})
        eth = _icmp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", b"ping",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[12:16] == b"\xc0\xa8\x00\x64"
        assert result[9] == 1  # protocol ICMP
        # ICMP checksum valid
        assert _checksum(result[20:]) == 0

    def test_map_ipv6(self):
        """IPv6 addresses are properly rewritten via the map."""
        replayer = self._make_replayer(ip_map={
            "2001:db8::1": "2001:db8::ff00:42:8329",
        })
        eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x86DD, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result[8:24] == b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\xff\x00\x00\x42\x83\x29"
        assert result[24:40] == b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02"


class TestIpFilterAndMap:
    """Combined ip_map + ip_filter: map first, then filter on the result."""

    def _make_replayer(self, ip_map=None, ip_filter=None, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0}
        params.update(kwargs)
        params["ip_map"] = ip_map
        params["ip_filter"] = ip_filter
        return TraceReplayer(**params)

    def test_map_then_filter_match(self):
        """Packet mapped to an IP in the filter set is replayed."""
        replayer = self._make_replayer(
            ip_map={"10.0.0.1": "192.168.0.100"},
            ip_filter={"192.168.0.100"},
        )
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0
        assert result[12:16] == b"\xc0\xa8\x00\x64"

    def test_map_then_filter_no_match(self):
        """Packet mapped to an IP NOT in the filter set is dropped."""
        replayer = self._make_replayer(
            ip_map={"10.0.0.1": "192.168.0.100"},
            ip_filter={"10.0.0.99"},
        )
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert result == b""

    def test_map_then_filter_multi_conversation(self):
        """Multiple conversations in the same pcap: only matching ones pass."""
        replayer = self._make_replayer(
            ip_map={
                "10.0.0.1": "192.168.0.100",
                "10.0.0.2": "192.168.0.101",
            },
            ip_filter={"192.168.0.100"},
        )
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0  # src maps to 192.168.0.100 -> matches filter

        # Different conversation: src=10.0.0.3, dst=10.0.0.4 (not in map)
        eth2 = _udp_packet(
            b"\x0a\x00\x00\x03", b"\x0a\x00\x00\x04", 11111, 22222, b"other",
        )
        frame2 = EthernetPcapFrame(2.0, eth2[0:6], eth2[6:12], 0x0800, eth2[14:])
        result2 = replayer._reconstruct_ip_packet(frame2)
        assert result2 == b""  # rewritten IPs (original) don't match filter

    def test_map_then_filter_convert_to_binary(self):
        """End-to-end: filtered-out packets produce no binary trace records."""
        replayer = self._make_replayer(
            ip_map={"10.0.0.1": "192.168.0.100"},
            ip_filter={"192.168.0.100"},
        )
        # Frame 1: matches (src maps to 192.168.0.100)
        eth1 = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"keep",
        )
        # Frame 2: does not match (original IPs, no map entry -> preserved -> not in filter)
        eth2 = _udp_packet(
            b"\x0a\x00\x00\x03", b"\x0a\x00\x00\x04", 11111, 22222, b"drop",
        )
        import tempfile
        data = _make_pcap([eth1, eth2])
        p = Path(tempfile.NamedTemporaryFile(suffix=".pcap", delete=False).name)
        p.write_bytes(data)
        binary = replayer.convert_to_binary(p)
        p.unlink()

        # Should have exactly 1 record (frame 2 was filtered out)
        offset = 0
        records = 0
        while offset < len(binary):
            record_len = struct.unpack_from("<I", binary, offset)[0]
            offset += 4
            frame = frame_pb2.Frame()
            frame.ParseFromString(binary[offset:offset + record_len])
            offset += record_len
            records += 1
        assert records == 1


class TestEthertypeFilter:
    """Tests for the ethertype_filter parameter (pre-rewrite L2 filter)."""

    def _make_replayer(self, ethertype_filter=None, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0}
        params.update(kwargs)
        params["ethertype_filter"] = ethertype_filter
        return TraceReplayer(**params)

    def _pcap_bytes(self, eth_frames: list[bytes]):
        import tempfile
        p = Path(tempfile.NamedTemporaryFile(suffix=".pcap", delete=False).name)
        p.write_bytes(_make_pcap(eth_frames))
        return p

    def test_filter_ipv4_only(self):
        """Only IPv4 frames pass through when filter is {0x0800}."""
        replayer = self._make_replayer(ethertype_filter={0x0800})
        ipv4_eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"v4",
        )
        ipv6_eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"v6",
        )
        p = self._pcap_bytes([ipv4_eth, ipv6_eth])
        binary = replayer.convert_to_binary(p)
        p.unlink()

        offset = 0
        ethertypes = []
        while offset < len(binary):
            record_len = struct.unpack_from("<I", binary, offset)[0]
            offset += 4
            frame = frame_pb2.Frame()
            frame.ParseFromString(binary[offset:offset + record_len])
            offset += record_len
            ethertypes.append(frame.eth.ethertype)
        assert ethertypes == [0x0800]  # only IPv4

    def test_filter_ipv6_only(self):
        """Only IPv6 frames pass through when filter is {0x86DD}."""
        replayer = self._make_replayer(ethertype_filter={0x86DD})
        ipv4_eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"v4",
        )
        ipv6_eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"v6",
        )
        p = self._pcap_bytes([ipv4_eth, ipv6_eth])
        binary = replayer.convert_to_binary(p)
        p.unlink()

        offset = 0
        ethertypes = []
        while offset < len(binary):
            record_len = struct.unpack_from("<I", binary, offset)[0]
            offset += 4
            frame = frame_pb2.Frame()
            frame.ParseFromString(binary[offset:offset + record_len])
            offset += record_len
            ethertypes.append(frame.eth.ethertype)
        assert ethertypes == [0x86DD]  # only IPv6

    def test_filter_empty_set_no_filtering(self):
        """Empty ethertype_filter set passes all frames."""
        replayer = self._make_replayer(ethertype_filter=set())
        ipv4_eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"v4",
        )
        ipv6_eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"v6",
        )
        p = self._pcap_bytes([ipv4_eth, ipv6_eth])
        binary = replayer.convert_to_binary(p)
        p.unlink()

        offset = 0
        count = 0
        while offset < len(binary):
            record_len = struct.unpack_from("<I", binary, offset)[0]
            offset += 4
            frame = frame_pb2.Frame()
            frame.ParseFromString(binary[offset:offset + record_len])
            offset += record_len
            count += 1
        assert count == 2  # both pass


class TestProtocolFilter:
    """Tests for the protocol_filter parameter (pre-rewrite L4 filter)."""

    def _make_replayer(self, protocol_filter=None, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0}
        params.update(kwargs)
        params["protocol_filter"] = protocol_filter
        return TraceReplayer(**params)

    def test_filter_udp_only(self):
        """Only UDP packets pass through when filter is {17}."""
        replayer = self._make_replayer(protocol_filter={17})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert len(replayer._reconstruct_ip_packet(frame)) > 0

    def test_filter_udp_icmpv4(self):
        """Both UDP and ICMPv4 pass when filter is {1, 17}."""
        replayer = self._make_replayer(protocol_filter={1, 17})
        udp_eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        icmp_eth = _icmp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", b"ping",
        )
        from boat.trace_replay import EthernetPcapFrame
        udp_result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, udp_eth[0:6], udp_eth[6:12], 0x0800, udp_eth[14:])
        )
        assert len(udp_result) > 0
        icmp_result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(2.0, icmp_eth[0:6], icmp_eth[6:12], 0x0800, icmp_eth[14:])
        )
        assert len(icmp_result) > 0

    def test_filter_udp_rejects_icmp(self):
        """UDP-only filter drops ICMP packets (returns empty)."""
        replayer = self._make_replayer(protocol_filter={17})
        eth = _icmp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", b"ping",
        )
        from boat.trace_replay import EthernetPcapFrame
        result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        )
        assert result == b""

    def test_filter_udp_rejects_unknown(self):
        """UDP-only filter drops unknown protocol packets."""
        replayer = self._make_replayer(protocol_filter={17})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        # Modify the protocol byte to 0xFD (unknown)
        payload = eth[14:]
        modified = payload[:9] + bytes([0xFD]) + payload[10:]
        eth_mod = eth[:14] + modified
        result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth_mod[0:6], eth_mod[6:12], 0x0800, eth_mod[14:])
        )
        assert result == b""

    def test_filter_tcp_blocked(self):
        """TCP packets (protocol 6) are blocked when filter is {17}."""
        replayer = self._make_replayer(protocol_filter={17})
        # Build a minimal TCP-like frame (protocol=6)
        total_len = 20 + 20  # IP header + TCP header (no payload)
        ip_hdr = struct.pack("!BBHHHBBH4s4s",
            0x45, 0, total_len, 0x9ABC, 0, 64, 6, 0,
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02")
        s = 0
        for i in range(0, 20, 2):
            word = struct.unpack("!H", ip_hdr[i:i+2])[0]
            s += word
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        ip_hdr = ip_hdr[:10] + struct.pack("!H", (~s) & 0xFFFF) + ip_hdr[12:]
        tcp_hdr = b"\x00\x50\x00\x50" + b"\x00" * 16  # minimal TCP header
        eth = b"\x00" * 12 + b"\x08\x00" + ip_hdr + tcp_hdr
        from boat.trace_replay import EthernetPcapFrame
        result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        )
        assert result == b""

    def test_filter_udp_over_ipv6(self):
        """UDP filter works for IPv6 (protocol 17) by number, not IP version."""
        replayer = self._make_replayer(protocol_filter={17})
        eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"v6data",
        )
        from boat.trace_replay import EthernetPcapFrame
        result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x86DD, eth[14:])
        )
        assert len(result) > 0
        assert result[6] == 17  # UDP over IPv6

    def test_filter_icmpv6_over_ipv6(self):
        """ICMPv6 (protocol 58) is allowed when in the filter set."""
        replayer = self._make_replayer(protocol_filter={58})
        eth = _icmp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            b"ping6",
        )
        from boat.trace_replay import EthernetPcapFrame
        result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x86DD, eth[14:])
        )
        assert len(result) > 0
        assert result[6] == 58  # ICMPv6

    def test_filter_empty_set_passes_all(self):
        """Empty protocol_filter set passes all protocols."""
        replayer = self._make_replayer(protocol_filter=set())
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        result = replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        )
        assert len(result) > 0


class TestEthertypeAndProtocolFilter:
    """Combined ethertype + protocol filter (both pre-rewrite)."""

    def test_ipv4_udp_only(self):
        """Only IPv4+UDP packets pass through (end-to-end via _convert_to_binary)."""
        from boat.trace_replay import TraceReplayer
        import tempfile

        replayer = TraceReplayer(
            buses=["eth0"], speed=1.0,
            ethertype_filter={0x0800},
            protocol_filter={17},
        )
        udp_v4 = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"keep",
        )
        icmp_v4 = _icmp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", b"drop",
        )
        udp_v6 = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"drop",
        )
        data = _make_pcap([udp_v4, icmp_v4, udp_v6])
        p = Path(tempfile.NamedTemporaryFile(suffix=".pcap", delete=False).name)
        p.write_bytes(data)
        binary = replayer.convert_to_binary(p)
        p.unlink()

        offset = 0
        records = 0
        while offset < len(binary):
            record_len = struct.unpack_from("<I", binary, offset)[0]
            offset += 4
            frame = frame_pb2.Frame()
            frame.ParseFromString(binary[offset:offset + record_len])
            offset += record_len
            records += 1
        assert records == 1  # only UDPv4 passes both filters


class TestSrcDstIpFilter:
    """Tests for direction-aware src_ip_filter and dst_ip_filter."""

    def _make_replayer(self, src_ip_filter=None, dst_ip_filter=None, **kwargs):
        from boat.trace_replay import TraceReplayer
        # No global rewrite — direction filters operate on original IPs
        params = {"buses": ["eth0"], "speed": 1.0}
        params.update(kwargs)
        params["src_ip_filter"] = src_ip_filter
        params["dst_ip_filter"] = dst_ip_filter
        return TraceReplayer(**params)

    def test_src_filter_match(self):
        """Packet whose rewritten src matches src_ip_filter is replayed."""
        replayer = self._make_replayer(src_ip_filter={"10.0.0.1"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert len(replayer._reconstruct_ip_packet(frame)) > 0

    def test_src_filter_no_match(self):
        """Packet whose rewritten src does NOT match src_ip_filter is dropped."""
        replayer = self._make_replayer(src_ip_filter={"9.9.9.9"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert replayer._reconstruct_ip_packet(frame) == b""

    def test_dst_filter_match(self):
        """Packet whose rewritten dst matches dst_ip_filter is replayed."""
        replayer = self._make_replayer(dst_ip_filter={"10.0.0.2"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert len(replayer._reconstruct_ip_packet(frame)) > 0

    def test_dst_filter_no_match(self):
        """Packet whose rewritten dst does NOT match dst_ip_filter is dropped."""
        replayer = self._make_replayer(dst_ip_filter={"9.9.9.9"})
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert replayer._reconstruct_ip_packet(frame) == b""

    def test_strict_direction(self):
        """Strict direction: src_ip_filter + dst_ip_filter block reverse traffic."""
        replayer = self._make_replayer(
            src_ip_filter={"10.0.0.1"},
            dst_ip_filter={"10.0.0.2"},
        )
        # Forward: src=10.0.0.1, dst=10.0.0.2 → matches both filters
        eth_fwd = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"fwd",
        )
        from boat.trace_replay import EthernetPcapFrame
        assert len(replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth_fwd[0:6], eth_fwd[6:12], 0x0800, eth_fwd[14:])
        )) > 0

        # Reverse: src=10.0.0.2, dst=10.0.0.1 → src_ip_filter drops
        eth_rev = _udp_packet(
            b"\x0a\x00\x00\x02", b"\x0a\x00\x00\x01", 12345, 30490, b"rev",
        )
        assert replayer._reconstruct_ip_packet(
            EthernetPcapFrame(2.0, eth_rev[0:6], eth_rev[6:12], 0x0800, eth_rev[14:])
        ) == b""

    def test_ip_filter_plus_src_filter_combine(self):
        """ip_filter (OR) + src_ip_filter (strict): reverse blocked despite OR match."""
        replayer = self._make_replayer(
            src_ip_filter={"10.0.0.1"},
            ip_filter={"10.0.0.2"},
        )
        # Forward: src=10.0.0.1 passes src filter; ip_filter doesn't match but OR passes
        eth_fwd = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"fwd",
        )
        from boat.trace_replay import EthernetPcapFrame
        assert len(replayer._reconstruct_ip_packet(
            EthernetPcapFrame(1.0, eth_fwd[0:6], eth_fwd[6:12], 0x0800, eth_fwd[14:])
        )) > 0

        # Reverse: src=10.0.0.2 — src_ip_filter drops it (10.0.0.2 not in src filter)
        eth_rev = _udp_packet(
            b"\x0a\x00\x00\x02", b"\x0a\x00\x00\x01", 12345, 30490, b"rev",
        )
        assert replayer._reconstruct_ip_packet(
            EthernetPcapFrame(2.0, eth_rev[0:6], eth_rev[6:12], 0x0800, eth_rev[14:])
        ) == b""

    def test_empty_filter_set_no_filtering(self):
        """Empty src_ip_filter / dst_ip_filter sets do no filtering."""
        replayer = self._make_replayer(src_ip_filter=set(), dst_ip_filter=set())
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert len(replayer._reconstruct_ip_packet(frame)) > 0

    def test_src_filter_over_ipv6(self):
        """src_ip_filter works with IPv6."""
        replayer = self._make_replayer(
            src_ip_filter={"2001:db8::1"},
            replay_src_ip="2001:db8::1", replay_dst_ip="2001:db8::100",
        )
        eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"data",
        )
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x86DD, eth[14:])
        assert len(replayer._reconstruct_ip_packet(frame)) > 0


class TestPortFilter:
    """Tests for src_port_filter and dst_port_filter (pre-rewrite)."""

    def _make_replayer(self, src_port_filter=None, dst_port_filter=None, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0, "replay_src_ip": "10.0.0.1",
                  "replay_dst_ip": "10.0.0.2"}
        params.update(kwargs)
        params["src_port_filter"] = src_port_filter
        params["dst_port_filter"] = dst_port_filter
        return TraceReplayer(**params)

    def test_src_port_match(self):
        replayer = self._make_replayer(src_port_filter={12345})
        eth = _udp_packet(b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data")
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert len(replayer._reconstruct_ip_packet(frame)) > 0

    def test_src_port_no_match(self):
        replayer = self._make_replayer(src_port_filter={9999})
        eth = _udp_packet(b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data")
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert replayer._reconstruct_ip_packet(frame) == b""

    def test_dst_port_match(self):
        replayer = self._make_replayer(dst_port_filter={30490})
        eth = _udp_packet(b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data")
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert len(replayer._reconstruct_ip_packet(frame)) > 0

    def test_dst_port_no_match(self):
        replayer = self._make_replayer(dst_port_filter={9999})
        eth = _udp_packet(b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data")
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert replayer._reconstruct_ip_packet(frame) == b""

    def test_port_filter_ignores_icmp(self):
        """Port filter does not affect ICMP (no ports)."""
        replayer = self._make_replayer(src_port_filter={12345})
        eth = _icmp_packet(b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", b"ping")
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert len(replayer._reconstruct_ip_packet(frame)) > 0

    def test_empty_set_no_filtering(self):
        replayer = self._make_replayer(src_port_filter=set(), dst_port_filter=set())
        eth = _udp_packet(b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data")
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert len(replayer._reconstruct_ip_packet(frame)) > 0

    def test_port_filter_over_ipv6(self):
        replayer = self._make_replayer(src_port_filter={12345})
        eth = _udp6_packet(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"data")
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x86DD, eth[14:])
        assert len(replayer._reconstruct_ip_packet(frame)) > 0


class TestIpv6ExtensionHeaders:
    """Tests for IPv6 extension header walking."""

    def _make_replayer(self, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0}
        params.update(kwargs)
        return TraceReplayer(**params)

    def test_walk_udp_no_extensions(self):
        """No extension headers: actual protocol is UDP directly."""
        replayer = self._make_replayer()
        eth = _udp_packet(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 12345, 30490, b"data")
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        assert len(replayer._reconstruct_ip_packet(frame)) > 0

    def test_walk_hbh_before_udp(self):
        """Hop-by-Hop extension (0) followed by UDP is correctly identified."""
        replayer = self._make_replayer(protocol_filter={17})
        eth = _ipv6_with_hbh(
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01",
            b"\x20\x01\x0d\xb8\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02",
            12345, 30490, b"hbhtest")
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x86DD, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0


class TestIpFragment:
    """Tests for IPv4 fragmentation handling."""

    def _make_replayer(self, **kwargs):
        from boat.trace_replay import TraceReplayer
        params = {"buses": ["eth0"], "speed": 1.0,
                  "replay_src_ip": "10.0.0.1", "replay_dst_ip": "10.0.0.2"}
        params.update(kwargs)
        return TraceReplayer(**params)

    def test_first_fragment_reconstructed(self):
        """First fragment (offset=0, MF=1) is processed, IP rewritten."""
        replayer = self._make_replayer()
        payload = b"\x30\x39\x77\x1a\x00\x0e\x00\x00" + b"data_payload"
        eth = _ipv4_frag(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", payload,
            identification=0x1234, frag_offset=0, more_frags=True)
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0
        # IP should be rewritten
        assert result[12:16] == b"\x0a\x00\x00\x01"  # 10.0.0.1
        assert result[16:20] == b"\x0a\x00\x00\x02"  # 10.0.0.2
        # Fragment flags/offset should be preserved
        frag_field = (result[6] << 8) | result[7]
        assert frag_field & 0x2000  # MF flag still set
        assert (frag_field & 0x1FFF) == 0  # offset still 0

    def test_non_first_fragment_passthrough(self):
        """Non-first fragment (offset>0) is rebuilt with IP rewrite, payload as-is."""
        replayer = self._make_replayer()
        payload = b"continuation_data_here"
        eth = _ipv4_frag(
            b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", payload,
            identification=0x1234, frag_offset=2, more_frags=True, protocol=17)
        from boat.trace_replay import EthernetPcapFrame
        frame = EthernetPcapFrame(1.0, eth[0:6], eth[6:12], 0x0800, eth[14:])
        result = replayer._reconstruct_ip_packet(frame)
        assert len(result) > 0
        assert result[12:16] == b"\x0a\x00\x00\x01"
        assert result[16:20] == b"\x0a\x00\x00\x02"
        # Fragment offset should be preserved (in 8-byte units)
        frag_field = (result[6] << 8) | result[7]
        assert (frag_field & 0x1FFF) == 2  # offset 2 in 8-byte units
        assert frag_field & 0x2000  # MF flag still set
        # Payload should be unchanged
        assert result[20:] == payload


class TestMacMap:
    """Tests for mac_map parameter (passed to C++ forwarder via gRPC)."""

    def test_mac_map_stored_on_replayer(self):
        """mac_map dict is stored on TraceReplayer."""
        from boat.trace_replay import TraceReplayer
        mm = {"192.168.0.100": "02:de:ad:be:ef:01",
              "192.168.0.101": "02:de:ad:be:ef:02"}
        replayer = TraceReplayer(buses=["eth0"], speed=1.0, mac_map=mm)
        assert replayer.mac_map == mm

    def test_mac_map_empty_default(self):
        """Empty mac_map defaults to empty dict (C++ uses fallback)."""
        from boat.trace_replay import TraceReplayer
        replayer = TraceReplayer(buses=["eth0"], speed=1.0)
        assert replayer.mac_map == {}

    def test_mac_map_passed_in_request(self):
        """mac_map is included in StartReplayRequest."""
        from boat.trace_replay import TraceReplayer
        from boat.v1 import replay_pb2
        replayer = TraceReplayer(
            buses=["eth0"], speed=1.0,
            mac_map={"10.0.0.1": "aa:bb:cc:dd:ee:01"},
        )
        req = replay_pb2.StartReplayRequest(
            trace_id="test", speed=replay_pb2.REPLAY_SPEED_ACCELERATED,
            eth_iface="eth0",
        )
        for ip_str, mac_str in replayer.mac_map.items():
            req.mac_map[ip_str] = mac_str
        assert req.mac_map["10.0.0.1"] == "aa:bb:cc:dd:ee:01"
        assert len(req.mac_map) == 1


class TestReplayRejectsPcap:
    """TraceReplayer.replay() is CAN-only; server-side delegation was removed."""

    def test_replay_raises_for_pcap(self, tmp_path):
        from boat.trace_replay import TraceReplayer, TraceReplayError

        pcap_path = tmp_path / "capture.pcap"
        pcap_path.write_bytes(_make_pcap([]))

        replayer = TraceReplayer(buses=["eth0"], speed=1.0)
        try:
            replayer.replay(str(pcap_path))
            assert False, "expected TraceReplayError"
        except TraceReplayError as e:
            assert "boat replay import" in str(e)

    def test_replay_server_side_no_longer_exists(self):
        from boat.trace_replay import TraceReplayer

        assert not hasattr(TraceReplayer, "replay_server_side")
