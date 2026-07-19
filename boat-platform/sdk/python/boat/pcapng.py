"""PCAPNG (pcap-next-generation) reader/writer for mixed CAN + Ethernet traces.

Hand-rolled (no third-party dependency), mirroring the existing hand-rolled
classic-pcap reader/writer already in this codebase (``EthernetPcapReader``
in ``trace_replay.py``, ``PcapCanWriter``/``PcapEthWriter`` in
``ui/recorder.py``). Unlike classic pcap (one link-type per whole file),
PCAPNG supports multiple Interface Description Blocks, so a single file can
carry CAN and Ethernet frames on independent interfaces sharing one
timeline -- the capability this module exists to add.

Blocks implemented: Section Header Block (SHB), Interface Description Block
(IDB), Enhanced Packet Block (EPB). Little-endian only -- BoAt never writes
big-endian pcapng, and reading a big-endian section raises ``PcapngError``.
See https://www.ietf.org/archive/id/draft-ietf-opsawg-pcapng-03.html

CAN records are yielded as ``CanPcapFrame`` and Ethernet records as
``EthPcapngFrame``. The latter is deliberately a distinct class from
``trace_replay.EthernetPcapFrame`` (same field shape, duck-type compatible)
rather than an import of it, to avoid a ``pcapng`` <-> ``trace_replay``
import cycle -- callers that need to tell CAN and Ethernet records apart in
a mixed stream should check ``hasattr(record, "ethertype")`` rather than
``isinstance``, the same duck-typing style already used throughout
``trace_replay.py`` (``getattr(msg, "channel", None)`` etc.).
"""
from __future__ import annotations

import struct
import threading
from collections import namedtuple
from typing import Dict

# ── Link types (DLT) ─────────────────────────────────────────────────────
DLT_EN10MB = 1      # Ethernet
DLT_CAN_SOCKETCAN = 227  # Linux SocketCAN can_frame/canfd_frame

# ── Block types ───────────────────────────────────────────────────────────
_BT_SHB = 0x0A0D0D0A
_BT_IDB = 0x00000001
_BT_EPB = 0x00000006
_BYTE_ORDER_MAGIC = 0x1A2B3C4D

# ── Option codes (shared by every block type) ────────────────────────────
_OPT_ENDOFOPT = 0
_OPT_IF_NAME = 2
_OPT_IF_TSRESOL = 9

# CAN FD flags (matches gateway constants in trace_replay.py)
_CANFD_BRS = 0x01
_CANFD_ESI = 0x02
_CANFD_FDF = 0x04


class PcapngError(RuntimeError):
    pass


CanPcapFrame = namedtuple("CanPcapFrame", [
    "timestamp", "channel", "iface_name", "arbitration_id", "data",
    "is_fd", "is_extended_id", "bitrate_switch",
])

EthPcapngFrame = namedtuple("EthPcapngFrame", [
    "timestamp", "dst_mac", "src_mac", "ethertype", "payload", "iface_name",
])

_Interface = namedtuple("_Interface", ["name", "linktype", "tsresol_divisor"])


def _pad4(n: int) -> int:
    return (4 - (n % 4)) % 4


def _pack_option(code: int, value: bytes) -> bytes:
    return struct.pack("<HH", code, len(value)) + value + b"\x00" * _pad4(len(value))


def pack_can_frame(can_id: int, dlc: int, data: bytes, flags: int) -> bytes:
    """Pack a CAN/CAN-FD frame per Linux SocketCAN's can_frame/canfd_frame
    layout, big-endian ``can_id``. DLT_CAN_SOCKETCAN requires network byte
    order -- little-endian makes Wireshark display the wrong CAN ID.
    """
    is_fd = bool(flags & _CANFD_FDF)
    if is_fd:
        # canfd_frame: 4 id (BE) + 1 len + 1 flags + 2 pad + 64 data = 72 B
        return struct.pack(">IBBBB", can_id, dlc, flags, 0, 0) + \
            (data[:dlc] + b"\x00" * 64)[:64]
    # can_frame: 4 id (BE) + 1 dlc + 3 pad + 8 data = 16 B
    return struct.pack(">IBBBB", can_id, dlc, 0, 0, 0) + \
        (data[:dlc] + b"\x00" * 8)[:8]


def unpack_can_frame(raw: bytes) -> tuple[int, int, int, bytes, bool]:
    """Inverse of :func:`pack_can_frame`.

    Returns ``(can_id, length, flags, data, is_fd)``. FD-ness is decided by
    frame size (72 bytes = canfd_frame, 16 bytes = can_frame), matching how
    Wireshark itself distinguishes the two on DLT_CAN_SOCKETCAN.
    """
    if len(raw) < 8:
        raise PcapngError(f"Truncated CAN frame ({len(raw)} bytes)")
    can_id, length, flags, _, _ = struct.unpack(">IBBBB", raw[:8])
    is_fd = len(raw) >= 72
    if not is_fd:
        flags = 0
    data = raw[8:8 + length]
    if len(data) != length:
        raise PcapngError("Truncated CAN frame payload")
    return can_id, length, flags, bytes(data), is_fd


def pack_eth_frame(dst_mac: bytes, src_mac: bytes, ethertype: int, payload: bytes) -> bytes:
    dst = dst_mac if len(dst_mac) == 6 else b"\xff" * 6
    src = src_mac if len(src_mac) == 6 else b"\x00" * 6
    return dst + src + struct.pack(">H", ethertype) + payload


class PcapngReader:
    """Iterate CAN + Ethernet records from a PCAPNG file.

    Yields ``CanPcapFrame`` for DLT_CAN_SOCKETCAN interfaces and
    ``EthPcapngFrame`` for DLT_EN10MB interfaces, interleaved in file
    order. Records on any other link type are silently skipped. Context
    manager, mirrors ``trace_replay.EthernetPcapReader``.

    ``.interfaces`` (``{interface_id: _Interface}``) is populated as
    Interface Description Blocks are encountered during iteration --
    inspect it after (or during) iteration, not before.
    """

    def __init__(self, path: str) -> None:
        self._f = open(path, "rb")
        self.interfaces: Dict[int, _Interface] = {}
        self._next_iface_id = 0
        try:
            self._read_shb()
        except PcapngError:
            self._f.close()
            raise
        except Exception as e:
            self._f.close()
            raise PcapngError(f"Invalid pcapng file: {e}") from e

    def __enter__(self) -> "PcapngReader":
        return self

    def __exit__(self, *args) -> None:
        self._f.close()

    def __iter__(self) -> "PcapngReader":
        return self

    def __next__(self):
        while True:
            block = self._read_block()
            if block is None:
                self._f.close()
                raise StopIteration
            block_type, body = block
            if block_type == _BT_IDB:
                self._handle_idb(body)
                continue
            if block_type == _BT_EPB:
                record = self._handle_epb(body)
                if record is not None:
                    return record
                continue
            # SHB (mid-file section change), SPB, NRB, ISB, custom blocks,
            # etc. -- not needed for CAN/Ethernet extraction, skip.
            continue

    # ── Block-level I/O ───────────────────────────────────────────────────

    def _read_exact(self, n: int) -> bytes:
        data = self._f.read(n)
        if len(data) != n:
            raise PcapngError("Truncated pcapng block")
        return data

    def _read_block(self):
        """Read one block, validating leading/trailing length agree.

        Returns ``(block_type, body)`` or ``None`` at a clean EOF (no bytes
        left before the next block would start). Any other truncation
        raises ``PcapngError`` -- unlike ``EthernetPcapReader``, a
        corrupt/truncated pcapng file is a hard error, not a silent
        early-EOF.
        """
        head = self._f.read(4)
        if len(head) == 0:
            return None
        if len(head) != 4:
            raise PcapngError("Truncated pcapng block header")
        (block_type,) = struct.unpack("<I", head)
        (total_len,) = struct.unpack("<I", self._read_exact(4))
        if total_len < 12 or total_len % 4 != 0:
            raise PcapngError(f"Invalid pcapng block length {total_len}")
        body = self._read_exact(total_len - 12)
        (trailer_len,) = struct.unpack("<I", self._read_exact(4))
        if trailer_len != total_len:
            raise PcapngError("Corrupt pcapng block: length mismatch")
        return block_type, body

    def _read_shb(self) -> None:
        block = self._read_block()
        if block is None:
            raise PcapngError("Empty pcapng file")
        block_type, body = block
        if block_type != _BT_SHB:
            raise PcapngError("pcapng file does not start with a Section Header Block")
        if len(body) < 16:
            raise PcapngError("Truncated Section Header Block")
        (magic,) = struct.unpack_from("<I", body, 0)
        if magic != _BYTE_ORDER_MAGIC:
            raise PcapngError(
                f"Unsupported pcapng byte order magic 0x{magic:08x} "
                "(only little-endian sections are supported)"
            )

    def _handle_idb(self, body: bytes) -> None:
        if len(body) < 8:
            raise PcapngError("Truncated Interface Description Block")
        linktype, _reserved, _snaplen = struct.unpack_from("<HHI", body, 0)
        name = ""
        tsresol_divisor = 10 ** 6  # spec default when if_tsresol is absent: microseconds
        offset = 8
        while offset + 4 <= len(body):
            code, length = struct.unpack_from("<HH", body, offset)
            offset += 4
            if code == _OPT_ENDOFOPT:
                break
            value = body[offset:offset + length]
            if code == _OPT_IF_NAME:
                name = value.decode("utf-8", errors="replace")
            elif code == _OPT_IF_TSRESOL and length >= 1:
                raw = value[0]
                tsresol_divisor = (2 ** (raw & 0x7F)) if (raw & 0x80) else (10 ** raw)
            offset += length + _pad4(length)
        iface_id = self._next_iface_id
        self._next_iface_id += 1
        self.interfaces[iface_id] = _Interface(
            name=name or f"if{iface_id}", linktype=linktype, tsresol_divisor=tsresol_divisor,
        )

    def _handle_epb(self, body: bytes):
        if len(body) < 20:
            raise PcapngError("Truncated Enhanced Packet Block")
        iface_id, ts_high, ts_low, caplen, _origlen = struct.unpack_from("<IIIII", body, 0)
        data = body[20:20 + caplen]
        if len(data) != caplen:
            raise PcapngError("Truncated Enhanced Packet Block payload")
        iface = self.interfaces.get(iface_id)
        if iface is None:
            raise PcapngError(f"Enhanced Packet Block references unknown interface {iface_id}")
        timestamp = ((ts_high << 32) | ts_low) / iface.tsresol_divisor

        if iface.linktype == DLT_EN10MB:
            if len(data) < 14:
                return None  # undersized Ethernet frame -- skip, don't fail the whole file
            return EthPcapngFrame(
                timestamp=timestamp,
                dst_mac=data[0:6],
                src_mac=data[6:12],
                ethertype=(data[12] << 8) | data[13],
                payload=data[14:],
                iface_name=iface.name,
            )
        if iface.linktype == DLT_CAN_SOCKETCAN:
            can_id, _length, flags, frame_data, is_fd = unpack_can_frame(data)
            return CanPcapFrame(
                timestamp=timestamp,
                channel=iface_id + 1,
                iface_name=iface.name,
                arbitration_id=can_id & 0x1FFFFFFF,
                data=frame_data,
                is_fd=is_fd,
                is_extended_id=bool(can_id & 0x80000000),
                bitrate_switch=bool(flags & _CANFD_BRS),
            )
        return None  # link type not needed for CAN/Ethernet extraction -- skip


class PcapngWriter:
    """Write CAN + Ethernet frames to one PCAPNG file across multiple
    interfaces.

    Thread-safe: one lock guards every write+flush. Unlike classic pcap's
    one-writer-per-bus-type-per-file model, PCAPNG interleaves CAN and
    Ethernet Enhanced Packet Blocks into a single file/fd, and
    ``ui/recorder.py`` drives CAN and Ethernet capture from two independent
    threads -- both must be able to append safely without corrupting each
    other's blocks.
    """

    def __init__(self, path) -> None:
        self._f = open(path, "wb")
        self._lock = threading.Lock()
        self._closed = False
        self._interfaces: list[str] = []
        self._write_shb()

    def _write_block(self, block_type: int, body: bytes) -> None:
        body = body + b"\x00" * _pad4(len(body))
        total_len = 12 + len(body)
        self._f.write(struct.pack("<I", block_type))
        self._f.write(struct.pack("<I", total_len))
        self._f.write(body)
        self._f.write(struct.pack("<I", total_len))

    def _write_shb(self) -> None:
        # byte-order magic(4) + major(2) + minor(2) + section_length(8, -1 = unknown)
        body = struct.pack("<IHHq", _BYTE_ORDER_MAGIC, 1, 0, -1)
        with self._lock:
            self._write_block(_BT_SHB, body)
            self._f.flush()

    def add_interface(self, name: str, dlt: int) -> int:
        """Register a CAN or Ethernet interface. Must be called before
        writing any frame that references it (via the returned id).
        """
        opts = _pack_option(_OPT_IF_NAME, name.encode("utf-8"))
        opts += _pack_option(_OPT_IF_TSRESOL, bytes([9]))  # nanosecond resolution
        opts += _pack_option(_OPT_ENDOFOPT, b"")
        body = struct.pack("<HHI", dlt, 0, 65535) + opts  # linktype, reserved, snaplen
        with self._lock:
            iface_id = len(self._interfaces)
            self._interfaces.append(name)
            self._write_block(_BT_IDB, body)
            self._f.flush()
        return iface_id

    def _write_epb(self, interface_id: int, ts: float, data: bytes) -> None:
        ts_ns = round(ts * 1_000_000_000)
        ts_high = (ts_ns >> 32) & 0xFFFFFFFF
        ts_low = ts_ns & 0xFFFFFFFF
        body = struct.pack("<IIIII", interface_id, ts_high, ts_low, len(data), len(data)) + data
        with self._lock:
            self._write_block(_BT_EPB, body)
            self._f.flush()

    def write_can(self, interface_id: int, ts: float, can_id: int, dlc: int, data: bytes, flags: int) -> None:
        self._write_epb(interface_id, ts, pack_can_frame(can_id, dlc, data, flags))

    def write_eth(self, interface_id: int, ts: float, dst_mac: bytes, src_mac: bytes,
                  ethertype: int, payload: bytes) -> None:
        self._write_epb(interface_id, ts, pack_eth_frame(dst_mac, src_mac, ethertype, payload))

    def close(self) -> None:
        """Idempotent -- safe to call more than once (recorder.py may hold
        two session fields aliasing the same writer for a mixed session)."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._f.close()
            except Exception:
                pass
