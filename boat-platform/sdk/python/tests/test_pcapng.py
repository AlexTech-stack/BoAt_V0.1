"""Tests for the hand-rolled PCAPNG reader/writer."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sdk" / "python"))

from boat.pcapng import (
    CanPcapFrame,
    DLT_CAN_SOCKETCAN,
    DLT_EN10MB,
    EthPcapngFrame,
    PcapngError,
    PcapngReader,
    PcapngWriter,
    pack_can_frame,
    unpack_can_frame,
)


def test_mixed_can_eth_round_trip(tmp_path):
    path = tmp_path / "mixed.pcapng"
    w = PcapngWriter(str(path))
    can_id = w.add_interface("vcan0", DLT_CAN_SOCKETCAN)
    eth_id = w.add_interface("eth0", DLT_EN10MB)

    w.write_can(can_id, 1.0, 0x123, 4, b"\xaa\xbb\xcc\xdd", 0)
    w.write_eth(eth_id, 1.1, b"\x00\x01\x02\x03\x04\x05", b"\x10\x11\x12\x13\x14\x15",
                0x0800, b"payload-bytes")
    w.write_can(can_id, 1.2, 0x456, 2, b"\x01\x02", 0)
    w.close()

    with PcapngReader(str(path)) as r:
        records = list(r)

    assert len(records) == 3
    assert isinstance(records[0], CanPcapFrame)
    assert records[0].arbitration_id == 0x123
    assert records[0].data == b"\xaa\xbb\xcc\xdd"
    assert records[0].timestamp == 1.0
    assert records[0].iface_name == "vcan0"

    assert isinstance(records[1], EthPcapngFrame)
    assert records[1].ethertype == 0x0800
    assert records[1].payload == b"payload-bytes"
    assert records[1].dst_mac == b"\x00\x01\x02\x03\x04\x05"
    assert records[1].src_mac == b"\x10\x11\x12\x13\x14\x15"
    assert records[1].iface_name == "eth0"

    assert isinstance(records[2], CanPcapFrame)
    assert records[2].arbitration_id == 0x456


def test_can_fd_flags_round_trip(tmp_path):
    path = tmp_path / "fd.pcapng"
    w = PcapngWriter(str(path))
    can_id = w.add_interface("vcan0", DLT_CAN_SOCKETCAN)

    # FDF + BRS + ESI, extended id
    flags = 0x04 | 0x01 | 0x02
    ext_id = 0x1ABCDEF | 0x80000000
    data = bytes(range(16))
    w.write_can(can_id, 2.0, ext_id, 16, data, flags)
    w.close()

    with PcapngReader(str(path)) as r:
        records = list(r)

    assert len(records) == 1
    rec = records[0]
    assert rec.is_fd is True
    assert rec.bitrate_switch is True
    assert rec.is_extended_id is True
    assert rec.arbitration_id == (0x1ABCDEF & 0x1FFFFFFF)
    assert rec.data == data


def test_classic_can_is_not_fd(tmp_path):
    path = tmp_path / "classic.pcapng"
    w = PcapngWriter(str(path))
    can_id = w.add_interface("vcan0", DLT_CAN_SOCKETCAN)
    w.write_can(can_id, 0.0, 0x001, 8, bytes(range(8)), 0)
    w.close()

    with PcapngReader(str(path)) as r:
        records = list(r)

    assert records[0].is_fd is False
    assert records[0].bitrate_switch is False
    assert records[0].is_extended_id is False


def test_pack_unpack_can_frame_identity():
    can_id, dlc, data, flags = 0x123, 4, b"\xde\xad\xbe\xef", 0
    raw = pack_can_frame(can_id, dlc, data, flags)
    assert raw == struct.pack(">IBBBB", can_id, dlc, 0, 0, 0) + data + b"\x00" * 4
    out_id, out_len, out_flags, out_data, is_fd = unpack_can_frame(raw)
    assert (out_id, out_len, out_flags, out_data, is_fd) == (can_id, dlc, 0, data, False)


def test_pack_unpack_can_fd_frame_identity():
    can_id, dlc, data, flags = 0x80000123, 12, bytes(range(12)), 0x05
    raw = pack_can_frame(can_id, dlc, data, flags)
    assert len(raw) == 72
    out_id, out_len, out_flags, out_data, is_fd = unpack_can_frame(raw)
    assert (out_id, out_len, out_flags, out_data, is_fd) == (can_id, dlc, flags, data, True)


def test_truncated_file_raises(tmp_path):
    path = tmp_path / "trunc.pcapng"
    w = PcapngWriter(str(path))
    can_id = w.add_interface("vcan0", DLT_CAN_SOCKETCAN)
    w.write_can(can_id, 0.0, 0x100, 2, b"\x01\x02", 0)
    w.close()

    data = path.read_bytes()
    trunc_path = tmp_path / "trunc2.pcapng"
    trunc_path.write_bytes(data[:-3])

    with PcapngReader(str(trunc_path)) as r:
        try:
            list(r)
            assert False, "expected PcapngError"
        except PcapngError:
            pass


def test_bad_byte_order_magic_raises(tmp_path):
    path = tmp_path / "badmagic.pcapng"
    w = PcapngWriter(str(path))
    w.close()

    data = bytearray(path.read_bytes())
    data[8:12] = b"\x00\x00\x00\x00"  # corrupt the byte-order magic field
    bad_path = tmp_path / "badmagic2.pcapng"
    bad_path.write_bytes(bytes(data))

    try:
        PcapngReader(str(bad_path))
        assert False, "expected PcapngError"
    except PcapngError:
        pass


def test_writer_close_is_idempotent(tmp_path):
    path = tmp_path / "idempotent.pcapng"
    w = PcapngWriter(str(path))
    w.close()
    w.close()  # must not raise


# ── trace_replay.py integration ──────────────────────────────────────────
# Regression coverage for the "single bus type per whole file" assumption
# that convert_to_binary() used to make (isinstance(reader, EthernetPcapReader)
# computed once) -- a pcapng file can interleave CAN and Ethernet records,
# so the fix dispatches per-message instead. These need frame_pb2
# (protobuf), same as test_eth_replay.py in this directory.

def _udp_ip4_payload(src_ip: bytes, dst_ip: bytes, src_port: int, dst_port: int,
                      payload: bytes, ttl: int = 64) -> bytes:
    """Valid IPv4+UDP bytes as convert_to_binary()'s Ethernet path expects
    (it reconstructs/rewrites IP headers, so non-IP Ethernet payloads are
    intentionally dropped -- this must be a real IP packet)."""
    udp_len = 8 + len(payload)
    udp_hdr = struct.pack("!HHHH", src_port, dst_port, udp_len, 0)
    total_len = 20 + udp_len
    ip_hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total_len, 0x1234, 0, ttl, 17, 0, src_ip, dst_ip)
    s = sum(struct.unpack("!H", ip_hdr[i:i + 2])[0] for i in range(0, len(ip_hdr), 2))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    ip_hdr = ip_hdr[:10] + struct.pack("!H", (~s) & 0xFFFF) + ip_hdr[12:]
    return ip_hdr + udp_hdr + payload


def test_convert_to_binary_handles_mixed_pcapng(tmp_path):
    from boat.trace_replay import TraceReplayer
    from boat.v1 import frame_pb2

    ip_payload = _udp_ip4_payload(b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02", 1234, 5678, b"data")

    path = tmp_path / "mixed.pcapng"
    w = PcapngWriter(str(path))
    can_id = w.add_interface("vcan0", DLT_CAN_SOCKETCAN)
    eth_id = w.add_interface("eth0", DLT_EN10MB)
    w.write_can(can_id, 1.0, 0x123, 4, b"\xaa\xbb\xcc\xdd", 0)
    w.write_eth(eth_id, 1.1, b"\x00" * 6, b"\x11" * 6, 0x0800, ip_payload)
    w.write_can(can_id, 1.2, 0x456, 2, b"\x01\x02", 0)
    w.close()

    replayer = TraceReplayer(buses=["vcan0"], eth_iface="eth0")
    binary = replayer.convert_to_binary(path)
    frames = TraceReplayer.parse_binary(binary)

    assert len(frames) == 3
    assert frames[0].bus_type == frame_pb2.Frame.CAN
    assert frames[0].can.can_id == 0x123
    assert frames[1].bus_type == frame_pb2.Frame.ETHERNET
    assert frames[2].bus_type == frame_pb2.Frame.CAN
    assert frames[2].can.can_id == 0x456


def test_export_to_pcapng_round_trip(tmp_path):
    from boat.trace_replay import TraceReplayer
    from boat.v1 import frame_pb2

    frames = [
        frame_pb2.Frame(
            bus_type=frame_pb2.Frame.CAN, timestamp_ns=1_000_000_000, payload=b"\x01\x02",
            can=frame_pb2.CanMetadata(can_id=0x100, dlc=2, flags=0, channel=1),
        ),
        frame_pb2.Frame(
            bus_type=frame_pb2.Frame.ETHERNET, timestamp_ns=1_100_000_000, iface="eth0",
            payload=b"payload-bytes",
            eth=frame_pb2.EthMetadata(dst_mac=b"\x00" * 6, src_mac=b"\x11" * 6, ethertype=0x88B5),
        ),
    ]
    out = tmp_path / "export.pcapng"
    stats = TraceReplayer.export_to_pcapng(frames, out)

    assert stats == {"can_frames": 1, "eth_frames": 1, "skipped": 0, "path": str(out)}

    # Re-read the exported file directly via PcapngReader (format-symmetric
    # check) rather than through convert_to_binary(), which reconstructs/
    # rewrites IP headers and would drop this non-IP ethertype -- that's
    # convert_to_binary()'s own, unrelated behavior, not something
    # export_to_pcapng() needs to round-trip through.
    with PcapngReader(str(out)) as r:
        reimported = list(r)
    assert len(reimported) == 2
    assert reimported[0].arbitration_id == 0x100
    assert reimported[1].ethertype == 0x88B5
    assert reimported[1].payload == b"payload-bytes"


def test_export_to_pcapng_skips_tcp_and_pdu(tmp_path):
    from boat.trace_replay import TraceReplayer
    from boat.v1 import frame_pb2

    frames = [
        frame_pb2.Frame(
            bus_type=frame_pb2.Frame.CAN, timestamp_ns=1, payload=b"\x01",
            can=frame_pb2.CanMetadata(can_id=1, dlc=1, flags=0, channel=1),
        ),
        frame_pb2.Frame(bus_type=frame_pb2.Frame.PDU, timestamp_ns=2, payload=b"x",
                         pdu=frame_pb2.PduMetadata(pdu_id=1)),
        frame_pb2.Frame(bus_type=frame_pb2.Frame.TCP, timestamp_ns=3, payload=b"y",
                         tcp=frame_pb2.TcpMetadata(src_port=1, dst_port=2)),
    ]
    out = tmp_path / "skip.pcapng"
    stats = TraceReplayer.export_to_pcapng(frames, out)
    assert stats["can_frames"] == 1
    assert stats["eth_frames"] == 0
    assert stats["skipped"] == 2
