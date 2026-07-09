"""Hardware HIL test: ISO-TP CAN Bridge via DUT.

Tests that the DUT correctly bridges CAN frames bidirectionally
between can0 and can1 using ISO 15765-2 Transport Protocol.

Architecture:
  Test Host                          DUT (CAN bridge)
  ┌──────────────────────────────┐  ┌───────────────┐
  │ CanTp-A (can0)               │  │               │
  │   source=0x400 target=0x401  │──┤ can0 ↔ can1  │
  │   sends payload via plugin   │  │ transparent  │
  │   receives FC via RX thread  │  │ bridge       │
  │                              │  │               │
  │ CanTp-B (can1)               │──┤               │
  │   source=0x401 target=0x400  │  │               │
  │   receives FF/CF via RX thr  │  │               │
  │   sends FC (BS=5, STmin=3)  │  │               │
  │   reassembles → PDU callback │  │               │
  └──────────────────────────────┘  └───────────────┘

Prerequisites:
  - can0, can1 physical interfaces up and connected to DUT
  - DUT bridges can0 ↔ can1 transparently
  - BOAT_HIL_ENABLED environment variable set

Usage:
  BOAT_HIL_ENABLED=1 python3 hw_can_tp_hil_test.py
"""

import ctypes
import os
import socket
import struct
import sys
import threading
import time

# ── Constants ────────────────────────────────────────────────────────────

CAN_ID_DATA = 0x400   # Data frames (SF, FF, CF) direction can0→can1
CAN_ID_FC   = 0x401   # Flow Control frames direction can1→can0
BS          = 5
STMIN_MS    = 3
TEST_PAYLOAD_SIZE = 100

# ── Raw CAN helpers (used internally by plugin handle) ───────────────────

_socket_mtu: dict = {}


def _can_mtu(iface: str) -> int:
    try:
        with open(f"/sys/class/net/{iface}/mtu") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 16


def open_can_socket(iface: str, timeout_s: float = 3.0):
    sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    sock.settimeout(timeout_s)
    mtu = _can_mtu(iface)
    if mtu >= 72:
        sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FD_FRAMES, 1)
    sock.bind((iface,))
    _socket_mtu[sock.fileno()] = mtu
    return sock


def send_can_frame(sock, can_id: int, data: bytes):
    can_id_field = can_id
    dlc = len(data)
    mtu = _socket_mtu.get(sock.fileno(), 16)
    if mtu >= 72:
        frame = struct.pack("<IB3x", can_id_field, dlc) + data.ljust(64, b'\x00')
    else:
        if dlc > 8:
            raise ValueError(f"DLC {dlc} exceeds classic CAN max (8)")
        frame = struct.pack("<IB3x", can_id_field, dlc) + data.ljust(8, b'\x00')
    sock.send(frame, 0)


def recv_can_frame(sock):
    mtu = _socket_mtu.get(sock.fileno(), 16)
    bufsize = 72 if mtu >= 72 else 16
    raw = sock.recv(bufsize)
    can_id, dlc = struct.unpack_from("<IB3x", raw, 0)
    data = raw[8:8 + dlc]
    return can_id & 0x1FFFFFFF, dlc, data


# ── CanTp plugin handle via ctypes ───────────────────────────────────────

class _BoatCanFrame(ctypes.Structure):
    _fields_ = [
        ("can_id", ctypes.c_uint32),
        ("dlc", ctypes.c_uint8),
        ("flags", ctypes.c_uint8),
        ("data", ctypes.c_uint8 * 64),
    ]


class _BoatPduFrame(ctypes.Structure):
    _fields_ = [
        ("pdu_id", ctypes.c_uint32),
        ("payload", ctypes.POINTER(ctypes.c_uint8)),
        ("payload_len", ctypes.c_size_t),
        ("iface", ctypes.c_char_p),
    ]


class CanTpPluginHandle:
    """Loads can_tp.so and manages a plugin instance.

    Wires raw CAN sockets (TX + RX + RX listener) and a PDU publisher
    callback to capture reassembled payloads.  Independent of any gateway.
    """

    def __init__(self, so_path: str, iface: str, source_addr: int,
                 target_addr: int, nsdu_id: int = 1,
                 block_size: int = 0, st_min: int = 0):
        if not os.path.exists(so_path):
            raise FileNotFoundError(f"can_tp.so not found: {so_path}")

        self._lib = ctypes.CDLL(so_path)
        self._iface = iface
        self._source_addr = source_addr
        self._target_addr = target_addr
        self._nsdu_id = nsdu_id
        self._rx_stop = threading.Event()
        self._rx_thread = None
        self._received_payload = None
        self._payload_event = threading.Event()

        # Create plugin instance
        self._lib.boat_plugin_create.restype = ctypes.c_void_p
        plugin_ptr = self._lib.boat_plugin_create()
        if not plugin_ptr:
            raise RuntimeError("boat_plugin_create() returned null")
        self._plugin_ptr = plugin_ptr

        # BoatPlugin layout: { vtable: void*, ctx: void* }
        ctx_arr = ctypes.cast(plugin_ptr, ctypes.POINTER(ctypes.c_void_p))
        vtable_ptr = ctx_arr[0]
        self._ctx = ctx_arr[1]

        def _vtable_fn(index):
            return ctypes.cast(
                ctypes.c_void_p(
                    ctypes.cast(vtable_ptr, ctypes.POINTER(ctypes.c_void_p))[index]
                ),
                ctypes.c_void_p,
            )

        # Initialize
        INIT_FN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p)
        config_json = f'{{"iface":"{iface}"}}'
        initialize = ctypes.cast(_vtable_fn(0), INIT_FN)
        if initialize(self._ctx, config_json.encode()) != 0:
            raise RuntimeError("tp_initialize failed")

        # Open raw CAN sockets
        self._tx_sock = open_can_socket(iface)
        self._rx_sock = open_can_socket(iface)

        # Wire CAN publisher
        CAN_PUB_FN = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.POINTER(_BoatCanFrame)
        )

        def _can_publish(pub_ctx, frame_ptr):
            try:
                frame = frame_ptr.contents
                data = bytes(frame.data[:frame.dlc])
                send_can_frame(self._tx_sock, frame.can_id, data)
            except Exception:
                pass

        self._can_publish_cb = CAN_PUB_FN(_can_publish)

        SET_CAN_PUB_FN = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )
        set_can_pub = ctypes.cast(_vtable_fn(4), SET_CAN_PUB_FN)
        dummy = ctypes.c_int(0)
        set_can_pub(self._ctx, self._can_publish_cb, ctypes.byref(dummy))

        # Wire PDU publisher (vtable index 9) — captures reassembled payloads
        PDU_PUB_FN = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.POINTER(_BoatPduFrame)
        )

        def _pdu_publish(pub_ctx, pdu_ptr):
            try:
                pdu = pdu_ptr.contents
                data = bytes(ctypes.cast(
                    pdu.payload, ctypes.POINTER(ctypes.c_uint8 * pdu.payload_len)
                ).contents)
                self._received_payload = data
                self._payload_event.set()
            except Exception:
                pass

        self._pdu_publish_cb = PDU_PUB_FN(_pdu_publish)

        SET_PDU_PUB_FN = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )
        set_pdu_pub = ctypes.cast(_vtable_fn(9), SET_PDU_PUB_FN)
        set_pdu_pub(self._ctx, self._pdu_publish_cb, ctypes.byref(dummy))

        # Configure ISO-TP session
        _CanTpConfig = type(
            "CanTpConfig",
            (ctypes.Structure,),
            {
                "_fields_": [
                    ("nsdu_id", ctypes.c_uint32),
                    ("source_addr", ctypes.c_uint32),
                    ("target_addr", ctypes.c_uint32),
                    ("rx_buffer_size", ctypes.c_uint32),
                    ("block_size", ctypes.c_uint8),
                    ("st_min", ctypes.c_uint8),
                    ("can_dlc", ctypes.c_uint8),
                    ("extended_addressing", ctypes.c_bool),
                ]
            },
        )
        cfg = _CanTpConfig(
            nsdu_id=nsdu_id,
            source_addr=source_addr,
            target_addr=target_addr,
            rx_buffer_size=4095,
            block_size=block_size,
            st_min=st_min,
            can_dlc=8,
            extended_addressing=False,
        )
        self._lib.can_tp_configure.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_CanTpConfig)
        ]
        self._lib.can_tp_configure.restype = ctypes.c_int32
        result = self._lib.can_tp_configure(self._ctx, ctypes.byref(cfg))
        if result != 0:
            raise RuntimeError(f"can_tp_configure returned {result}")

        # Start RX listener (vtable index 5 = on_can_frame)
        ON_CAN_FRAME_FN = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.POINTER(_BoatCanFrame), ctypes.c_char_p
        )
        self._on_can_frame = ctypes.cast(_vtable_fn(5), ON_CAN_FRAME_FN)

        def _rx_listener():
            iface_bytes = iface.encode()
            while not self._rx_stop.is_set():
                try:
                    self._rx_sock.settimeout(0.5)
                    can_id, dlc, data = recv_can_frame(self._rx_sock)
                except socket.timeout:
                    continue
                except OSError:
                    break
                # Skip own outgoing frames (same source CAN ID)
                if can_id == source_addr:
                    continue
                bcf = _BoatCanFrame()
                bcf.can_id = can_id
                bcf.dlc = dlc
                for i in range(min(dlc, 64)):
                    bcf.data[i] = data[i]
                self._on_can_frame(self._ctx, ctypes.byref(bcf), iface_bytes)

        self._rx_thread = threading.Thread(target=_rx_listener, daemon=True)
        self._rx_thread.start()

    def send(self, data: bytes) -> int:
        self._lib.can_tp_send.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
        ]
        self._lib.can_tp_send.restype = ctypes.c_int32
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        return self._lib.can_tp_send(self._ctx, self._nsdu_id, buf, len(data))

    def wait_for_payload(self, timeout_s: float = 5.0) -> bytes:
        if not self._payload_event.wait(timeout=timeout_s):
            raise TimeoutError(
                "No payload received within timeout"
            )
        return self._received_payload

    def close(self):
        self._rx_stop.set()
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=2.0)
        if self._plugin_ptr:
            self._lib.boat_plugin_destroy.argtypes = [ctypes.c_void_p]
            self._lib.boat_plugin_destroy(self._plugin_ptr)
            self._plugin_ptr = None
        for sock in ("_tx_sock", "_rx_sock"):
            s = getattr(self, sock, None)
            if s:
                s.close()
                setattr(self, sock, None)


# ── Test logic ───────────────────────────────────────────────────────────

def _ensure_env():
    if not os.environ.get("BOAT_HIL_ENABLED"):
        print("SKIP: BOAT_HIL_ENABLED not set")
        return False
    for iface in ("can0", "can1"):
        path = f"/sys/class/net/{iface}"
        if not os.path.exists(path):
            print(f"SKIP: Interface {iface} not found")
            return False
        with open(f"{path}/operstate") as f:
            state = f.read().strip()
        if state != "up":
            print(f"SKIP: Interface {iface} is {state}, expected 'up'")
            return False
    return True


def _find_can_tp_so() -> str:
    candidates = [
        "build/debug/src/plugins/can_tp/can_tp.so",
        "build/release/src/plugins/can_tp/can_tp.so",
        "/usr/local/lib/boat/plugins/can_tp.so",
    ]
    for c in candidates:
        path = os.path.join(os.path.dirname(__file__) or ".", "..", "..", c)
        if os.path.exists(path):
            return os.path.abspath(path)
        if os.path.exists(c):
            return os.path.abspath(c)
    raise FileNotFoundError(
        "can_tp.so not found. Build it first: cmake --build --preset debug"
    )


def test_dut_bridge(so_path: str):
    """Send 100 bytes via CanTp on can0 → DUT bridges to can1 → verify on can1.

    Two plugin instances:
      - Sender on can0: source=0x400, target=0x401
      - Receiver on can1: source=0x401, target=0x400

    The ISO-TP handshake flows through the DUT bridge automatically:
      sender FF(can0) → DUT → receiver on can1
      receiver FC(BS=5,STmin=3) on can1 → DUT → sender on can0
      sender CFs(can0) → DUT → receiver on can1 (in blocks of BS=5)
      receiver re-FCs after each block → DUT → sender ...
      → receiver reassembles and delivers via PDU callback
    """
    print(f"\n--- ISO-TP Bridge Test ---")

    sender = None
    receiver = None
    try:
        sender = CanTpPluginHandle(
            so_path=so_path, iface="can0",
            source_addr=CAN_ID_DATA, target_addr=CAN_ID_FC,
            nsdu_id=1, block_size=BS, st_min=STMIN_MS,
        )
        print(f"  Sender: can0 source=0x{CAN_ID_DATA:X} target=0x{CAN_ID_FC:X} BS={BS} STmin={STMIN_MS}ms")

        receiver = CanTpPluginHandle(
            so_path=so_path, iface="can1",
            source_addr=CAN_ID_FC, target_addr=CAN_ID_DATA,
            nsdu_id=2, block_size=BS, st_min=STMIN_MS,
        )
        print(f"  Receiver: can1 source=0x{CAN_ID_FC:X} target=0x{CAN_ID_DATA:X} BS={BS} STmin={STMIN_MS}ms")

        payload = bytes(range(TEST_PAYLOAD_SIZE))
        print(f"  Payload: {len(payload)} bytes")

        ret = sender.send(payload)
        print(f"  can_tp_send returned {ret} ({'single' if ret > 0 else 'multi'} frame)")

        received = receiver.wait_for_payload(timeout_s=8.0)
        if received != payload:
            print(f"  MISMATCH: sent {len(payload)}B received {len(received)}B")
            print(f"  First 16 sent:     {payload[:16].hex()}")
            print(f"  First 16 received: {received[:16].hex()}")
            print(f"  Last 16 sent:      {payload[-16:].hex()}")
            print(f"  Last 16 received:  {received[-16:].hex()}")
            for i in range(min(len(payload), len(received))):
                if payload[i] != received[i]:
                    print(f"  First diff at byte {i}: sent={payload[i]:02x} received={received[i]:02x}")
                    break
        assert received == payload, (
            f"Payload mismatch: sent {len(payload)}B, received {len(received)}B"
        )
        print(f"  PASS: Received {len(received)} bytes, payload matches")

    finally:
        if sender:
            sender.close()
        if receiver:
            receiver.close()


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    if not _ensure_env():
        sys.exit(0)

    so_path = _find_can_tp_so()
    print(f"=== ISO-TP CAN Bridge HIL Test ===")
    print(f"  can_tp.so: {so_path}")
    print(f"  CAN IDs: data=0x{CAN_ID_DATA:X}, fc=0x{CAN_ID_FC:X}")
    print(f"  Block Size: {BS}, STmin: {STMIN_MS}ms")

    test_dut_bridge(so_path)

    print("\n=== ALL TESTS PASSED ===")


if __name__ == "__main__":
    main()
