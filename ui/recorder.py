"""
BoAt Platform — Trace Recorder
Manages recording sessions: subscribes to CAN / Ethernet / Bus streams and writes
ASC, BLF, or PCAP files plus an optional JSONL sidecar for BoAt bus signals.
Run:  python3 demo/recorder.py
Open: http://localhost:8083
"""
from __future__ import annotations
import json
import os
import struct
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))
import grpc
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from boat.client import BoAtClient
from boat.v1 import bus_pb2, can_pb2, ethernet_pb2
try:
    import can as python_can
    _HAS_PYTHON_CAN = True
except ImportError:
    _HAS_PYTHON_CAN = False
# ── Config ─────────────────────────────────────────────────────────────────────
_DEFAULT_GW      = os.environ.get("BOAT_GATEWAY", "localhost:50051")
_DEFAULT_OUT_DIR = Path(os.environ.get("BOAT_TRACES_DIR", "traces"))
_PORT            = int(os.environ.get("BOAT_REC_PORT", "8083"))
_DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
# ── PCAP constants ─────────────────────────────────────────────────────────────
_PCAP_MAGIC   = 0xA1B2C3D4
_PCAP_SNAPLEN = 65535
_DLT_CAN_SK   = 227   # DLT_CAN_SOCKETCAN — Wireshark decodes classic + FD by length
_DLT_ETH      = 1     # DLT_EN10MB
_CAN_EFF_FLAG = 0x80000000
# ── File writers ───────────────────────────────────────────────────────────────
class _PcapBase:
    def __init__(self, path: Path, dlt: int) -> None:
        self._f    = open(path, "wb")
        self._lock = threading.Lock()
        self._f.write(struct.pack("<IHHiIII",
            _PCAP_MAGIC, 2, 4, 0, 0, _PCAP_SNAPLEN, dlt))
        self._f.flush()
    def _packet(self, ts: float, data: bytes) -> None:
        sec  = int(ts)
        usec = int((ts - sec) * 1_000_000)
        hdr  = struct.pack("<IIII", sec, usec, len(data), len(data))
        with self._lock:
            self._f.write(hdr + data)
            self._f.flush()
    def close(self) -> None:
        with self._lock:
            try: self._f.close()
            except Exception: pass
class PcapCanWriter(_PcapBase):
    def __init__(self, path: Path) -> None:
        super().__init__(path, _DLT_CAN_SK)
    def write(self, ts: float, can_id: int, dlc: int, data: bytes, flags: int) -> None:
        # DLT_CAN_SOCKETCAN requires can_id in network byte order (big-endian).
        # Using little-endian causes Wireshark to display wrong IDs (e.g. 0x103 → 0x000).
        is_fd = bool(flags & 0x04)
        if is_fd:
            # canfd_frame: 4 id (BE) + 1 len + 1 flags + 2 pad + 64 data = 72 B
            raw = struct.pack(">IBBBB", can_id, dlc, flags, 0, 0) + \
                  (data[:dlc] + b"\x00" * 64)[:64]
        else:
            # can_frame: 4 id (BE) + 1 dlc + 3 pad + 8 data = 16 B
            raw = struct.pack(">IBBBB", can_id, dlc, 0, 0, 0) + \
                  (data[:dlc] + b"\x00" * 8)[:8]
        self._packet(ts, raw)
class PcapEthWriter(_PcapBase):
    def __init__(self, path: Path) -> None:
        super().__init__(path, _DLT_ETH)
    def write(self, ts: float, dst_mac: bytes, src_mac: bytes,
              ethertype: int, payload: bytes) -> None:
        dst = dst_mac if len(dst_mac) == 6 else b"\xff" * 6
        src = src_mac if len(src_mac) == 6 else b"\x00" * 6
        self._packet(ts, dst + src + struct.pack(">H", ethertype) + payload)
class JsonlWriter:
    def __init__(self, path: Path) -> None:
        self._f    = open(path, "w", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
    def write(self, ts: float, name: str, vtype: str, value: Any) -> None:
        with self._lock:
            self._f.write(json.dumps(
                {"ts": ts, "name": name, "type": vtype, "value": value}
            ) + "\n")
    def close(self) -> None:
        with self._lock:
            try: self._f.close()
            except Exception: pass
# ── Session ────────────────────────────────────────────────────────────────────
@dataclass
class Session:
    session_id:      str
    name:            str
    fmt:             str          # "asc" | "blf" | "pcap"
    buses:           List[str]    # CAN interface names to record
    eth_ifaces:      List[str]    # Ethernet interface names (pcap only)
    include_signals: bool
    gateway:         str
    output_dir:      Path
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stopped_at: Optional[datetime] = None
    can_count: int = 0
    eth_count: int = 0
    sig_count: int = 0
    # internals
    _stop:        threading.Event   = field(default_factory=threading.Event,  repr=False)
    _threads:     List[threading.Thread] = field(default_factory=list,        repr=False)
    _can_writer:  Any               = field(default=None,                     repr=False)
    _eth_writer:  Any               = field(default=None,                     repr=False)
    _sig_writer:  Any               = field(default=None,                     repr=False)
    _can_stream:  Any               = field(default=None,                     repr=False)
    _eth_stream:  Any               = field(default=None,                     repr=False)
    _bus_stream:  Any               = field(default=None,                     repr=False)
    _files:       List[Path]        = field(default_factory=list,             repr=False)
    _channel_map: Dict[str, int]    = field(default_factory=dict,             repr=False)
    @property
    def running(self) -> bool:
        return self.stopped_at is None
    def to_dict(self) -> dict:
        files = []
        for p in self._files:
            try:    size = p.stat().st_size
            except: size = 0
            files.append({"name": p.name, "size": size})
        return {
            "session_id":      self.session_id,
            "name":            self.name,
            "format":          self.fmt,
            "buses":           self.buses,
            "eth_ifaces":      self.eth_ifaces,
            "include_signals": self.include_signals,
            "gateway":         self.gateway,
            "started_at":      self.started_at.isoformat(),
            "stopped_at":      self.stopped_at.isoformat() if self.stopped_at else None,
            "running":         self.running,
            "can_count":       self.can_count,
            "eth_count":       self.eth_count,
            "sig_count":       self.sig_count,
            "files":           files,
        }
# ── Session lifecycle ──────────────────────────────────────────────────────────
def _open_writers(session: Session) -> None:
    """Create output files and open writers based on format."""
    base = session.output_dir / session.session_id
    if session.name:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in session.name)
        base = session.output_dir / f"{session.session_id}_{safe}"
    if session.fmt == "asc":
        if not _HAS_PYTHON_CAN:
            raise RuntimeError("python-can is required for ASC format")
        p = Path(str(base) + ".asc")
        session._can_writer = python_can.ASCWriter(str(p))
        session._files.append(p)
    elif session.fmt == "blf":
        if not _HAS_PYTHON_CAN:
            raise RuntimeError("python-can is required for BLF format")
        p = Path(str(base) + ".blf")
        session._can_writer = python_can.BLFWriter(str(p))
        session._files.append(p)
    elif session.fmt == "pcap":
        if session.buses:
            p = Path(str(base) + "_can.pcap")
            session._can_writer = PcapCanWriter(p)
            session._files.append(p)
        if session.eth_ifaces:
            p = Path(str(base) + "_eth.pcap")
            session._eth_writer = PcapEthWriter(p)
            session._files.append(p)
    if session.include_signals:
        p = Path(str(base) + "_bus.jsonl")
        session._sig_writer = JsonlWriter(p)
        session._files.append(p)
    session._channel_map = {iface: idx for idx, iface in enumerate(session.buses)}
def _write_can(session: Session, frame: Any, ts: float) -> None:
    w = session._can_writer
    if w is None:
        return
    ch = session._channel_map.get(frame.iface, 0)
    if session.fmt in ("asc", "blf"):
        arb_id = frame.can_id & 0x1FFFFFFF
        is_ext = bool(frame.can_id & _CAN_EFF_FLAG)
        is_fd  = bool(frame.flags & 0x04)
        is_brs = bool(frame.flags & 0x01)
        msg = python_can.Message(
            timestamp        = ts,
            arbitration_id   = arb_id,
            data             = bytes(frame.data[:frame.dlc]),
            channel          = ch,
            is_extended_id   = is_ext,
            is_fd            = is_fd,
            bitrate_switch   = is_brs,
        )
        w(msg)
    else:
        w.write(ts, frame.can_id, frame.dlc, bytes(frame.data[:frame.dlc]), frame.flags)
def _run_can_sub(session: Session) -> None:
    client = stream = None
    try:
        client = BoAtClient(session.gateway)
        stream = client.can.SubscribeCanFrames(
            can_pb2.SubscribeCanFramesRequest(iface=""))
        session._can_stream = stream
        for frame in stream:
            if session._stop.is_set():
                break
            if session.buses and frame.iface not in session.buses:
                continue
            ts = frame.timestamp_ns / 1e9 if frame.timestamp_ns else time.time()
            _write_can(session, frame, ts)
            session.can_count += 1
    except Exception:
        pass
    finally:
        if stream:
            try: stream.cancel()
            except: pass
        if client:
            try: client.close()
            except: pass
def _run_eth_sub(session: Session) -> None:
    client = stream = None
    try:
        client = BoAtClient(session.gateway)
        stream = client.ethernet.SubscribeFrames(
            ethernet_pb2.SubscribeEthernetFramesRequest(iface="", ethertype=0))
        session._eth_stream = stream
        for frame in stream:
            if session._stop.is_set():
                break
            if session.eth_ifaces and frame.iface not in session.eth_ifaces:
                continue
            w = session._eth_writer
            if w is None:
                continue
            ts = frame.timestamp_ns / 1e9 if frame.timestamp_ns else time.time()
            w.write(ts, frame.dst_mac, frame.src_mac, frame.ethertype, frame.payload)
            session.eth_count += 1
    except Exception:
        pass
    finally:
        if stream:
            try: stream.cancel()
            except: pass
        if client:
            try: client.close()
            except: pass
def _run_bus_sub(session: Session) -> None:
    client = stream = None
    try:
        client = BoAtClient(session.gateway)
        stream = client.bus.Subscribe(bus_pb2.BusSubscribeRequest(names=[]))
        session._bus_stream = stream
        for sig in stream:
            if session._stop.is_set():
                break
            w = session._sig_writer
            if w is None:
                continue
            ts = time.time()
            kind = sig.WhichOneof("value")
            if kind == "number_value": vtype, val = "number", sig.number_value
            elif kind == "string_value": vtype, val = "string", sig.string_value
            elif kind == "bool_value":   vtype, val = "bool",   sig.bool_value
            elif kind == "bytes_value":  vtype, val = "bytes",  sig.bytes_value.hex()
            else:                        vtype, val = "unknown", None
            w.write(ts, sig.name, vtype, val)
            session.sig_count += 1
    except Exception:
        pass
    finally:
        if stream:
            try: stream.cancel()
            except: pass
        if client:
            try: client.close()
            except: pass
def _close_writer(w: Any) -> None:
    if w is None:
        return
    for method in ("stop", "close"):
        fn = getattr(w, method, None)
        if fn:
            try: fn()
            except: pass
            break
def start_session(req_data: dict) -> Session:
    sid = "rec_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    out = Path(req_data.get("output_dir") or str(_DEFAULT_OUT_DIR))
    out.mkdir(parents=True, exist_ok=True)
    session = Session(
        session_id      = sid,
        name            = req_data.get("name", ""),
        fmt             = req_data.get("format", "asc").lower(),
        buses           = req_data.get("buses") or [],
        eth_ifaces      = req_data.get("eth_ifaces") or [],
        include_signals = bool(req_data.get("include_signals", True)),
        gateway         = req_data.get("gateway", _DEFAULT_GW),
        output_dir      = out,
    )
    _open_writers(session)
    if session.buses or (session.fmt != "pcap" and session._can_writer):
        t = threading.Thread(target=_run_can_sub, args=(session,), daemon=True)
        session._threads.append(t)
        t.start()
    if session.eth_ifaces and session.fmt == "pcap":
        t = threading.Thread(target=_run_eth_sub, args=(session,), daemon=True)
        session._threads.append(t)
        t.start()
    if session.include_signals:
        t = threading.Thread(target=_run_bus_sub, args=(session,), daemon=True)
        session._threads.append(t)
        t.start()
    return session
def stop_session(session: Session) -> None:
    if not session.running:
        return
    session._stop.set()
    for attr in ("_can_stream", "_eth_stream", "_bus_stream"):
        s = getattr(session, attr, None)
        if s:
            try: s.cancel()
            except: pass
    for t in session._threads:
        t.join(timeout=5.0)
    _close_writer(session._can_writer)
    _close_writer(session._eth_writer)
    _close_writer(session._sig_writer)
    session.stopped_at = datetime.now(timezone.utc)
# ── State ──────────────────────────────────────────────────────────────────────
_sessions: Dict[str, Session] = {}
_sessions_lock = threading.Lock()
# ── REST API ───────────────────────────────────────────────────────────────────
class StartRequest(BaseModel):
    gateway:         str       = _DEFAULT_GW
    name:            str       = ""
    format:          str       = "asc"        # "asc" | "blf" | "pcap"
    buses:           List[str] = []
    eth_ifaces:      List[str] = []
    include_signals: bool      = True
    output_dir:      str       = str(_DEFAULT_OUT_DIR)
app = FastAPI()
@app.get("/api/sessions")
def api_sessions():
    with _sessions_lock:
        return [s.to_dict() for s in reversed(list(_sessions.values()))]
@app.get("/api/sessions/{session_id}")
def api_session(session_id: str):
    s = _sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s.to_dict()
@app.post("/api/sessions")
def api_start(req: StartRequest):
    fmt = req.format.lower()
    if fmt not in ("asc", "blf", "pcap"):
        raise HTTPException(400, f"Unknown format: {fmt!r} (use asc, blf, or pcap)")
    if fmt in ("asc", "blf") and not _HAS_PYTHON_CAN:
        raise HTTPException(500, "python-can is not installed; cannot write ASC or BLF")
    try:
        session = start_session(req.model_dump())
    except Exception as e:
        raise HTTPException(500, str(e))
    with _sessions_lock:
        _sessions[session.session_id] = session
    return session.to_dict()
@app.delete("/api/sessions/{session_id}")
def api_stop(session_id: str):
    s = _sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    stop_session(s)
    return s.to_dict()
@app.delete("/api/sessions")
def api_stop_all():
    to_stop = []
    with _sessions_lock:
        for s in _sessions.values():
            if s.running:
                to_stop.append(s)
    stopped = []
    for s in to_stop:
        stop_session(s)
        stopped.append(s.session_id)
    return {"stopped": stopped}
@app.get("/api/files/{filename}")
def api_download(filename: str):
    # Security: only serve files from the traces directory, no path traversal
    p = (_DEFAULT_OUT_DIR / filename).resolve()
    if not str(p).startswith(str(_DEFAULT_OUT_DIR.resolve())):
        raise HTTPException(403, "Forbidden")
    if not p.exists():
        # Also search all session output dirs
        for s in _sessions.values():
            candidate = (s.output_dir / filename).resolve()
            try:
                candidate.relative_to(s.output_dir.resolve())
            except ValueError:
                continue
            if candidate.exists():
                p = candidate
                break
        else:
            raise HTTPException(404, "File not found")
    return FileResponse(str(p), filename=filename)
@app.get("/api/gateway")
def api_gateway():
    return {"address": _DEFAULT_GW}
# ── Panel HTML ─────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — Trace Recorder</title>
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
  html, body { height: 100%; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 13px; overflow: hidden; }
  header { height: 46px; background: var(--panel);
    border-bottom: 1px solid var(--border); display: flex; align-items: center;
    padding: 0 20px; gap: 14px; flex-shrink: 0; }
  .logo     { font-weight: 700; font-size: 15px; color: var(--blue); letter-spacing: .4px; }
  .subtitle { color: var(--muted); font-size: 12px; }
  header .spacer { flex: 1; }
  .gw-badge { font-size: 11px; padding: 2px 10px; border-radius: 12px;
    background: #1f3a1f; color: var(--green); border: 1px solid #2ea043;
    font-family: var(--mono); }
  .gw-input { background: var(--bg); border: 1px solid var(--border); color: var(--text);
    border-radius: 4px; padding: 3px 9px; font-size: 12px; font-family: var(--mono);
    width: 200px; outline: none; }
  .gw-input:focus { border-color: var(--blue); }
  .layout { display: flex; height: calc(100vh - 78px); overflow: hidden; }
  /* left 38% */
  .col-left { flex: 0 0 38%; display: flex; flex-direction: column;
    border-right: 1px solid var(--border); overflow: hidden; }
  /* right 62% */
  .col-right { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  .pane-header { height: 36px; padding: 0 14px; display: flex; align-items: center;
    gap: 8px; border-bottom: 1px solid var(--border); background: var(--panel);
    flex-shrink: 0; }
  .pane-title { font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: .8px; color: var(--muted); }
  .pane-spacer { flex: 1; }
  /* ── New session form ── */
  .form-scroll { overflow-y: auto; flex-shrink: 0; max-height: 50vh; }
  .form-area { padding: 14px; display: flex; flex-direction: column; gap: 9px; }
  .field-row { display: grid; grid-template-columns: 100px 1fr; align-items: center; gap: 8px; }
  label.fl { font-size: 11px; color: var(--muted); text-align: right; }
  input[type="text"], select {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    border-radius: 4px; padding: 4px 8px; font-size: 12px;
    font-family: var(--mono); width: 100%; outline: none; }
  input[type="text"]:focus, select:focus { border-color: var(--blue); }
  input::placeholder { color: #3d4450; }
  select option { background: var(--panel); }
  .radio-group { display: flex; gap: 14px; }
  .radio-label { display: flex; align-items: center; gap: 5px; font-size: 12px;
    font-family: var(--mono); cursor: pointer; }
  input[type="radio"] { accent-color: var(--blue); cursor: pointer; }
  .check-group { display: flex; flex-wrap: wrap; gap: 8px 14px; }
  .check-label { display: flex; align-items: center; gap: 5px; font-size: 12px;
    font-family: var(--mono); cursor: pointer; }
  input[type="checkbox"] { accent-color: var(--blue); width: 13px; height: 13px; cursor: pointer; }
  input:disabled + span { color: var(--muted); }
  .section-label { font-size: 10px; font-weight: 600; text-transform: uppercase;
    letter-spacing: .7px; color: var(--muted); margin-top: 4px; }
  .btn-start { padding: 7px 0; background: #1c3a5c; color: var(--blue);
    border: 1px solid var(--blue); border-radius: 5px; font-size: 12px; font-weight: 600;
    cursor: pointer; width: 100%; transition: background .15s; }
  .btn-start:hover:not(:disabled) { background: #24497a; }
  .btn-start:disabled { opacity: .4; cursor: not-allowed; }
  /* ── Active sessions ── */
  .active-area { flex: 1; overflow-y: auto; padding: 10px 14px;
    display: flex; flex-direction: column; gap: 8px; }
  .session-card { background: var(--panel); border: 1px solid var(--border);
    border-radius: 6px; padding: 10px 12px; }
  .session-card.running { border-color: var(--green); }
  .sc-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .running-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green);
    flex-shrink: 0; animation: pulse 2s infinite; }
  .stopped-dot { width: 7px; height: 7px; border-radius: 50%;
    background: var(--muted); flex-shrink: 0; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .sc-id { font-family: var(--mono); font-size: 11px; color: var(--blue); flex: 1; }
  .sc-name { font-size: 11px; color: var(--muted); }
  .sc-counts { display: flex; gap: 14px; font-family: var(--mono); font-size: 11px;
    color: var(--muted); margin-bottom: 6px; }
  .sc-count-val { color: var(--text); }
  .btn-stop { padding: 3px 12px; background: #3a1a1a; color: var(--red);
    border: 1px solid #8b2020; border-radius: 4px; font-size: 11px; font-weight: 600;
    cursor: pointer; transition: background .15s; }
  .btn-stop:hover { background: #4a2020; }
  .empty-active { font-size: 12px; color: var(--muted); padding: 20px 0;
    text-align: center; font-style: italic; }
  /* ── Sessions history (right) ── */
  .tbl-scroll { flex: 1; overflow-y: auto; min-height: 0; }
  table { width: 100%; border-collapse: collapse; }
  thead th { position: sticky; top: 0; background: #1c2128;
    border-bottom: 1px solid var(--border); padding: 5px 12px; text-align: left;
    font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: .6px;
    color: var(--muted); z-index: 1; }
  tbody tr { border-bottom: 1px solid rgba(48,54,61,.35); transition: background .1s; }
  tbody tr:hover { background: #1c2128; }
  tbody tr.files-row td { padding: 0; }
  td { padding: 5px 12px; font-family: var(--mono); font-size: 11px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 200px; }
  .td-status-run { color: var(--green); }
  .td-status-done { color: var(--muted); }
  .td-id { color: var(--blue); max-width: 160px; }
  .fmt-badge { display: inline-block; padding: 1px 7px; border-radius: 10px;
    font-size: 10px; font-weight: 600; text-transform: uppercase; }
  .fmt-asc  { background: #1c3a1c; color: var(--green); }
  .fmt-blf  { background: #1c1c3a; color: var(--blue); }
  .fmt-pcap { background: #2d1c3a; color: var(--purple); }
  .btn-expand { background: none; border: none; color: var(--blue); cursor: pointer;
    font-size: 11px; padding: 0; font-family: var(--mono); }
  .files-panel { display: none; background: #0a0d12; border-top: 1px solid var(--border);
    padding: 8px 14px; }
  .files-panel.open { display: block; }
  .file-link { display: flex; align-items: center; gap: 8px; padding: 3px 0;
    font-family: var(--mono); font-size: 11px; }
  .file-link a { color: var(--blue); text-decoration: none; }
  .file-link a:hover { text-decoration: underline; }
  .file-size { color: var(--muted); }
  .gw-status-badge {
    font-size: 11px; padding: 2px 10px; border-radius: 12px;
    font-family: var(--mono); transition: all .3s; flex-shrink: 0;
  }
  .gw-status-badge.on { background: #1f3a1f; color: var(--green); border: 1px solid #2ea043; }
  .gw-status-badge.off { background: #3d0b0b; color: var(--red); border: 1px solid #8b2020; }
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
</style>
</head>
<body>
<header>
  <span class="logo">⛵ BoAt</span>
  <span class="subtitle">Trace Recorder</span>
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
<div class="layout">
  <!-- ══ Left: form + active sessions ══ -->
  <div class="col-left">
    <div class="pane-header">
      <span class="pane-title">New Session</span>
      <span class="pane-spacer"></span>
      <button style="background:none;border:none;color:var(--muted);cursor:pointer;font-size:12px"
              title="Reload buses / interfaces" onclick="loadInterfaces()">↺</button>
    </div>
    <div class="form-scroll">
      <div class="form-area">
        <div class="field-row">
          <label class="fl" for="rec-name">Name</label>
          <input type="text" id="rec-name" placeholder="optional label"/>
        </div>
        <div class="field-row">
          <label class="fl">Format</label>
          <div class="radio-group">
            <label class="radio-label"><input type="radio" name="fmt" value="asc" checked/><span>ASC</span></label>
            <label class="radio-label"><input type="radio" name="fmt" value="blf"/><span>BLF</span></label>
            <label class="radio-label"><input type="radio" name="fmt" value="pcap"/><span>PCAP</span></label>
          </div>
        </div>
        <div class="field-row" style="align-items:flex-start;margin-top:2px">
          <label class="fl" style="padding-top:2px">CAN buses</label>
          <div class="check-group" id="can-checks">
            <span style="color:var(--muted);font-size:11px;font-style:italic">loading…</span>
          </div>
        </div>
        <div class="field-row" style="align-items:flex-start" id="eth-row">
          <label class="fl" style="padding-top:2px">Ethernet</label>
          <div class="check-group" id="eth-checks">
            <span style="color:var(--muted);font-size:11px;font-style:italic">loading…</span>
          </div>
        </div>
        <div class="field-row">
          <label class="fl">Bus signals</label>
          <label class="check-label">
            <input type="checkbox" id="inc-signals" checked/>
            <span>Include BoAt bus signals → .jsonl</span>
          </label>
        </div>
        <div class="field-row">
          <label class="fl" for="out-dir">Output dir</label>
          <input type="text" id="out-dir" value="traces"/>
        </div>
        <div class="field-row">
          <label class="fl"></label>
          <button class="btn-start" id="btn-start" onclick="startRecording()">▶ Start Recording</button>
        </div>
      </div>
    </div>
    <div class="pane-header" style="border-top: 1px solid var(--border)">
      <span class="pane-title">Active Sessions</span>
      <span class="pane-spacer"></span>
      <span id="active-count" style="font-size:11px;color:var(--muted);font-family:var(--mono)">0</span>
    </div>
    <div class="active-area" id="active-area">
      <div class="empty-active">No active recordings</div>
    </div>
  </div><!-- col-left -->
  <!-- ══ Right: session history ══ -->
  <div class="col-right">
    <div class="pane-header">
      <span class="pane-title">Session History</span>
      <span class="pane-spacer"></span>
      <span id="session-count" style="font-size:11px;color:var(--muted);font-family:var(--mono)">0</span>
    </div>
    <div class="tbl-scroll">
      <table>
        <thead>
          <tr>
            <th style="width:28px"></th>
            <th>Session ID</th>
            <th>Name</th>
            <th>Fmt</th>
            <th>Started</th>
            <th>Duration</th>
            <th>CAN</th>
            <th>ETH</th>
            <th>Signals</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody id="history-tbody"></tbody>
      </table>
    </div>
  </div>
</div><!-- .layout -->
<script>
let gateway = 'localhost:50051';
let canIfaces = [];
let ethIfaces = [];
// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  const r = await fetch('/api/gateway');
  const d = await r.json();
  gateway = d.address;
  document.getElementById('gw-addr').value  = gateway;
  document.getElementById('gw-badge').textContent = '● ' + gateway;
  await loadInterfaces();
  await refresh();
  setInterval(refresh, 1500);
  // Format radio → enable/disable eth checkboxes
  document.querySelectorAll('input[name="fmt"]').forEach(r =>
    r.addEventListener('change', updateEthState));
}
document.getElementById('gw-addr').addEventListener('change', e => {
  gateway = e.target.value.trim() || 'localhost:50051';
  document.getElementById('gw-badge').textContent = '● ' + gateway;
  loadInterfaces();
});
// ── Interface loader ──────────────────────────────────────────────────────────
async function loadInterfaces() {
  // CAN
  try {
    const r = await fetch(`/api/can-buses?gw=${encodeURIComponent(gateway)}`);
    canIfaces = (await r.json()).ifaces || [];
  } catch { canIfaces = []; }
  renderCheckGroup('can-checks', canIfaces, 'can_', true);
  // Ethernet
  try {
    const r = await fetch(`/api/eth-ifaces?gw=${encodeURIComponent(gateway)}`);
    ethIfaces = (await r.json()).ifaces || [];
  } catch { ethIfaces = []; }
  renderCheckGroup('eth-checks', ethIfaces, 'eth_', false);
  updateEthState();
}
function renderCheckGroup(containerId, ifaces, prefix, defaultChecked) {
  const el = document.getElementById(containerId);
  if (!ifaces.length) {
    el.innerHTML = '<span style="color:var(--muted);font-size:11px;font-style:italic">none registered</span>';
    return;
  }
  el.innerHTML = ifaces.map(iface =>
    `<label class="check-label">
      <input type="checkbox" id="${prefix}${iface}" value="${iface}" ${defaultChecked ? 'checked' : ''}/>
      <span>${iface}</span>
    </label>`
  ).join('');
}
function updateEthState() {
  const fmt = document.querySelector('input[name="fmt"]:checked')?.value;
  const isPcap = fmt === 'pcap';
  document.querySelectorAll('#eth-checks input[type="checkbox"]').forEach(cb => {
    cb.disabled = !isPcap;
  });
  document.getElementById('eth-row').style.opacity = isPcap ? '1' : '0.45';
}
// ── Start ─────────────────────────────────────────────────────────────────────
async function startRecording() {
  const fmt  = document.querySelector('input[name="fmt"]:checked').value;
  const buses = canIfaces.filter(i => {
    const cb = document.getElementById('can_' + i);
    return cb && cb.checked;
  });
  const eths = ethIfaces.filter(i => {
    const cb = document.getElementById('eth_' + i);
    return cb && cb.checked && !cb.disabled;
  });
  const body = {
    gateway:         gateway,
    name:            document.getElementById('rec-name').value.trim(),
    format:          fmt,
    buses:           buses,
    eth_ifaces:      eths,
    include_signals: document.getElementById('inc-signals').checked,
    output_dir:      document.getElementById('out-dir').value.trim() || 'traces',
  };
  document.getElementById('btn-start').disabled = true;
  try {
    const r = await fetch('/api/sessions', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok) { alert('Error: ' + (d.detail || r.status)); }
    else { await refresh(); }
  } catch(e) { alert('Error: ' + e); }
  document.getElementById('btn-start').disabled = false;
}
// ── Stop ──────────────────────────────────────────────────────────────────────
async function stopSession(sid) {
  await fetch(`/api/sessions/${sid}`, {method:'DELETE'});
  await refresh();
}
// ── Refresh ───────────────────────────────────────────────────────────────────
async function refresh() {
  let sessions;
  try {
    const r = await fetch('/api/sessions');
    sessions = await r.json();
  } catch { return; }
  renderActive(sessions.filter(s => s.running));
  renderHistory(sessions);
  document.getElementById('session-count').textContent = sessions.length;
}
// ── Active sessions ───────────────────────────────────────────────────────────
function renderActive(active) {
  const el = document.getElementById('active-area');
  document.getElementById('active-count').textContent = active.length;
  if (!active.length) {
    el.innerHTML = '<div class="empty-active">No active recordings</div>';
    return;
  }
  el.innerHTML = active.map(s => `
    <div class="session-card running">
      <div class="sc-header">
        <div class="running-dot"></div>
        <span class="sc-id">${s.session_id}</span>
        ${s.name ? `<span class="sc-name">${escHtml(s.name)}</span>` : ''}
        <button class="btn-stop" onclick="stopSession('${s.session_id}')">■ Stop</button>
      </div>
      <div class="sc-counts">
        <span>CAN <span class="sc-count-val">${s.can_count.toLocaleString()}</span></span>
        <span>ETH <span class="sc-count-val">${s.eth_count.toLocaleString()}</span></span>
        <span>Signals <span class="sc-count-val">${s.sig_count.toLocaleString()}</span></span>
        <span>Buses <span class="sc-count-val">${s.buses.join(', ') || '—'}</span></span>
      </div>
      <div style="font-size:10px;color:var(--muted);font-family:var(--mono)">
        ${s.output_dir || ''}
      </div>
    </div>
  `).join('');
}
// ── History table ─────────────────────────────────────────────────────────────
function renderHistory(sessions) {
  const tbody = document.getElementById('history-tbody');
  tbody.innerHTML = '';
  for (const s of sessions) {
    const tr = document.createElement('tr');
    const dur = duration(s.started_at, s.stopped_at);
    const ts  = new Date(s.started_at).toLocaleTimeString();
    tr.innerHTML = `
      <td><button class="btn-expand" onclick="toggleFiles('${s.session_id}')">▾</button></td>
      <td class="td-id" title="${s.session_id}">${s.session_id}</td>
      <td>${escHtml(s.name || '—')}</td>
      <td><span class="fmt-badge fmt-${s.format}">${s.format}</span></td>
      <td>${ts}</td>
      <td>${dur}</td>
      <td>${s.can_count.toLocaleString()}</td>
      <td>${s.eth_count.toLocaleString()}</td>
      <td>${s.sig_count.toLocaleString()}</td>
      <td class="${s.running ? 'td-status-run' : 'td-status-done'}">
        ${s.running ? '● rec' : '✓ done'}
      </td>`;
    tbody.appendChild(tr);
    // Files row
    const fr = document.createElement('tr');
    fr.className = 'files-row';
    fr.id = 'files-row-' + s.session_id;
    const td = document.createElement('td');
    td.colSpan = 10;
    const fp = document.createElement('div');
    fp.className = 'files-panel';
    fp.id = 'files-panel-' + s.session_id;
    if (s.files.length) {
      fp.innerHTML = s.files.map(f => `
        <div class="file-link">
          <a href="/api/files/${encodeURIComponent(f.name)}" download="${f.name}">${f.name}</a>
          <span class="file-size">${fmtSize(f.size)}</span>
        </div>`).join('');
    } else {
      fp.innerHTML = '<span style="color:var(--muted);font-size:11px">no files yet</span>';
    }
    td.appendChild(fp);
    fr.appendChild(td);
    tbody.appendChild(fr);
  }
}
function toggleFiles(sid) {
  const panel = document.getElementById('files-panel-' + sid);
  const btn   = document.querySelector(`#history-tbody .btn-expand[onclick="toggleFiles('${sid}')"]`);
  if (!panel) return;
  const open = panel.classList.toggle('open');
  if (btn) btn.textContent = open ? '▴' : '▾';
}
// ── Helpers ───────────────────────────────────────────────────────────────────
function duration(start, stop) {
  const s = new Date(start);
  const e = stop ? new Date(stop) : new Date();
  const sec = Math.round((e - s) / 1000);
  if (sec < 60)   return sec + 's';
  if (sec < 3600) return Math.floor(sec/60) + 'm ' + (sec%60) + 's';
  return Math.floor(sec/3600) + 'h ' + Math.floor((sec%3600)/60) + 'm';
}
function fmtSize(bytes) {
  if (!bytes) return '0 B';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes/1024).toFixed(1) + ' KB';
  return (bytes/1048576).toFixed(2) + ' MB';
}
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
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
init();
</script>
</body>
</html>
"""
# ── Discovery proxy endpoints (panel JS hits these to avoid CORS) ──────────────
@app.get("/api/gateway/health")
def api_gw_health():
    c = None
    try:
        c = BoAtClient(_DEFAULT_GW)
        c.can.ListBuses(can_pb2.ListBusesRequest())
        return {"running": True}
    except Exception:
        return {"running": False}
    finally:
        if c: c.close()

@app.get("/api/can-buses")
def api_can_buses(gw: str = _DEFAULT_GW):
    c = None
    try:
        c = BoAtClient(gw)
        resp = c.can.ListBuses(can_pb2.ListBusesRequest())
        return {"ifaces": list(resp.ifaces)}
    except Exception:
        return {"ifaces": []}
    finally:
        if c: c.close()

@app.get("/api/eth-ifaces")
def api_eth_ifaces(gw: str = _DEFAULT_GW):
    c = None
    try:
        c = BoAtClient(gw)
        resp = c.ethernet.ListInterfaces(ethernet_pb2.ListEthernetInterfacesRequest())
        return {"ifaces": list(resp.ifaces)}
    except Exception:
        return {"ifaces": []}
    finally:
        if c: c.close()
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)
# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"BoAt Trace Recorder  → http://localhost:{_PORT}")
    print(f"Default gateway      : {_DEFAULT_GW}")
    print(f"Traces directory     : {_DEFAULT_OUT_DIR.resolve()}")
    print(f"python-can           : {'4.x installed' if _HAS_PYTHON_CAN else 'NOT FOUND — ASC/BLF unavailable'}")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
