"""
BoAt Platform — Commander Panel
Run:  python3 demo/commander.py
Open: http://localhost:8082
"""
from __future__ import annotations
import os
import sys
import threading
import time as _time_module
from pathlib import Path
from typing import Optional
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))
import grpc
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from boat.client import BoAtClient
from boat.v1 import bus_pb2, can_pb2, ethernet_pb2
from boat.pdu_db import PduDatabase
_CANFD_FDF = 0x04
_CANFD_BRS = 0x01
_DEFAULT_GW = os.environ.get("BOAT_GATEWAY", "localhost:50051")
_PORT       = int(os.environ.get("BOAT_CMD_PORT", "8082"))
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "boat-platform" / "config"
_LAST_IMPORT_FILE = _CONFIG_DIR / ".last_import"
_PDU_DB: Optional[PduDatabase] = None
_PDU_FILENAME: Optional[str] = None
# ── gRPC client cache (one channel per address) ────────────────────────────────
_clients: dict[str, BoAtClient] = {}
def _client(address: str) -> BoAtClient:
    if address not in _clients:
        _clients[address] = BoAtClient(address)
    return _clients[address]
# ── PDU signal packing ────────────────────────────────────────────────────────
def _pack_intel(data: bytearray, start_pos: int, length: int, value: int) -> None:
    """Intel/Little-endian: LSB at start_pos, bits increment upward."""
    for i in range(length):
        bit_pos = start_pos + i
        byte_idx = bit_pos // 8
        bit_idx = bit_pos % 8
        if (value >> i) & 1:
            data[byte_idx] |= (1 << bit_idx)

def _pack_motorola(data: bytearray, start_pos: int, length: int, value: int) -> None:
    """Motorola/Big-endian: MSB at start_pos, address decreases downward."""
    current_byte = start_pos // 8
    current_bit = start_pos % 8
    for bit_i in range(length):
        if (value >> (length - 1 - bit_i)) & 1:
            data[current_byte] |= (1 << current_bit)
        current_bit -= 1
        if current_bit < 0:
            current_byte -= 1
            current_bit = 7

def _build_frame_data(message: dict, db_id: int | None = None) -> bytes:
    """Pack all signals from a PDU message into a byte array using override values."""
    if db_id is not None and db_id in _PAYLOAD_OVERRIDES:
        return _PAYLOAD_OVERRIDES[db_id]
    frame_length = message.get("Length", 8)
    data = bytearray(frame_length)
    for sig in message.get("signals", []):
        init_val = sig.get("InitValue", 0)
        if db_id is not None:
            override_val = _SIGNAL_VALUES.get((db_id, sig["id"]))
            if override_val is not None:
                init_val = override_val
        factor = float(sig.get("Factor", 1.0))
        offset_val = float(sig.get("Offset", 0.0))
        start_pos = sig.get("StartPos", 0)
        sig_len = sig.get("Length", 8)
        byte_order = sig.get("ByteOrder", 0)
        value_type = sig.get("ValueType", "Unsigned")
        if factor != 1.0 or offset_val != 0.0:
            raw_value = int(round((init_val - offset_val) / factor))
        else:
            raw_value = int(init_val)
        max_val = (1 << sig_len) - 1
        if value_type == "Signed":
            half = 1 << (sig_len - 1)
            raw_value = max(-half, min(half - 1, raw_value)) & max_val
        elif value_type == "Bool":
            raw_value = 1 if init_val else 0
        else:
            raw_value = max(0, min(max_val, raw_value))
        if byte_order == 0:
            _pack_intel(data, start_pos, sig_len, raw_value)
        else:
            _pack_motorola(data, start_pos, sig_len, raw_value)
    return bytes(data)

# ── PDU cyclic sender ─────────────────────────────────────────────────────────
_CYCLIC_STOP: dict[int, threading.Event] = {}
_CYCLIC_STATS: dict[int, dict] = {}
_CYCLIC_LOCK = threading.Lock()
_SIGNAL_VALUES: dict[tuple[int, int], float] = {}
_PAYLOAD_OVERRIDES: dict[int, bytes] = {}

def _normalize_bus(bus_name: str) -> str:
    """Normalize a PDU DB bus name to a Linux interface name."""
    n = bus_name.lower().replace(" ", "_")
    if len(n) > 15:
        n = n[:15]
    return n

def _cyclic_can_sender(db_id: int, gateway: str, msg: dict, cycle_ms: int) -> None:
    bus = _normalize_bus(msg.get("Bus", ""))
    identifier = msg.get("Identifier", 0)
    is_fd = msg.get("BusType", "") == "CANFD"
    brs = msg.get("BRS", False)
    frame_type = msg.get("FrameType", 0)
    if frame_type == 1:
        identifier |= 0x80000000
    flags = _CANFD_FDF if is_fd else 0
    if brs and is_fd:
        flags |= _CANFD_BRS
    cycle_s = cycle_ms / 1000.0
    client = None
    deadline = _time_module.monotonic() + cycle_s
    stop_event = _CYCLIC_STOP.get(db_id, threading.Event())
    while not stop_event.is_set():
        try:
            data = _build_frame_data(msg, db_id=db_id)
            dlc = len(data)
            if client is None:
                client = BoAtClient(gateway)
            frame = can_pb2.CanFrame(
                can_id=identifier, dlc=dlc, data=data,
                iface=bus, flags=flags,
            )
            client.can.SendCanFrame(can_pb2.SendCanFrameRequest(frame=frame))
            with _CYCLIC_LOCK:
                s = _CYCLIC_STATS.get(db_id, {"count": 0})
                s["count"] = s.get("count", 0) + 1
                _CYCLIC_STATS[db_id] = s
        except Exception:
            with _CYCLIC_LOCK:
                s = _CYCLIC_STATS.get(db_id, {"count": 0})
                s["count"] = s.get("count", 0) + 1
                _CYCLIC_STATS[db_id] = s
            try:
                if client: client.close()
            except Exception: pass
            client = None
            _time_module.sleep(0.5)
            deadline = _time_module.monotonic() + cycle_s
            stop_event = _CYCLIC_STOP.get(db_id, threading.Event())
            continue
        now = _time_module.monotonic()
        if deadline > now:
            _time_module.sleep(deadline - now)
        deadline += cycle_s
    if client:
        try: client.close()
        except Exception: pass

def _cyclic_eth_sender(db_id: int, gateway: str, msg: dict, cycle_ms: int) -> None:
    bus = _normalize_bus(msg.get("Bus", ""))
    ethertype = msg.get("EtherType", 0x0800)
    src_mac_raw = msg.get("SrcMAC", "")
    dst_mac_raw = msg.get("DstMAC", "")
    src_mac = _parse_hex_bytes(src_mac_raw) if src_mac_raw else b""
    dst_mac = _parse_hex_bytes(dst_mac_raw) if dst_mac_raw else b""
    cycle_s = cycle_ms / 1000.0
    client = None
    deadline = _time_module.monotonic() + cycle_s
    stop_event = _CYCLIC_STOP.get(db_id, threading.Event())
    while not stop_event.is_set():
        try:
            data = _build_frame_data(msg, db_id=db_id)
            if client is None:
                client = BoAtClient(gateway)
            frame = ethernet_pb2.EthernetFrame(
                ethertype=ethertype, payload=data,
                iface=bus, src_mac=src_mac, dst_mac=dst_mac,
            )
            client.ethernet.SendFrame(ethernet_pb2.SendEthernetFrameRequest(frame=frame))
            with _CYCLIC_LOCK:
                s = _CYCLIC_STATS.get(db_id, {"count": 0})
                s["count"] = s.get("count", 0) + 1
                _CYCLIC_STATS[db_id] = s
        except Exception:
            with _CYCLIC_LOCK:
                s = _CYCLIC_STATS.get(db_id, {"count": 0})
                s["count"] = s.get("count", 0) + 1
                _CYCLIC_STATS[db_id] = s
            try:
                if client: client.close()
            except Exception: pass
            client = None
            _time_module.sleep(0.5)
            deadline = _time_module.monotonic() + cycle_s
            stop_event = _CYCLIC_STOP.get(db_id, threading.Event())
            continue
        now = _time_module.monotonic()
        if deadline > now:
            _time_module.sleep(deadline - now)
        deadline += cycle_s
    if client:
        try: client.close()
        except Exception: pass

# ── Request models ─────────────────────────────────────────────────────────────
class CanSendReq(BaseModel):
    address:  str = _DEFAULT_GW
    can_id:   str           # hex or decimal, e.g. "0x123"
    data:     str = ""      # hex bytes, e.g. "DEADBEEF"
    dlc:      int = -1      # -1 = infer from data
    iface:    str = ""      # "" = all registered buses
    fd:       bool = False
    brs:      bool = False
class EthSendReq(BaseModel):
    address:    str = _DEFAULT_GW
    ethertype:  str = "0x0800"
    payload:    str = ""    # hex bytes
    iface:      str = ""    # "" = all
    src_mac:    str = ""    # hex, e.g. "020000000001" or "02:00:00:00:00:01"
    dst_mac:    str = ""
class BusPublishReq(BaseModel):
    address:    str = _DEFAULT_GW
    name:       str
    value_type: str         # "number" | "string" | "bool" | "bytes"
    value:      str
    publisher:  str = "commander"
# ── Helpers ────────────────────────────────────────────────────────────────────
def _parse_hex_bytes(s: str) -> bytes:
    return bytes.fromhex(s.replace(":", "").replace("-", "").replace(" ", ""))
def _parse_mac(s: str) -> bytes:
    if not s:
        return b""
    b = _parse_hex_bytes(s)
    if len(b) != 6:
        raise ValueError(f"MAC must be 6 bytes, got {len(b)}")
    return b
# ── FastAPI ────────────────────────────────────────────────────────────────────
app = FastAPI()
@app.get("/api/gateway")
def api_gateway():
    return {"address": _DEFAULT_GW}

@app.get("/api/gateway/health")
def api_gw_health():
    try:
        c = _client(_DEFAULT_GW)
        c.can.ListBuses(can_pb2.ListBusesRequest())
        return {"running": True}
    except Exception:
        return {"running": False}

# ── Discovery endpoints ────────────────────────────────────────────────────────
@app.get("/api/can/buses")
def api_can_buses(address: str = _DEFAULT_GW):
    try:
        resp = _client(address).can.ListBuses(can_pb2.ListBusesRequest())
        return {"ifaces": list(resp.ifaces)}
    except Exception:
        return {"ifaces": []}
@app.get("/api/eth/ifaces")
def api_eth_ifaces(address: str = _DEFAULT_GW):
    try:
        resp = _client(address).ethernet.ListInterfaces(
            ethernet_pb2.ListEthernetInterfacesRequest()
        )
        return {"ifaces": list(resp.ifaces)}
    except Exception:
        return {"ifaces": []}
@app.get("/api/bus/signals")
def api_bus_signals(address: str = _DEFAULT_GW):
    try:
        resp = _client(address).bus.ListSignals(bus_pb2.BusListSignalsRequest())
        return {"signals": list(resp.names)}
    except Exception:
        return {"signals": []}
# ── Send / Publish endpoints ───────────────────────────────────────────────────
@app.post("/api/can/send")
def api_can_send(req: CanSendReq):
    try:
        can_id = int(req.can_id, 0)
    except ValueError:
        return {"ok": False, "detail": f"Invalid CAN ID: {req.can_id!r}"}
    try:
        raw = _parse_hex_bytes(req.data) if req.data.strip() else b""
    except ValueError as e:
        return {"ok": False, "detail": f"Invalid data: {e}"}
    flags = 0
    if req.fd:
        flags |= _CANFD_FDF
    if req.brs and req.fd:
        flags |= _CANFD_BRS
    max_len  = 64 if req.fd else 8
    if len(raw) > max_len:
        return {"ok": False, "detail": f"Payload too long ({len(raw)} > {max_len} bytes)"}
    byte_count = req.dlc if req.dlc >= 0 else len(raw)
    byte_count = min(byte_count, max_len)
    if byte_count > len(raw):
        raw = raw + bytes(byte_count - len(raw))  # zero-pad
    else:
        raw = raw[:byte_count]                     # truncate
    frame = can_pb2.CanFrame(
        can_id=can_id, dlc=byte_count, data=raw,
        iface=req.iface, flags=flags,
    )
    try:
        resp = _client(req.address).can.SendCanFrame(
            can_pb2.SendCanFrameRequest(frame=frame)
        )
        return {"ok": bool(resp.accepted)}
    except grpc.RpcError as e:
        return {"ok": False, "detail": f"gRPC error: {e.details()}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}
@app.post("/api/eth/send")
def api_eth_send(req: EthSendReq):
    try:
        etype = int(req.ethertype, 0)
    except ValueError:
        return {"ok": False, "detail": f"Invalid ethertype: {req.ethertype!r}"}
    try:
        payload = _parse_hex_bytes(req.payload) if req.payload.strip() else b""
    except ValueError as e:
        return {"ok": False, "detail": f"Invalid payload: {e}"}
    try:
        src = _parse_mac(req.src_mac)
        dst = _parse_mac(req.dst_mac)
    except ValueError as e:
        return {"ok": False, "detail": str(e)}
    if len(payload) > 1500:
        return {"ok": False, "detail": f"Payload too long ({len(payload)} > 1500 bytes)"}
    frame = ethernet_pb2.EthernetFrame(
        ethertype=etype, payload=payload,
        iface=req.iface, src_mac=src, dst_mac=dst,
    )
    try:
        resp = _client(req.address).ethernet.SendFrame(
            ethernet_pb2.SendEthernetFrameRequest(frame=frame)
        )
        return {"ok": bool(resp.accepted)}
    except grpc.RpcError as e:
        return {"ok": False, "detail": f"gRPC error: {e.details()}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}
@app.post("/api/bus/publish")
def api_bus_publish(req: BusPublishReq):
    if not req.name.strip():
        return {"ok": False, "detail": "Signal name is required"}
    sig = bus_pb2.BusSignal(name=req.name.strip(), publisher=req.publisher)
    try:
        vt = req.value_type
        if vt == "number":
            sig.number_value = float(req.value)
        elif vt == "string":
            sig.string_value = req.value
        elif vt == "bool":
            sig.bool_value = req.value.strip().lower() in ("1", "true", "yes", "on")
        elif vt == "bytes":
            sig.bytes_value = _parse_hex_bytes(req.value)
        else:
            return {"ok": False, "detail": f"Unknown value type: {vt!r}"}
    except ValueError as e:
        return {"ok": False, "detail": str(e)}
    try:
        resp = _client(req.address).bus.Publish(bus_pb2.BusPublishRequest(signal=sig))
        return {"ok": bool(resp.accepted)}
    except grpc.RpcError as e:
        return {"ok": False, "detail": f"gRPC error: {e.details()}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}
# ── PDU DB API ─────────────────────────────────────────────────────────────────
@app.get("/api/pdu/list")
def api_pdu_list():
    if not _CONFIG_DIR.is_dir():
        return {"files": []}
    files = sorted(f.name for f in _CONFIG_DIR.iterdir()
                   if f.suffix == ".json" and f.name != "pdu_db.schema.json")
    return {"files": files}

@app.get("/api/pdu/last-import")
def api_pdu_last_import():
    if _LAST_IMPORT_FILE.is_file():
        fn = _LAST_IMPORT_FILE.read_text().strip()
        return {"filename": fn if fn else None}
    return {"filename": None}

@app.post("/api/pdu/import")
def api_pdu_import(filename: str):
    global _PDU_DB, _PDU_FILENAME
    path = _CONFIG_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    try:
        _PDU_DB = PduDatabase(str(path))
        _PDU_FILENAME = filename
        _LAST_IMPORT_FILE.write_text(filename)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse PDU DB: {e}")
    messages = []
    for m in _PDU_DB.messages():
        bt = m.get("BusType", "")
        if bt in ("CAN", "CANFD", "ETH", "ETH_PDU"):
            messages.append({
                "db_id": m["DbId"],
                "name": m["MessageName"],
                "bus": m.get("Bus", ""),
                "bus_type": bt,
                "send_type": m.get("SendType", ""),
                "cycle_time": m.get("CycleTime", 0),
                "identifier": m.get("Identifier"),
                "length": m.get("Length", 0),
                "frame_type": m.get("FrameType", 0),
                "signal_count": len(m.get("signals", [])),
            })
    return {"ok": True, "filename": filename, "messages": messages}

@app.get("/api/pdu/messages")
def api_pdu_messages():
    if _PDU_DB is None:
        raise HTTPException(status_code=404, detail="No PDU DB loaded")
    messages = []
    for m in _PDU_DB.messages():
        bt = m.get("BusType", "")
        if bt in ("CAN", "CANFD", "ETH", "ETH_PDU"):
            db_id = m["DbId"]
            messages.append({
                "db_id": db_id,
                "name": m["MessageName"],
                "bus": m.get("Bus", ""),
                "bus_type": bt,
                "send_type": m.get("SendType", ""),
                "cycle_time": m.get("CycleTime", 0),
                "identifier": m.get("Identifier"),
                "frame_type": m.get("FrameType", 0),
                "length": m.get("Length", 0),
                "signal_count": len(m.get("signals", [])),
                "active": db_id in _CYCLIC_STOP and not _CYCLIC_STOP[db_id].is_set(),
            })
    return {"messages": messages, "filename": _PDU_FILENAME}

class CyclicStartReq(BaseModel):
    gateway: str = _DEFAULT_GW
    db_ids: list[int] = []

@app.post("/api/pdu/cyclic/start")
def api_pdu_cyclic_start(req: CyclicStartReq):
    if _PDU_DB is None:
        raise HTTPException(status_code=400, detail="No PDU DB loaded")
    started = []
    for db_id in req.db_ids:
        msg = _PDU_DB.by_id(db_id)
        if msg is None:
            continue
        bt = msg.get("BusType", "")
        if bt not in ("CAN", "CANFD", "ETH", "ETH_PDU"):
            continue
        cycle_ms = msg.get("CycleTime", 100) or 100
        if cycle_ms <= 0:
            continue
        if db_id in _CYCLIC_STOP and not _CYCLIC_STOP[db_id].is_set():
            continue
        ev = threading.Event()
        _CYCLIC_STOP[db_id] = ev
        _CYCLIC_STATS[db_id] = {"count": 0}
        if bt in ("CAN", "CANFD"):
            t = threading.Thread(
                target=_cyclic_can_sender,
                args=(db_id, req.gateway, msg, cycle_ms),
                daemon=True, name=f"cyclic-can-{db_id}"
            )
        else:
            t = threading.Thread(
                target=_cyclic_eth_sender,
                args=(db_id, req.gateway, msg, cycle_ms),
                daemon=True, name=f"cyclic-eth-{db_id}"
            )
        t.start()
        started.append(db_id)
    return {"ok": True, "started": started}

class CyclicStopReq(BaseModel):
    db_ids: list[int] = []

@app.post("/api/pdu/cyclic/stop")
def api_pdu_cyclic_stop(req: CyclicStopReq):
    stopped = []
    for db_id in req.db_ids:
        ev = _CYCLIC_STOP.get(db_id)
        if ev and not ev.is_set():
            ev.set()
            stopped.append(db_id)
    return {"ok": True, "stopped": stopped}

@app.post("/api/pdu/cyclic/stop_all")
def api_pdu_cyclic_stop_all():
    stopped = []
    for db_id, ev in _CYCLIC_STOP.items():
        if not ev.is_set():
            ev.set()
            stopped.append(db_id)
    return {"ok": True, "stopped": stopped}

@app.get("/api/pdu/cyclic/status")
def api_pdu_cyclic_status():
    active = []
    for db_id, ev in _CYCLIC_STOP.items():
        if not ev.is_set():
            active.append({
                "db_id": db_id,
                "count": _CYCLIC_STATS.get(db_id, {}).get("count", 0),
            })
    return {"active": active}

class PduSendReq(BaseModel):
    gateway: str = _DEFAULT_GW
    db_id: int

@app.post("/api/pdu/send")
def api_pdu_send(req: PduSendReq):
    if _PDU_DB is None:
        raise HTTPException(400, "No PDU DB loaded")
    msg = _PDU_DB.by_id(req.db_id)
    if msg is None:
        raise HTTPException(404, f"Message {req.db_id} not found")
    bt = msg.get("BusType", "")
    if bt not in ("CAN", "CANFD", "ETH", "ETH_PDU"):
        raise HTTPException(400, f"Unsupported bus type: {bt}")
    bus = _normalize_bus(msg.get("Bus", ""))
    identifier = msg.get("Identifier", 0)
    is_fd = bt == "CANFD"
    frame_type = msg.get("FrameType", 0)
    if frame_type == 1:
        identifier |= 0x80000000
    flags = _CANFD_FDF if is_fd else 0
    if msg.get("BRS", False) and is_fd:
        flags |= _CANFD_BRS
    data = _build_frame_data(msg, db_id=req.db_id)
    dlc = len(data)
    try:
        if bt in ("CAN", "CANFD"):
            frame = can_pb2.CanFrame(can_id=identifier, dlc=dlc, data=data, iface=bus, flags=flags)
            _client(req.gateway).can.SendCanFrame(can_pb2.SendCanFrameRequest(frame=frame))
        else:
            ethertype = msg.get("EtherType", 0x0800)
            src_mac_raw = msg.get("SrcMAC", "")
            dst_mac_raw = msg.get("DstMAC", "")
            src_mac = _parse_hex_bytes(src_mac_raw) if src_mac_raw else b""
            dst_mac = _parse_hex_bytes(dst_mac_raw) if dst_mac_raw else b""
            frame = ethernet_pb2.EthernetFrame(ethertype=ethertype, payload=data, iface=bus, src_mac=src_mac, dst_mac=dst_mac)
            _client(req.gateway).ethernet.SendFrame(ethernet_pb2.SendEthernetFrameRequest(frame=frame))
        return {"ok": True, "db_id": req.db_id}
    except Exception as e:
        raise HTTPException(500, detail=str(e))

# ── PDU signal / payload editing ──────────────────────────────────────────────
@app.get("/api/pdu/messages/{db_id}")
def api_pdu_message_detail(db_id: int):
    if _PDU_DB is None:
        raise HTTPException(400, "No PDU DB loaded")
    msg = _PDU_DB.by_id(db_id)
    if msg is None:
        raise HTTPException(404, f"Message {db_id} not found")
    try:
        payload_hex = _build_frame_data(msg, db_id=db_id).hex(" ").upper()
    except Exception:
        payload_hex = ""
    signals = []
    for sig in msg.get("signals", []):
        sig_id = sig["id"]
        init_val = sig.get("InitValue", 0)
        override = _SIGNAL_VALUES.get((db_id, sig_id))
        current_raw = override if override is not None else init_val
        signals.append({
            "id": sig_id,
            "name": sig["SignalName"],
            "type": sig.get("ValueType", "Unsigned"),
            "length": sig.get("Length", 1),
            "unit": sig.get("Unit", ""),
            "factor": sig.get("Factor", 1.0),
            "offset": sig.get("Offset", 0.0),
            "min": sig.get("Min"),
            "max": sig.get("Max"),
            "init_value": init_val,
            "current_value": current_raw,
            "has_override": override is not None,
        })
    hp = db_id in _PAYLOAD_OVERRIDES
    return {
        "db_id": db_id,
        "name": msg["MessageName"],
        "bus_type": msg.get("BusType", ""),
        "length": msg.get("Length", 8),
        "payload_hex": payload_hex,
        "has_payload_override": hp,
        "payload_override_hex": _PAYLOAD_OVERRIDES[db_id].hex(" ").upper() if hp else "",
        "signals": signals,
    }

class SignalValueReq(BaseModel):
    value: float

@app.put("/api/pdu/messages/{db_id}/signals/{signal_id}")
def api_pdu_set_signal_value(db_id: int, signal_id: int, req: SignalValueReq):
    if _PDU_DB is None:
        raise HTTPException(400, "No PDU DB loaded")
    msg = _PDU_DB.by_id(db_id)
    if msg is None:
        raise HTTPException(404, f"Message {db_id} not found")
    if not any(s.get("id") == signal_id for s in msg.get("signals", [])):
        raise HTTPException(404, f"Signal {signal_id} not found in message {db_id}")
    _SIGNAL_VALUES[(db_id, signal_id)] = req.value
    return {"ok": True}

@app.delete("/api/pdu/messages/{db_id}/signals/{signal_id}")
def api_pdu_clear_signal_value(db_id: int, signal_id: int):
    _SIGNAL_VALUES.pop((db_id, signal_id), None)
    return {"ok": True}

class PayloadOverrideReq(BaseModel):
    payload: str

@app.put("/api/pdu/messages/{db_id}/payload")
def api_pdu_set_payload_override(db_id: int, req: PayloadOverrideReq):
    if _PDU_DB is None:
        raise HTTPException(400, "No PDU DB loaded")
    msg = _PDU_DB.by_id(db_id)
    if msg is None:
        raise HTTPException(404, f"Message {db_id} not found")
    try:
        raw = bytes.fromhex(req.payload.replace(" ", "").replace(":", "").replace("-", ""))
    except ValueError as e:
        raise HTTPException(400, f"Invalid hex: {e}")
    expected = msg.get("Length", len(raw))
    if len(raw) != expected:
        raise HTTPException(400, f"Payload length {len(raw)} B != expected {expected} B")
    _PAYLOAD_OVERRIDES[db_id] = raw
    return {"ok": True}

@app.delete("/api/pdu/messages/{db_id}/payload")
def api_pdu_clear_payload_override(db_id: int):
    _PAYLOAD_OVERRIDES.pop(db_id, None)
    return {"ok": True}

# ── HTML ───────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — Commander</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg:     #0d1117;
    --panel:  #161b22;
    --border: #30363d;
    --text:   #e6edf3;
    --muted:  #8b949e;
    --blue:   #58a6ff;
    --green:  #3fb950;
    --yellow: #d29922;
    --red:    #f85149;
    --purple: #d2a8ff;
    --orange: #ffa657;
    --mono:   "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  }
  html, body {
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 13px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  /* ── Header ── */
  header {
    height: 46px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    padding: 0 20px;
    gap: 14px;
    flex-shrink: 0;
  }
  .logo     { font-weight: 700; font-size: 15px; color: var(--blue); letter-spacing: .4px; }
  .subtitle { color: var(--muted); font-size: 12px; }
  header .spacer { flex: 1; }
  .gw-badge {
    font-size: 11px; padding: 2px 10px; border-radius: 12px;
    background: #1f3a1f; color: var(--green); border: 1px solid #2ea043;
    font-family: var(--mono);
  }
  .gw-input {
    background: var(--bg); border: 1px solid var(--border);
    color: var(--text); border-radius: 4px;
    padding: 3px 9px; font-size: 12px; font-family: var(--mono);
    width: 200px; outline: none;
  }
  .gw-input:focus { border-color: var(--blue); }
  .gw-status-badge {
    font-size: 11px; padding: 2px 10px; border-radius: 12px;
    font-family: var(--mono); transition: all .3s; flex-shrink: 0;
  }
  .gw-status-badge.on { background: #1f3a1f; color: var(--green); border: 1px solid #2ea043; }
  .gw-status-badge.off { background: #3d0b0b; color: var(--red); border: 1px solid #8b2020; }
  /* ── Three-column layout ── */
  .layout {
    display: flex;
    flex: 1;
    min-height: 0;
    overflow: hidden;
  }
  .col {
    flex: 1;
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--border);
    min-height: 0;
    min-width: 0;
  }
  .col:last-child { border-right: none; }
  /* ── Pane chrome (shared with dashboard) ── */
  .pane-header {
    height: 36px;
    padding: 0 14px;
    display: flex;
    align-items: center;
    gap: 8px;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
    flex-shrink: 0;
  }
  .pane-title {
    font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .8px; color: var(--muted);
  }
  .pane-spacer { flex: 1; }
  .btn-icon {
    background: none; border: none; color: var(--muted);
    cursor: pointer; font-size: 13px; padding: 2px 4px;
    border-radius: 3px; transition: color .15s;
  }
  .btn-icon:hover { color: var(--blue); }
  /* ── Form area ── */
  .form-area {
    flex-shrink: 0;
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    border-bottom: 1px solid var(--border);
  }
  .field-row {
    display: grid;
    grid-template-columns: 96px 1fr;
    align-items: center;
    gap: 8px;
  }
  .field-row.full-row {
    grid-template-columns: 1fr;
  }
  label {
    font-size: 11px; color: var(--muted);
    text-align: right; white-space: nowrap;
  }
  input[type="text"], input[type="number"], select, textarea {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
    font-family: var(--mono);
    width: 100%;
    outline: none;
    transition: border-color .15s;
  }
  input[type="text"]:focus,
  input[type="number"]:focus,
  select:focus { border-color: var(--blue); }
  input::placeholder { color: #3d4450; }
  select option { background: var(--panel); }
  /* checkboxes row */
  .check-row {
    display: grid;
    grid-template-columns: 96px 1fr;
    align-items: center;
    gap: 8px;
  }
  .checks {
    display: flex; gap: 18px; align-items: center;
  }
  .check-label {
    display: flex; align-items: center; gap: 5px;
    font-size: 12px; font-family: var(--mono); color: var(--text);
    cursor: pointer; user-select: none;
  }
  input[type="checkbox"] {
    accent-color: var(--blue);
    width: 13px; height: 13px; cursor: pointer;
  }
  input[type="checkbox"]:disabled + span { color: var(--muted); }
  /* send button */
  .send-row {
    display: grid;
    grid-template-columns: 96px 1fr;
    align-items: center;
    gap: 8px;
    margin-top: 2px;
  }
  .btn-send {
    padding: 6px 0;
    background: #1c3a5c; color: var(--blue);
    border: 1px solid var(--blue); border-radius: 5px;
    font-size: 12px; font-weight: 600;
    cursor: pointer; transition: background .15s;
    width: 100%;
  }
  .btn-send:hover:not(:disabled) { background: #24497a; }
  .btn-send:disabled { opacity: .4; cursor: not-allowed; }
  .btn-send.success { background: #1a3a1a; color: var(--green); border-color: var(--green); }
  .btn-send.error   { background: #3a1a1a; color: var(--red);   border-color: var(--red); }
  /* value type selector (bus signal) */
  .vtype-select {
    max-width: 110px;
  }
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
  /* ── Nav bar ── */
  #panel-nav {
    height: 32px; flex-shrink: 0;
    background: #0d1117;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center;
    padding: 0 16px; gap: 2px;
  }
  #panel-nav a {
    font-size: 11px; color: var(--muted);
    text-decoration: none;
    padding: 3px 11px; border-radius: 4px;
    transition: background .12s, color .12s;
  }
  #panel-nav a:hover { background: #21262d; color: var(--text); }
  #panel-nav a.active { color: var(--blue); background: rgba(88,166,255,.10); font-weight: 600; }
  /* ── PDU DB section ── */
  #pdu-import-bar {
    background: var(--panel); border-bottom: 1px solid var(--border);
    padding: 6px 14px; flex-shrink: 0;
    display: flex; align-items: center; gap: 8px;
  }
  #pdu-import-bar label { font-size: 10px; color: var(--muted); font-weight: 600;
                           text-transform: uppercase; letter-spacing: .6px; }
  #pdu-import-bar select, #pdu-import-bar input {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    border-radius: 4px; padding: 3px 7px; font-size: 11px;
    font-family: var(--mono); outline: none; }
  #pdu-import-bar select:focus, #pdu-import-bar input:focus { border-color: var(--blue); }
  #pdu-messages-section {
    background: var(--panel); border-bottom: 1px solid var(--border);
    padding: 4px 14px 8px; display: none;
    flex-direction: column; min-height: 0; flex: 1;
  }
  #pdu-messages-section.visible { display: flex; }
  #main-area {
    flex: 1; min-height: 0;
    display: flex; flex-direction: column;
  }
  #main-area .layout {
    flex-shrink: 0;
  }
  .pdu-toolbar {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 6px;
  }
  .pdu-toolbar label { font-size: 10px; color: var(--muted); font-weight: 600;
                        text-transform: uppercase; letter-spacing: .6px; }
  .pdu-toolbar select, .pdu-toolbar input {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    border-radius: 4px; padding: 3px 7px; font-size: 11px;
    font-family: var(--mono); outline: none; }
  .pdu-toolbar select:focus, .pdu-toolbar input:focus { border-color: var(--blue); }
  .pdu-toolbar .btn { flex-shrink: 0; }
  .btn-pdu {
    font-size: 11px; padding: 4px 14px; border-radius: 5px; cursor: pointer;
    border: 1px solid var(--border); font-weight: 600;
    transition: background .15s, border-color .15s; white-space: nowrap;
  }
  .btn-pdu:disabled { opacity: .4; cursor: not-allowed; }
  .btn-pdu-start {
    background: #1a3a1a; color: var(--green); border-color: #2ea043;
  }
  .btn-pdu-start:hover:not(:disabled) { background: #1f4a1f; border-color: var(--green); }
  .btn-pdu-stop {
    background: #3a1a1a; color: var(--red); border-color: #8b2020;
  }
  .btn-pdu-stop:hover:not(:disabled) { background: #4a2020; border-color: var(--red); }
  #pdu-message-scroll {
    flex: 1; min-height: 0; overflow-y: auto;
    border: 1px solid var(--border); border-radius: 4px;
    margin-bottom: 6px;
  }
  .pdu-msg-row {
    display: flex; align-items: center; gap: 8px;
    padding: 4px 8px; border-bottom: 1px solid rgba(48,54,61,.3);
    font-size: 11px; font-family: var(--mono);
    transition: background .1s;
  }
  .pdu-msg-row:hover { background: #1c2128; }
  .pdu-msg-row .pdu-chk { accent-color: var(--blue); width: 13px; height: 13px; cursor: pointer; flex-shrink: 0; }
  .pdu-msg-row .pdu-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--blue); }
  .pdu-msg-row .pdu-bus { color: var(--muted); width: 90px; text-align: left; }
  .pdu-msg-row .pdu-id { color: var(--green); width: 80px; text-align: left; }
  .pdu-msg-row .pdu-cycle { color: var(--yellow); width: 66px; text-align: right; }
  .pdu-msg-row .pdu-state { width: 40px; text-align: center; font-size: 14px; }
  .pdu-msg-row .pdu-send-btn { width: 44px; text-align: center; }
  .btn-pdu-xs {
    font-size: 10px; padding: 1px 6px; border-radius: 3px; cursor: pointer;
    border: 1px solid var(--border); background: transparent; color: var(--muted);
    transition: all .12s; font-family: inherit;
  }
  .btn-pdu-xs:hover { border-color: var(--blue); color: var(--blue); background: rgba(88,166,255,.08); }
  .btn-pdu-xs.sent { border-color: var(--green); color: var(--green); background: rgba(63,185,80,.10); }
  .btn-pdu-xs.fail { border-color: var(--red); color: var(--red); background: rgba(248,81,73,.10); }
  .pdu-msg-actions { display: flex; gap: 6px; align-items: center; }
  .pdu-select-all { font-size: 10px; color: var(--muted); cursor: pointer; user-select: none; }
  .pdu-select-all:hover { color: var(--blue); }
  #pdu-status { font-size: 10px; color: var(--muted); font-family: var(--mono); }
  .pdu-detail-row td { padding: 0; background: #0a0d12; }
  .pdu-detail-inner { padding: 8px 12px; display: flex; flex-direction: column; gap: 8px; }
  .pdu-detail-inner table { width: 100%; border-collapse: collapse; font-size: 11px; font-family: var(--mono); }
  .pdu-detail-inner td, .pdu-detail-inner th { padding: 3px 6px; text-align: left; white-space: nowrap; }
  .pdu-detail-inner th { color: var(--muted); font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: .6px; border-bottom: 1px solid var(--border); }
  .pdu-detail-inner td { color: var(--text); border-bottom: 1px solid rgba(48,54,61,.3); }
  .pdu-detail-inner .oval { color: var(--green); }
  .pdu-detail-inner .ovr { color: var(--yellow); }
  .pdu-signal-input { background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 3px; padding: 2px 5px; font-size: 11px; font-family: var(--mono); width: 80px; outline: none; }
  .pdu-signal-input:focus { border-color: var(--blue); }
  .pdu-payload-input { background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 3px; padding: 3px 6px; font-size: 11px; font-family: var(--mono); outline: none; flex: 1; max-width: 260px; }
  .pdu-payload-input:focus { border-color: var(--blue); }
  .pdu-detail-toggle { width: 20px; text-align: center; cursor: pointer; user-select: none; color: var(--muted); flex-shrink: 0; }
  .pdu-detail-toggle:hover { color: var(--blue); }
  .pdu-live-payload { font-size: 10px; color: var(--muted); font-family: var(--mono); }
</style>
</head>
<body>
<header>
  <span class="logo">⛵ BoAt</span>
  <span class="subtitle">Commander</span>
  <span class="gw-status-badge off" id="gw-status-badge">○ gateway</span>
  <span class="spacer"></span>
  <input class="gw-input" id="gw-addr" placeholder="localhost:50051"/>
  <span class="gw-badge" id="gw-badge">● :50051</span>
</header>
<nav id="panel-nav">
  <a class="nav-link" data-port="8086">Launcher</a>
  <a class="nav-link" data-port="8080">Dashboard</a>
  <a class="nav-link" data-port="8081">Nodes</a>
  <a class="nav-link" data-port="8082">Commander</a>
  <a class="nav-link" data-port="8083">Recorder</a>
</nav>
<div id="pdu-import-bar">
  <label>PDU DB</label>
  <select id="pdu-file-select">
    <option value="" disabled selected>Select DB file…</option>
  </select>
  <button class="btn-pdu btn-pdu-start" onclick="importPduDb()">Import</button>
  <span style="color:var(--green);font-size:10px;font-family:var(--mono);margin-left:4px" id="pdu-filename-label"></span>
</div>
<div id="main-area">
<div id="pdu-messages-section">
  <div class="pdu-toolbar">
    <label>Messages</label>
    <span id="pdu-filename-label2" style="color:var(--green);font-size:10px;font-family:var(--mono)"></span>
    <span style="flex:1"></span>
    <span class="pdu-select-all" onclick="selectAllPdu()">Select all</span>
    <span class="pdu-select-all" onclick="deselectAllPdu()">Deselect all</span>
  </div>
  <div id="pdu-message-scroll">
    <table style="width:100%;border-collapse:collapse">
      <tbody id="pdu-msg-list"></tbody>
    </table>
  </div>
  <div class="pdu-toolbar" style="margin-top:4px">
    <span id="pdu-status"></span>
    <span style="flex:1"></span>
    <button class="btn-pdu btn-pdu-start" id="btn-cyclic-start" onclick="startCyclicSend()">▶ Start Selected</button>
    <button class="btn-pdu btn-pdu-stop" id="btn-cyclic-stop" onclick="stopCyclicSend()" disabled>■ Stop Selected</button>
    <button class="btn-pdu btn-pdu-stop" id="btn-cyclic-stop-all" onclick="stopAllCyclic()" disabled>■ Stop All</button>
  </div>
</div>
<div class="layout">
  <!-- ══ CAN Send ══ -->
  <div class="col">
    <div class="pane-header">
      <span class="pane-title">CAN Send</span>
      <span class="pane-spacer"></span>
      <button class="btn-icon" title="Reload interfaces" onclick="loadCanBuses()">↺</button>
    </div>
    <div class="form-area">
      <div class="field-row">
        <label for="can-id">CAN ID</label>
        <input type="text" id="can-id" placeholder="0x123" spellcheck="false"/>
      </div>
      <div class="field-row">
        <label for="can-data">Data (hex)</label>
        <input type="text" id="can-data" placeholder="DE AD BE EF" spellcheck="false"/>
      </div>
      <div class="field-row">
        <label for="can-dlc">DLC override</label>
        <input type="number" id="can-dlc" placeholder="auto" min="0" max="64"/>
      </div>
      <div class="field-row">
        <label for="can-iface">Interface</label>
        <select id="can-iface">
          <option value="" disabled selected>— select —</option>
        </select>
      </div>
      <div class="check-row">
        <label></label>
        <div class="checks">
          <label class="check-label">
            <input type="checkbox" id="can-fd" onchange="onFdChange()"/>
            <span>CAN FD</span>
          </label>
          <label class="check-label">
            <input type="checkbox" id="can-brs" disabled/>
            <span>BRS</span>
          </label>
        </div>
      </div>
      <div class="send-row">
        <label></label>
        <button class="btn-send" id="btn-can-send" onclick="sendCan()">Send Frame</button>
      </div>
    </div>
  </div>
  <!-- ══ Ethernet Send ══ -->
  <div class="col">
    <div class="pane-header">
      <span class="pane-title">Ethernet Send</span>
      <span class="pane-spacer"></span>
      <button class="btn-icon" title="Reload interfaces" onclick="loadEthIfaces()">↺</button>
    </div>
    <div class="form-area">
      <div class="field-row">
        <label for="eth-iface">Interface</label>
        <select id="eth-iface">
          <option value="" disabled selected>— select —</option>
        </select>
      </div>
      <div class="field-row">
        <label for="eth-etype">EtherType</label>
        <input type="text" id="eth-etype" value="0x0800" spellcheck="false"/>
      </div>
      <div class="field-row">
        <label for="eth-payload">Payload (hex)</label>
        <input type="text" id="eth-payload" placeholder="11 AA 22 BB" spellcheck="false"/>
      </div>
      <div class="field-row">
        <label for="eth-src">Src MAC</label>
        <input type="text" id="eth-src" placeholder="02:00:00:00:00:01  (optional)" spellcheck="false"/>
      </div>
      <div class="field-row">
        <label for="eth-dst">Dst MAC</label>
        <input type="text" id="eth-dst" placeholder="FF:FF:FF:FF:FF:FF  (optional)" spellcheck="false"/>
      </div>
      <div class="send-row">
        <label></label>
        <button class="btn-send" id="btn-eth-send" onclick="sendEth()">Send Frame</button>
      </div>
    </div>
  </div>
  <!-- ══ Bus Signal Publish ══ -->
  <div class="col">
    <div class="pane-header">
      <span class="pane-title">Bus Signal</span>
      <span class="pane-spacer"></span>
      <button class="btn-icon" title="Reload signal names" onclick="loadSignals()">↺</button>
    </div>
    <div class="form-area">
      <div class="field-row">
        <label for="bus-name">Signal name</label>
        <input type="text" id="bus-name" placeholder="engine.rpm" list="signal-list" spellcheck="false"/>
        <datalist id="signal-list"></datalist>
      </div>
      <div class="field-row">
        <label for="bus-vtype">Type</label>
        <select id="bus-vtype" class="vtype-select" onchange="onVtypeChange()">
          <option value="number">number</option>
          <option value="string">string</option>
          <option value="bool">bool</option>
          <option value="bytes">bytes (hex)</option>
        </select>
      </div>
      <div class="field-row" id="bus-value-row">
        <label for="bus-value">Value</label>
        <input type="text" id="bus-value" placeholder="0.0" spellcheck="false"/>
      </div>
      <!-- bool shortcut row, hidden by default -->
      <div class="field-row" id="bus-bool-row" style="display:none">
        <label></label>
        <div class="checks">
          <label class="check-label">
            <input type="checkbox" id="bus-bool-check"/>
            <span id="bus-bool-label">false</span>
          </label>
        </div>
      </div>
      <div class="field-row">
        <label for="bus-pub">Publisher</label>
        <input type="text" id="bus-pub" value="commander" spellcheck="false"/>
      </div>
      <div class="send-row">
        <label></label>
        <button class="btn-send" id="btn-bus-pub" onclick="publishBus()">Publish</button>
      </div>
    </div>
  </div>
</div><!-- .layout -->
</div><!-- #main-area -->
<script>
// ── Gateway ───────────────────────────────────────────────────────────────────
let gateway = 'localhost:50051';
async function initGateway() {
  const r = await fetch('/api/gateway');
  const d = await r.json();
  gateway = d.address;
  document.getElementById('gw-addr').value  = gateway;
  document.getElementById('gw-badge').textContent = '● ' + gateway;
}
document.getElementById('gw-addr').addEventListener('change', e => {
  gateway = e.target.value.trim() || 'localhost:50051';
  document.getElementById('gw-badge').textContent = '● ' + gateway;
  loadCanBuses(); loadEthIfaces(); loadSignals();
});
// ── Interface / signal loaders ────────────────────────────────────────────────
async function loadCanBuses() {
  const sel = document.getElementById('can-iface');
  const prev = sel.value;
  sel.innerHTML = '<option value="" disabled selected>— select —</option>';
  try {
    const r = await fetch('/api/can/buses?address=' + encodeURIComponent(gateway));
    const d = await r.json();
    for (const iface of d.ifaces) {
      const opt = document.createElement('option');
      opt.value = iface; opt.textContent = iface;
      if (iface === prev) opt.selected = true;
      sel.appendChild(opt);
    }
  } catch {}
}
async function loadEthIfaces() {
  const sel = document.getElementById('eth-iface');
  const prev = sel.value;
  sel.innerHTML = '<option value="" disabled selected>— select —</option>';
  try {
    const r = await fetch('/api/eth/ifaces?address=' + encodeURIComponent(gateway));
    const d = await r.json();
    for (const iface of d.ifaces) {
      const opt = document.createElement('option');
      opt.value = iface; opt.textContent = iface;
      if (iface === prev) opt.selected = true;
      sel.appendChild(opt);
    }
  } catch {}
}
async function loadSignals() {
  const dl = document.getElementById('signal-list');
  dl.innerHTML = '';
  try {
    const r = await fetch('/api/bus/signals?address=' + encodeURIComponent(gateway));
    const d = await r.json();
    for (const name of d.signals) {
      const opt = document.createElement('option');
      opt.value = name;
      dl.appendChild(opt);
    }
  } catch {}
}
// ── CAN FD checkbox ───────────────────────────────────────────────────────────
function onFdChange() {
  const fd  = document.getElementById('can-fd').checked;
  const brs = document.getElementById('can-brs');
  brs.disabled = !fd;
  if (!fd) brs.checked = false;
}
// ── Bus value type switcher ───────────────────────────────────────────────────
function onVtypeChange() {
  const vt       = document.getElementById('bus-vtype').value;
  const valRow   = document.getElementById('bus-value-row');
  const boolRow  = document.getElementById('bus-bool-row');
  const valInput = document.getElementById('bus-value');
  if (vt === 'bool') {
    valRow.style.display  = 'none';
    boolRow.style.display = '';
  } else {
    valRow.style.display  = '';
    boolRow.style.display = 'none';
  }
  const placeholders = { number: '120.5', string: 'hello', bytes: 'DE AD BE EF' };
  valInput.placeholder = placeholders[vt] || '';
}
// update label next to bool checkbox
document.getElementById('bus-bool-check').addEventListener('change', e => {
  document.getElementById('bus-bool-label').textContent = e.target.checked ? 'true' : 'false';
});
// ── PDU DB ────────────────────────────────────────────────────────────────────
let cyclicActiveIds = new Set();
let pduOpenDetails = new Set();
let cyclicPollTimer = null;

async function fetchPduFiles() {
  try {
    const r = await fetch('/api/pdu/list');
    const d = await r.json();
    const sel = document.getElementById('pdu-file-select');
    if (!d.files.length) return;
    sel.innerHTML = '<option value="" disabled selected>Select DB file…</option>';
    for (const f of d.files) {
      const opt = document.createElement('option');
      opt.value = f; opt.textContent = f;
      sel.appendChild(opt);
    }
  } catch {}
}

async function importPduDb() {
  const sel = document.getElementById('pdu-file-select');
  const filename = sel.value;
  if (!filename) { alert('Select a PDU DB file first'); return; }
  try {
    const r = await fetch(`/api/pdu/import?filename=${encodeURIComponent(filename)}`, { method: 'POST' });
    const d = await r.json();
    if (!r.ok) { alert('Failed: ' + (d.detail || r.status)); return; }
    pduFilename = d.filename;
    pduMessages = d.messages;
    document.getElementById('pdu-messages-section').classList.add('visible');
    document.getElementById('pdu-filename-label').textContent = '✓ ' + d.filename;
    document.getElementById('pdu-filename-label2').textContent = d.filename;
    renderPduList();
    startCyclicPoll();
  } catch(e) { alert('Error: ' + e); }
}

function renderPduList() {
  const el = document.getElementById('pdu-msg-list');
  if (!pduMessages.length) return;
  el.innerHTML = pduMessages.map(m => {
    const idStr = m.identifier != null ? '0x' + m.identifier.toString(16).toUpperCase() : '—';
    const isActive = cyclicActiveIds.has(m.db_id);
    return `<tr class="pdu-msg-row">
      <td class="pdu-detail-toggle" id="toggle-${m.db_id}" onclick="togglePduDetail(${m.db_id})">▸</td>
      <td style="width:24px"><input type="checkbox" class="pdu-chk" id="pdu-chk-${m.db_id}"
             ${isActive ? 'checked' : ''} onchange="onPduChk()"/></td>
      <td class="pdu-state">${isActive ? '<span style="color:var(--green)">●</span>' : '<span style="color:var(--muted)">○</span>'}</td>
      <td class="pdu-name" title="${escHtml(m.name)} (DbId ${m.db_id})">${escHtml(m.name)}</td>
      <td class="pdu-bus">${escHtml(m.bus_type)} / ${escHtml(m.bus)}</td>
      <td class="pdu-id">${idStr}</td>
      <td class="pdu-cycle">${m.cycle_time > 0 ? m.cycle_time + ' ms' : m.send_type}</td>
      <td class="pdu-send-btn"><button class="btn-pdu-xs" id="sendonce-${m.db_id}" onclick="sendOnce(${m.db_id})">↻</button></td>
    </tr>
    <tr class="pdu-detail-row" id="detail-row-${m.db_id}" style="display:none">
      <td colspan="8"><div class="pdu-detail-inner" id="detail-inner-${m.db_id}"></div></td>
    </tr>`;
  }).join('');
  // Re-hide details that were open before
  pduOpenDetails.forEach(id => {
    const row = document.getElementById('detail-row-' + id);
    if (row) { row.style.display = 'table-row'; loadPduDetail(id); }
    const toggle = document.getElementById('toggle-' + id);
    if (toggle) toggle.textContent = '▾';
  });
  updatePduStatus();
}

function onPduChk() {}

function getSelectedIds() {
  const ids = [];
  document.querySelectorAll('.pdu-chk:checked').forEach(cb => {
    ids.push(parseInt(cb.id.replace('pdu-chk-', '')));
  });
  return ids;
}

function selectAllPdu() {
  document.querySelectorAll('.pdu-chk').forEach(cb => cb.checked = true);
}

function deselectAllPdu() {
  document.querySelectorAll('.pdu-chk').forEach(cb => cb.checked = false);
}

// ── Manual CAN send ────────────────────────────────────────────────────────────
function flashBtn(id, ok) {
  const btn = document.getElementById(id);
  btn.disabled = true;
  btn.classList.add(ok ? 'success' : 'error');
  btn.textContent = ok ? '\u2713 Accepted' : '\u2717 Failed';
  setTimeout(() => {
    btn.classList.remove('success', 'error');
    btn.disabled = false;
    btn.textContent = id.includes('bus') ? 'Publish' : 'Send Frame';
  }, 1200);
}

async function sendCan() {
  const canId = document.getElementById('can-id').value.trim();
  if (!canId) { document.getElementById('can-id').focus(); return; }
  const iface = document.getElementById('can-iface').value;
  if (!iface) { document.getElementById('can-iface').focus(); return; }
  const body = {
    address: gateway,
    can_id:  canId,
    data:    document.getElementById('can-data').value.trim(),
    dlc:     parseInt(document.getElementById('can-dlc').value) || -1,
    iface:   iface,
    fd:      document.getElementById('can-fd').checked,
    brs:     document.getElementById('can-brs').checked,
  };
  document.getElementById('btn-can-send').disabled = true;
  let result;
  try {
    const r = await fetch('/api/can/send', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    result = await r.json();
  } catch (e) {
    result = { ok: false, detail: String(e) };
  }
  flashBtn('btn-can-send', result.ok);
}

async function sendEth() {
  const etype = document.getElementById('eth-etype').value.trim() || '0x0800';
  const iface = document.getElementById('eth-iface').value;
  if (!iface) { document.getElementById('eth-iface').focus(); return; }
  const body  = {
    address:   gateway,
    ethertype: etype,
    payload:   document.getElementById('eth-payload').value.trim(),
    iface:     iface,
    src_mac:   document.getElementById('eth-src').value.trim(),
    dst_mac:   document.getElementById('eth-dst').value.trim(),
  };
  document.getElementById('btn-eth-send').disabled = true;
  let result;
  try {
    const r = await fetch('/api/eth/send', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    result = await r.json();
  } catch (e) {
    result = { ok: false, detail: String(e) };
  }
  flashBtn('btn-eth-send', result.ok);
}

async function publishBus() {
  const name = document.getElementById('bus-name').value.trim();
  if (!name) { document.getElementById('bus-name').focus(); return; }
  const vt = document.getElementById('bus-vtype').value;
  let value;
  if (vt === 'bool') {
    value = document.getElementById('bus-bool-check').checked ? 'true' : 'false';
  } else {
    value = document.getElementById('bus-value').value.trim();
  }
  const body = {
    address:    gateway,
    name:       name,
    value_type: vt,
    value:      value,
    publisher:  document.getElementById('bus-pub').value.trim() || 'commander',
  };
  document.getElementById('btn-bus-pub').disabled = true;
  let result;
  try {
    const r = await fetch('/api/bus/publish', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    result = await r.json();
  } catch (e) {
    result = { ok: false, detail: String(e) };
  }
  flashBtn('btn-bus-pub', result.ok);
}

// ── Keyboard shortcuts (Enter to send) ───────────────────────────────────────
document.querySelectorAll('.col').forEach((col, i) => {
  col.querySelectorAll('input').forEach(inp => {
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        [sendCan, sendEth, publishBus][i]?.();
      }
    });
  });
});

// ── Helpers ───────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── PDU cyclic send ───────────────────────────────────────────────────────────
async function startCyclicSend() {
  const ids = getSelectedIds();
  if (!ids.length) { alert('Select at least one message'); return; }
  document.getElementById('btn-cyclic-start').disabled = true;
  try {
    const r = await fetch('/api/pdu/cyclic/start', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ gateway, db_ids: ids }),
    });
    const d = await r.json();
    if (!r.ok) { alert('Failed: ' + (d.detail || r.status)); return; }
    for (const id of d.started) cyclicActiveIds.add(id);
    updatePduStatus();
    renderPduList();
    startCyclicPoll();
  } catch(e) { alert('Error: ' + e); }
  document.getElementById('btn-cyclic-start').disabled = false;
}

async function stopCyclicSend() {
  const ids = getSelectedIds();
  if (!ids.length) { alert('No messages selected'); return; }
  document.getElementById('btn-cyclic-stop').disabled = true;
  try {
    const r = await fetch('/api/pdu/cyclic/stop', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ db_ids: ids }),
    });
    const d = await r.json();
    if (!r.ok) { alert('Failed: ' + (d.detail || r.status)); return; }
    for (const id of d.stopped) cyclicActiveIds.delete(id);
    updatePduStatus();
    renderPduList();
  } catch(e) { alert('Error: ' + e); }
  document.getElementById('btn-cyclic-stop').disabled = false;
}

async function stopAllCyclic() {
  document.getElementById('btn-cyclic-stop-all').disabled = true;
  try {
    const r = await fetch('/api/pdu/cyclic/stop_all', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) { alert('Failed: ' + (d.detail || r.status)); return; }
    for (const id of d.stopped) cyclicActiveIds.delete(id);
    updatePduStatus();
    renderPduList();
  } catch(e) { alert('Error: ' + e); }
  document.getElementById('btn-cyclic-stop-all').disabled = false;
}

async function sendOnce(dbId) {
  const btn = document.getElementById('sendonce-' + dbId);
  try {
    const r = await fetch('/api/pdu/send', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ gateway, db_id: dbId }),
    });
    if (!r.ok) {
      btn.className = 'btn-pdu-xs fail';
      btn.textContent = '✗';
      setTimeout(() => { btn.className = 'btn-pdu-xs'; btn.textContent = '↻'; }, 800);
      return;
    }
    btn.className = 'btn-pdu-xs sent';
    btn.textContent = '✓';
    setTimeout(() => { btn.className = 'btn-pdu-xs'; btn.textContent = '↻'; }, 800);
  } catch(e) {
    btn.className = 'btn-pdu-xs fail';
    btn.textContent = '✗';
    setTimeout(() => { btn.className = 'btn-pdu-xs'; btn.textContent = '↻'; }, 800);
  }
}

async function pollCyclicStatus() {
  try {
    const r = await fetch('/api/pdu/cyclic/status');
    const d = await r.json();
    let changed = false;
    const newActive = new Set(d.active.map(a => a.db_id));
    if (newActive.size !== cyclicActiveIds.size) changed = true;
    else {
      for (const id of newActive) { if (!cyclicActiveIds.has(id)) { changed = true; break; } }
    }
    if (changed) {
      cyclicActiveIds = newActive;
      renderPduList();
    }
    // Refresh open detail rows
    pduOpenDetails.forEach(id => refreshPduDetail(id));
  } catch {}
}

function startCyclicPoll() {
  if (cyclicPollTimer) clearInterval(cyclicPollTimer);
  cyclicPollTimer = setInterval(pollCyclicStatus, 1500);
}

function updatePduStatus() {
  const el = document.getElementById('pdu-status');
  const startBtn = document.getElementById('btn-cyclic-start');
  const stopBtn = document.getElementById('btn-cyclic-stop');
  const stopAllBtn = document.getElementById('btn-cyclic-stop-all');
  const n = cyclicActiveIds.size;
  el.textContent = n > 0 ? `● ${n} message(s) sending cyclically` : '○ idle';
  startBtn.disabled = n > 0;
  stopBtn.disabled = n === 0;
  stopAllBtn.disabled = n === 0;
}

// ── PDU detail expand ─────────────────────────────────────────────────────────
function togglePduDetail(dbId) {
  const row = document.getElementById('detail-row-' + dbId);
  const toggle = document.getElementById('toggle-' + dbId);
  if (!row) return;
  if (row.style.display === '' || row.style.display === 'table-row') {
    row.style.display = 'none';
    if (toggle) toggle.textContent = '▸';
    pduOpenDetails.delete(dbId);
    return;
  }
  row.style.display = 'table-row';
  if (toggle) toggle.textContent = '▾';
  pduOpenDetails.add(dbId);
  const inner = document.getElementById('detail-inner-' + dbId);
  if (inner && !inner.dataset.loaded) {
    loadPduDetail(dbId);
  } else {
    refreshPduDetail(dbId);
  }
}

async function loadPduDetail(dbId) {
  const inner = document.getElementById('detail-inner-' + dbId);
  if (!inner) return;
  inner.innerHTML = 'Loading…';
  try {
    const r = await fetch('/api/pdu/messages/' + dbId);
    if (!r.ok) { inner.innerHTML = 'Failed to load'; return; }
    const d = await r.json();
    inner.dataset.loaded = '1';
    renderPduDetail(inner, d);
  } catch { inner.innerHTML = 'Error loading details'; }
}

function renderPduDetail(inner, d) {
  const sigRows = d.signals.map(s => {
    const hasOvr = s.has_override;
    const valCls = hasOvr ? 'oval' : '';
    const disabled = s.type === 'EnumValues' ? 'disabled' : '';
    return `<tr>
      <td style="color:var(--blue)">${escHtml(s.name)}</td>
      <td>${escHtml(s.type)}</td>
      <td style="color:var(--muted)">${s.length} bit</td>
      <td class="${valCls}" data-cv="${d.db_id}-${s.id}">${escHtml(String(s.current_value))}</td>
      <td style="color:var(--muted)">${escHtml(s.unit)}</td>
      <td><input class="pdu-signal-input" id="sig-in-${d.db_id}-${s.id}" type="number" step="any" value="${escHtml(String(s.current_value))}"/></td>
      <td>
        <button class="btn-pdu-xs" onclick="applySignalValue(${d.db_id},${s.id})">Apply</button>
        ${s.has_override ? `<button class="btn-pdu-xs" onclick="clearSignalValue(${d.db_id},${s.id})" style="color:var(--red)">✕</button>` : ''}
      </td>
    </tr>`;
  }).join('');
  inner.innerHTML = `
    <div style="display:flex;gap:12px;align-items:center;margin-bottom:2px">
      <span style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)">Signals</span>
      <span class="pdu-live-payload" id="live-payload-${d.db_id}">Live: ${escHtml(d.payload_hex)}</span>
    </div>
    <table><thead><tr><th>Signal</th><th>Type</th><th>Length</th><th>Current</th><th>Unit</th><th>Value</th><th></th></tr></thead><tbody>${sigRows}</tbody></table>
    <div style="display:flex;align-items:center;gap:8px;margin-top:6px;padding-top:4px;border-top:1px solid var(--border)">
      <span style="font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)">Raw Payload</span>
      <input class="pdu-payload-input" id="pay-in-${d.db_id}" placeholder="${d.has_payload_override ? escHtml(d.payload_override_hex) : escHtml(d.payload_hex)}"/>
      <button class="btn-pdu-xs" onclick="setPayloadOverride(${d.db_id})">Set</button>
      <button class="btn-pdu-xs" onclick="clearPayloadOverride(${d.db_id})" style="color:var(--red)">Clear</button>
    </div>`;
}

async function refreshPduDetail(dbId) {
  const inner = document.getElementById('detail-inner-' + dbId);
  if (!inner || !inner.dataset.loaded) return;
  try {
    const r = await fetch('/api/pdu/messages/' + dbId);
    if (!r.ok) return;
    const d = await r.json();
    const live = document.getElementById('live-payload-' + dbId);
    if (live) live.textContent = 'Live: ' + d.payload_hex;
    // Update current-value cells without touching input fields
    for (const s of d.signals) {
      const cell = document.querySelector(`#detail-inner-${dbId} td[data-cv="${dbId}-${s.id}"]`);
      if (cell) cell.textContent = s.current_value;
    }
  } catch {}
}

async function applySignalValue(dbId, sigId) {
  const inp = document.getElementById('sig-in-' + dbId + '-' + sigId);
  if (!inp) return;
  const val = parseFloat(inp.value);
  if (isNaN(val)) { alert('Invalid value'); return; }
  try {
    const r = await fetch('/api/pdu/messages/' + dbId + '/signals/' + sigId, {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ value: val }),
    });
    if (!r.ok) { const e = await r.json(); alert('Failed: ' + (e.detail || r.status)); return; }
    refreshPduDetail(dbId);
  } catch(e) { alert('Error: ' + e); }
}

async function clearSignalValue(dbId, sigId) {
  try {
    await fetch('/api/pdu/messages/' + dbId + '/signals/' + sigId, { method: 'DELETE' });
    refreshPduDetail(dbId);
  } catch {}
}

async function setPayloadOverride(dbId) {
  const inp = document.getElementById('pay-in-' + dbId);
  if (!inp || !inp.value.trim()) return;
  try {
    const r = await fetch('/api/pdu/messages/' + dbId + '/payload', {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ payload: inp.value.trim() }),
    });
    if (!r.ok) { const e = await r.json(); alert('Failed: ' + (e.detail || r.status)); return; }
    refreshPduDetail(dbId);
  } catch(e) { alert('Error: ' + e); }
}

async function clearPayloadOverride(dbId) {
  try {
    await fetch('/api/pdu/messages/' + dbId + '/payload', { method: 'DELETE' });
    refreshPduDetail(dbId);
  } catch {}
}

// ── Gateway health ─────────────────────────────────────────────────────────────
async function pollGatewayHealth() {
  try {
    const r = await fetch('/api/gateway/health');
    const d = await r.json();
    const badge = document.getElementById('gw-status-badge');
    if (badge) { badge.className = 'gw-status-badge ' + (d.running ? 'on' : 'off'); badge.textContent = d.running ? '● gateway' : '○ gateway'; }
  } catch {
    const badge = document.getElementById('gw-status-badge');
    if (badge) { badge.className = 'gw-status-badge off'; badge.textContent = '○ gateway'; }
  }
}
setInterval(pollGatewayHealth, 2000);
pollGatewayHealth();

// ── Nav bar ───────────────────────────────────────────────────────────────────
(function() {
  const h = window.location.hostname;
  const p = window.location.port;
  document.querySelectorAll('.nav-link').forEach(a => {
    a.href = 'http://' + h + ':' + a.dataset.port + '/';
    if (a.dataset.port === p) a.classList.add('active');
  });
})();
// ── Boot ──────────────────────────────────────────────────────────────────────
(async () => {
  await initGateway();
  loadCanBuses();
  loadEthIfaces();
  loadSignals();
  await fetchPduFiles();
  // Auto-import last used PDU DB so it persists across page reloads
  try {
    const r = await fetch('/api/pdu/last-import');
    const d = await r.json();
    if (d.filename) {
      const sel = document.getElementById('pdu-file-select');
      sel.value = d.filename;
      await importPduDb();
    }
  } catch {}
})();
</script>
</body>
</html>
"""
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)
if __name__ == "__main__":
    print(f"BoAt Commander → http://localhost:{_PORT}")
    print(f"Default gateway : {_DEFAULT_GW}")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
