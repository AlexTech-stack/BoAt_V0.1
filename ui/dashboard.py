"""
BoAt Platform — CAN Trace, Bus Signal Log & Event Log
Run:  python3 demo/dashboard.py
Open: http://localhost:8080
"""
from __future__ import annotations
import sys
from pathlib import Path
import threading
import time
from datetime import datetime
from typing import List
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))
import grpc
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from boat.client import BoAtClient
from boat.v1 import bus_pb2, can_pb2, ethernet_pb2
# ── State ──────────────────────────────────────────────────────────────────────
MAX_CAN_FRAMES   = 2000
MAX_ETH_FRAMES   = 2000
MAX_BUS_SIGNALS  = 2000
MAX_LOG_ENTRIES  = 2000
class DashboardState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.can_frames: List[dict] = []
        self.eth_frames: List[dict] = []
        self.bus_signals: List[dict] = []
        self.event_log: List[dict] = []
        self._can_seq = 1
        self._eth_seq = 1
        self._bus_seq = 1
        self._log_seq = 1
        self._can_stream = None
        self._eth_stream = None
        self._bus_stream = None
        self._can_thread: threading.Thread | None = None
        self._eth_thread: threading.Thread | None = None
        self._bus_thread: threading.Thread | None = None
    def log(self, msg: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with self.lock:
            self.event_log.append({"ts": ts, "msg": msg, "level": level, "seq": self._log_seq})
            self._log_seq += 1
            if len(self.event_log) > MAX_LOG_ENTRIES:
                self.event_log.pop(0)
    def push_can_frame(self, frame) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        hex_data = frame.data.hex(":").upper() if frame.data else ""
        arb_id = frame.can_id & 0x1FFFFFFF
        with self.lock:
            self.can_frames.append({
                "seq": self._can_seq,
                "ts": ts,
                "iface": frame.iface or "?",
                "can_id": f"0x{arb_id:X}",
                "dlc": frame.dlc,
                "data": hex_data,
            })
            self._can_seq += 1
            if len(self.can_frames) > MAX_CAN_FRAMES:
                self.can_frames.pop(0)
    def push_eth_frame(self, frame) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        src = frame.src_mac.hex(":") if frame.src_mac else "—"
        dst = frame.dst_mac.hex(":") if frame.dst_mac else "—"
        with self.lock:
            self.eth_frames.append({
                "seq":      self._eth_seq,
                "ts":        ts,
                "iface":     frame.iface or "?",
                "ethertype": f"0x{frame.ethertype:04X}",
                "src_mac":   src,
                "dst_mac":   dst,
                "length":    len(frame.payload),
                "payload":   frame.payload[:16].hex(":").upper(),
            })
            self._eth_seq += 1
            if len(self.eth_frames) > MAX_ETH_FRAMES:
                self.eth_frames.pop(0)
    def push_bus_signal(self, sig) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        kind = sig.WhichOneof("value")
        if kind == "number_value":
            val_str = str(sig.number_value)
            val_type = "number"
        elif kind == "string_value":
            val_str = repr(sig.string_value)
            val_type = "string"
        elif kind == "bool_value":
            val_str = str(sig.bool_value)
            val_type = "bool"
        elif kind == "bytes_value":
            val_str = sig.bytes_value.hex(":")
            val_type = "bytes"
        else:
            val_str = "—"
            val_type = "unknown"
        with self.lock:
            self.bus_signals.append({
                "seq": self._bus_seq,
                "ts": ts,
                "name": sig.name,
                "publisher": sig.publisher or "—",
                "type": val_type,
                "value": val_str,
            })
            self._bus_seq += 1
            if len(self.bus_signals) > MAX_BUS_SIGNALS:
                self.bus_signals.pop(0)
    def start_can_subscribe(self, client: BoAtClient) -> None:
        if self._can_thread and self._can_thread.is_alive():
            return
        def _worker() -> None:
            self.log("CAN subscription started")
            while True:
                try:
                    stream = client.can.SubscribeCanFrames(
                        can_pb2.SubscribeCanFramesRequest(simulation_id="", iface="")
                    )
                    with self.lock:
                        self._can_stream = stream
                    for frame in stream:
                        self.push_can_frame(frame)
                except grpc.RpcError as e:
                    self.log(f"CAN stream lost: {e.code().name} — retrying in 2 s", "warn")
                    time.sleep(2)
                except Exception as e:
                    self.log(f"CAN error: {e}", "error")
                    time.sleep(2)
        self._can_thread = threading.Thread(target=_worker, daemon=True, name="can-sub")
        self._can_thread.start()
    def start_eth_subscribe(self, client: BoAtClient) -> None:
        if self._eth_thread and self._eth_thread.is_alive():
            return
        def _worker() -> None:
            self.log("Ethernet subscription started")
            while True:
                try:
                    stream = client.ethernet.SubscribeFrames(
                        ethernet_pb2.SubscribeEthernetFramesRequest(iface="", ethertype=0)
                    )
                    with self.lock:
                        self._eth_stream = stream
                    for frame in stream:
                        self.push_eth_frame(frame)
                except grpc.RpcError as e:
                    self.log(f"Ethernet stream lost: {e.code().name} — retrying in 2 s", "warn")
                    time.sleep(2)
                except Exception as e:
                    self.log(f"Ethernet error: {e}", "error")
                    time.sleep(2)
        self._eth_thread = threading.Thread(target=_worker, daemon=True, name="eth-sub")
        self._eth_thread.start()
    def start_bus_subscribe(self, client: BoAtClient) -> None:
        if self._bus_thread and self._bus_thread.is_alive():
            return
        def _worker() -> None:
            self.log("Bus signal subscription started")
            while True:
                try:
                    stream = client.bus.Subscribe(
                        bus_pb2.BusSubscribeRequest(names=[])  # empty = all
                    )
                    with self.lock:
                        self._bus_stream = stream
                    for sig in stream:
                        self.push_bus_signal(sig)
                except grpc.RpcError as e:
                    self.log(f"Bus stream lost: {e.code().name} — retrying in 2 s", "warn")
                    time.sleep(2)
                except Exception as e:
                    self.log(f"Bus error: {e}", "error")
                    time.sleep(2)
        self._bus_thread = threading.Thread(target=_worker, daemon=True, name="bus-sub")
        self._bus_thread.start()
dash = DashboardState()
client = BoAtClient("localhost:50051")
app = FastAPI()
# Start subscribing immediately at boot
dash.start_can_subscribe(client)
dash.start_eth_subscribe(client)
dash.start_bus_subscribe(client)
@app.get("/api/gateway/health")
def api_gw_health():
    try:
        client.can.ListBuses(can_pb2.ListBusesRequest())
        return {"running": True}
    except Exception:
        return {"running": False}
# ── REST API ──────────────────────────────────────────────────────────────────
@app.get("/api/can")
def api_can(since: int = 0):
    with dash.lock:
        frames = [f for f in dash.can_frames if f["seq"] > since]
        total = dash._can_seq
    return {"frames": frames, "total": total}
@app.post("/api/can/clear")
def api_can_clear():
    with dash.lock:
        dash.can_frames.clear()
    return {"ok": True}
@app.get("/api/eth")
def api_eth(since: int = 0):
    with dash.lock:
        frames = [f for f in dash.eth_frames if f["seq"] > since]
        total = dash._eth_seq
    return {"frames": frames, "total": total}
@app.post("/api/eth/clear")
def api_eth_clear():
    with dash.lock:
        dash.eth_frames.clear()
    return {"ok": True}
@app.get("/api/bus")
def api_bus(since: int = 0):
    with dash.lock:
        sigs = [f for f in dash.bus_signals if f["seq"] > since]
        total = dash._bus_seq
    return {"signals": sigs, "total": total}
@app.post("/api/bus/clear")
def api_bus_clear():
    with dash.lock:
        dash.bus_signals.clear()
    return {"ok": True}
@app.get("/api/log")
def api_log(since: int = 0):
    with dash.lock:
        entries = [f for f in dash.event_log if f["seq"] > since]
        total = dash._log_seq
    return {"entries": entries, "total": total}
# ── HTML ──────────────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — Live Monitor</title>
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
  }
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
  .logo { font-weight: 700; font-size: 15px; color: var(--blue); letter-spacing: .4px; }
  .subtitle { color: var(--muted); font-size: 12px; }
  header .spacer { flex: 1; }
  .gw-badge {
    font-size: 11px; padding: 2px 10px; border-radius: 12px;
    background: #1f3a1f; color: var(--green); border: 1px solid #2ea043;
  }
  /* ── Main layout: messages + event log ── */
  .layout {
    display: flex; flex-direction: column;
    height: calc(100vh - 78px);
    overflow: hidden;
  }
  .msg-pane { flex: 4; min-height: 0; }
  /* ── Shared pane chrome ── */
  .pane {
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .pane-header {
    height: 36px;
    padding: 0 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
    flex-shrink: 0;
  }
  .pane-title {
    font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .8px;
    color: var(--muted);
  }
  .live-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--green);
    animation: pulse 2s infinite;
    flex-shrink: 0;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }
  .pane-spacer { flex: 1; }
  .filter-input {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 4px;
    padding: 3px 7px;
    font-size: 11px;
    font-family: var(--mono);
    width: 130px;
    outline: none;
  }
  .filter-input:focus { border-color: var(--blue); }
  .frame-count { font-size: 11px; color: var(--muted); font-family: var(--mono); }
  .btn-small {
    font-size: 11px; padding: 2px 8px;
    background: var(--bg); border: 1px solid var(--border);
    color: var(--muted); border-radius: 4px; cursor: pointer;
  }
  .btn-small:hover { background: #21262d; color: var(--text); }
  /* scrollable table area */
  .tbl-scroll {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }
  table { width: 100%; border-collapse: collapse; }
  thead th {
    position: sticky; top: 0;
    background: #1c2128;
    border-bottom: 1px solid var(--border);
    padding: 5px 10px;
    text-align: left;
    font-size: 10px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .6px;
    color: var(--muted); z-index: 1;
  }
  tbody tr {
    border-bottom: 1px solid rgba(48,54,61,.35);
    transition: background .1s;
  }
  tbody tr:hover { background: #1c2128; }
  td {
    padding: 4px 10px;
    font-family: var(--mono);
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 220px;
  }
  /* ── Unified message columns ── */
  .td-ts      { color: var(--muted); width: 84px; }
  .td-source  { width: 80px; }
  .td-id-name { width: 90px; color: var(--blue); }
  .iface-pill {
    display: inline-block; padding: 1px 7px;
    border-radius: 10px; font-size: 10px; font-weight: 600;
  }
  .type-badge {
    display: inline-block; padding: 1px 6px; border-radius: 8px;
    font-size: 10px; font-weight: 600; letter-spacing: .3px;
  }
  .type-can { background: #1f3a1f; color: #3fb950; }
  .type-eth { background: #3a2a00; color: #ffa657; }
  .type-bus { background: #1c3a5c; color: #58a6ff; }
  .chk-type { font-size: 11px; display: flex; align-items: center; gap: 3px; color: var(--muted); cursor: pointer; }
  .chk-type input { accent-color: var(--blue); }
  @keyframes rowIn {
    from { background: rgba(88,166,255,.12); }
    to   { background: transparent; }
  }
  .row-new { animation: rowIn .8s ease-out forwards; }
  /* ── Event log pane ── */
  .log-pane { flex: 1; min-height: 0; background: var(--panel); }
  .log-scroll {
    flex: 1; overflow-y: auto;
    padding: 4px 12px 8px;
    font-family: var(--mono); font-size: 11px;
    min-height: 0;
  }
  .log-entry { display: flex; gap: 10px; padding: 2px 0; border-bottom: 1px solid rgba(48,54,61,.3); }
  .log-ts    { color: var(--muted); flex-shrink: 0; width: 80px; }
  .log-info  { color: var(--text); }
  .log-warn  { color: var(--yellow); }
  .log-error { color: var(--red); }
  ::-webkit-scrollbar { width: 4px; height: 4px; }
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
  .gw-status-badge {
    font-size: 11px; padding: 2px 10px; border-radius: 12px;
    font-family: var(--mono); transition: all .3s; flex-shrink: 0;
  }
  .gw-status-badge.on { background: #1f3a1f; color: var(--green); border: 1px solid #2ea043; }
  .gw-status-badge.off { background: #3d0b0b; color: var(--red); border: 1px solid #8b2020; }
</style>
</head>
<body>
<header>
  <span class="logo">⛵ BoAt</span>
  <span class="subtitle">Live Monitor</span>
  <span class="gw-status-badge off" id="gw-status-badge">○ gateway</span>
  <span class="spacer"></span>
  <span class="gw-badge">● gateway :50051</span>
</header>
<nav id="panel-nav">
  <a class="nav-link" data-port="8086">Launcher</a>
  <a class="nav-link" data-port="8080">Dashboard</a>
  <a class="nav-link" data-port="8081">Nodes</a>
  <a class="nav-link" data-port="8082">Commander</a>
  <a class="nav-link" data-port="8083">Recorder</a>
</nav>
<div class="layout">
  <!-- ══ Unified message trace ══ -->
  <div class="pane msg-pane">
    <div class="pane-header">
      <div class="live-dot"></div>
      <span class="pane-title">Messages</span>
      <span class="frame-count" id="msg-count">0</span>
      <div class="pane-spacer"></div>
      <input class="filter-input" id="filter-msg" placeholder="Search..." oninput="applyMsgFilter()" style="width:160px"/>
      <label class="chk-type"><input type="checkbox" id="chk-can" checked onchange="applyMsgFilter()"/> CAN</label>
      <label class="chk-type"><input type="checkbox" id="chk-eth" checked onchange="applyMsgFilter()"/> ETH</label>
      <label class="chk-type"><input type="checkbox" id="chk-bus" checked onchange="applyMsgFilter()"/> BUS</label>
      <button class="btn-small" onclick="clearAll()">Clear</button>
      <button class="btn-small" id="btn-msg-pause" onclick="togglePause()">⏸ Pause</button>
    </div>
    <div class="tbl-scroll" id="msg-scroll">
      <table>
        <thead>
          <tr>
            <th class="td-ts">Time</th>
            <th style="width:44px">Type</th>
            <th class="td-source">Source</th>
            <th class="td-id-name">Identifier</th>
            <th>Data</th>
          </tr>
        </thead>
        <tbody id="msg-tbody"></tbody>
      </table>
    </div>
  </div>
  <!-- ══ Event log ══ -->
  <div class="pane log-pane">
    <div class="pane-header">
      <div class="live-dot"></div>
      <span class="pane-title">Event Log</span>
      <div class="pane-spacer"></div>
      <button class="btn-small" onclick="clearLog()">Clear</button>
    </div>
    <div class="log-scroll" id="log-scroll"></div>
  </div>
</div><!-- end layout -->
<script>
// ── State ────────────────────────────────────────────────────────────────────
let allMessages = [];
let msgPaused   = false;
let msgFilter   = "";
let typeFilter  = { can: true, eth: true, bus: true };
let canSince = 0;
let ethSince = 0;
let busSince = 0;
let logSince = 0;
// ── iface colour palette ──────────────────────────────────────────────────────
const IFACE_COLORS = [
  { bg: "#1c3a5c", fg: "#58a6ff" },
  { bg: "#1f3a1f", fg: "#3fb950" },
  { bg: "#3a1c3a", fg: "#d2a8ff" },
  { bg: "#3a2a00", fg: "#ffa657" },
  { bg: "#3a1c1c", fg: "#f78166" },
];
const ifaceColorIdx = {};
let nextIfaceColor = 0;
function ifaceColor(iface) {
  if (!(iface in ifaceColorIdx)) ifaceColorIdx[iface] = nextIfaceColor++ % IFACE_COLORS.length;
  return IFACE_COLORS[ifaceColorIdx[iface]];
}
function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}
// ── CAN fetch ────────────────────────────────────────────────────────────────
let canFetching = false;
function fetchCan() {
  if (canFetching) return;
  canFetching = true;
  fetch('/api/can?since=' + canSince)
    .then(r => r.json())
    .then(d => {
      canFetching = false;
      if (!d.frames.length) return;
      canSince = d.total;
      const items = d.frames.map(f => ({ ts: f.ts, type: 'CAN', source: f.iface, id: f.can_id, data: f.data || '—' }));
      allMessages.push(...items);
      if (allMessages.length > 2000) allMessages.splice(0, allMessages.length - 2000);
      if (!msgPaused) renderMessages(items);
    }).catch(() => { canFetching = false; });
}
// ── Ethernet fetch ───────────────────────────────────────────────────────────
let ethFetching = false;
function fetchEth() {
  if (ethFetching) return;
  ethFetching = true;
  fetch('/api/eth?since=' + ethSince)
    .then(r => r.json())
    .then(d => {
      ethFetching = false;
      if (!d.frames.length) return;
      ethSince = d.total;
      const items = d.frames.map(f => ({ ts: f.ts, type: 'ETH', source: f.iface, id: f.ethertype, data: f.payload || '—' }));
      allMessages.push(...items);
      if (allMessages.length > 2000) allMessages.splice(0, allMessages.length - 2000);
      if (!msgPaused) renderMessages(items);
    }).catch(() => { ethFetching = false; });
}
// ── Bus signal fetch ─────────────────────────────────────────────────────────
let busFetching = false;
function fetchBus() {
  if (busFetching) return;
  busFetching = true;
  fetch('/api/bus?since=' + busSince)
    .then(r => r.json())
    .then(d => {
      busFetching = false;
      if (!d.signals.length) return;
      busSince = d.total;
      const items = d.signals.map(s => ({ ts: s.ts, type: 'BUS', source: s.publisher, id: s.name, data: s.value }));
      allMessages.push(...items);
      if (allMessages.length > 2000) allMessages.splice(0, allMessages.length - 2000);
      if (!msgPaused) renderMessages(items);
    }).catch(() => { busFetching = false; });
}
// ── Unified render ───────────────────────────────────────────────────────────
function matchesFilter(m) {
  if (!typeFilter[m.type.toLowerCase()]) return false;
  if (!msgFilter) return true;
  const q = msgFilter.toLowerCase();
  return m.source.toLowerCase().includes(q) ||
         m.id.toLowerCase().includes(q) ||
         m.data.toLowerCase().includes(q) ||
         m.type.toLowerCase().includes(q);
}
function renderMessages(items) {
  const tbody  = document.getElementById('msg-tbody');
  const scroll = document.getElementById('msg-scroll');
  const atBottom = scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop < 60;
  for (const m of items) {
    if (!matchesFilter(m)) continue;
    const c = ifaceColor(m.source);
    const tc = m.type.toLowerCase();
    const tr = document.createElement('tr');
    tr.className = 'row-new';
    tr.innerHTML =
      `<td class="td-ts">${m.ts}</td>` +
      `<td><span class="type-badge type-${tc}">${m.type}</span></td>` +
      `<td class="td-source"><span class="iface-pill" style="background:${c.bg};color:${c.fg}">${escHtml(m.source)}</span></td>` +
      `<td class="td-id-name">${escHtml(m.id)}</td>` +
      `<td>${escHtml(m.data)}</td>`;
    tbody.appendChild(tr);
  }
  while (tbody.rows.length > 2000) tbody.deleteRow(0);
  document.getElementById('msg-count').textContent = allMessages.length;
  if (atBottom) scroll.scrollTop = scroll.scrollHeight;
}
function applyMsgFilter() {
  msgFilter = document.getElementById('filter-msg').value;
  typeFilter.can = document.getElementById('chk-can').checked;
  typeFilter.eth = document.getElementById('chk-eth').checked;
  typeFilter.bus = document.getElementById('chk-bus').checked;
  document.getElementById('msg-tbody').innerHTML = '';
  renderMessages(allMessages);
}
function clearAll() {
  Promise.all([
    fetch('/api/can/clear', { method: 'POST' }),
    fetch('/api/eth/clear', { method: 'POST' }),
    fetch('/api/bus/clear', { method: 'POST' }),
  ]).then(() => {
    allMessages = []; canSince = 0; ethSince = 0; busSince = 0;
    document.getElementById('msg-tbody').innerHTML = '';
    document.getElementById('msg-count').textContent = '0';
  }).catch(() => {});
}
function togglePause() {
  msgPaused = !msgPaused;
  document.getElementById('btn-msg-pause').textContent = msgPaused ? '▶ Resume' : '⏸ Pause';
  if (!msgPaused) {
    document.getElementById('msg-tbody').innerHTML = '';
    renderMessages(allMessages);
  }
}
// ── Event log ─────────────────────────────────────────────────────────────────
let logFetching = false;
function fetchLog() {
  if (logFetching) return;
  logFetching = true;
  fetch('/api/log?since=' + logSince)
    .then(r => r.json())
    .then(d => {
      logFetching = false;
      if (!d.entries.length) return;
      logSince = d.total;
      appendLog(d.entries);
    }).catch(() => { logFetching = false; });
}
function appendLog(entries) {
  const el = document.getElementById('log-scroll');
  const atBottom = el.scrollHeight - el.clientHeight - el.scrollTop < 40;
  for (const e of entries) {
    const row = document.createElement('div');
    row.className = 'log-entry';
    row.innerHTML = `<span class="log-ts">${e.ts}</span><span class="log-${e.level}">${escHtml(e.msg)}</span>`;
    el.appendChild(row);
  }
  while (el.children.length > 2000) el.removeChild(el.firstChild);
  if (atBottom) el.scrollTop = el.scrollHeight;
}
function clearLog() {
  document.getElementById('log-scroll').innerHTML = '';
  logSince = 0;
}
// ── Nav bar ───────────────────────────────────────────────────────────────────
(function() {
  const h = window.location.hostname;
  const p = window.location.port;
  document.querySelectorAll('.nav-link').forEach(a => {
    a.href = 'http://' + h + ':' + a.dataset.port + '/';
    if (a.dataset.port === p) a.classList.add('active');
  });
})();
// ── boot ──────────────────────────────────────────────────────────────────────
setInterval(fetchCan, 150);
setInterval(fetchEth, 150);
setInterval(fetchBus, 150);
setInterval(fetchLog, 500);
fetchCan(); fetchEth(); fetchBus(); fetchLog();
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
</script>
</body>
</html>
"""
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)
if __name__ == "__main__":
    import os
    port = int(os.environ.get("BOAT_DASH_PORT", "8080"))
    print(f"BoAt Live Monitor → http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
