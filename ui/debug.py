"""
BoAt Platform — Gateway gRPC Traffic Inspector
Run:  python3 demo/debug.py
Open: http://localhost:8084
Subscribes to the gateway's DebugService.StreamEvents gRPC stream and shows
every internal RPC call in real time:
  - which method was called  (/boat.v1.CanService/SendCanFrame)
  - which client called it   (ip:port)
  - call type                (UNARY / SERVER_STREAM / …)
  - event lifecycle          CALL_START → MSG_RECV → MSG_SEND → CALL_END
  - message sizes in bytes
  - round-trip duration and gRPC status code
"""
from __future__ import annotations
import sys
from pathlib import Path
import threading
import time
from collections import deque
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))
import grpc
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from urllib.parse import unquote
from boat.client import BoAtClient
from boat.v1 import debug_pb2, can_pb2
# ── Config ─────────────────────────────────────────────────────────────────────
_GW   = "localhost:50051"
_PORT = 8084
_MAX  = 5000
# ── Ring buffer ────────────────────────────────────────────────────────────────
_lock    = threading.Lock()
_entries: deque = deque(maxlen=_MAX)
_seq     = 0
def _add(ev: debug_pb2.RpcEvent) -> None:
    global _seq
    ts_ns = ev.timestamp_ns
    if ts_ns:
        dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).astimezone()
        ts_str = dt.strftime("%H:%M:%S.%f")[:-3]
    else:
        ts_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    # Strip the package prefix: "/boat.v1.CanService/SendCanFrame" → "CanService/SendCanFrame"
    method = ev.method
    for prefix in ("/boat.v1.", "/boat."):
        if method.startswith(prefix):
            method = method[len(prefix):]
            break
    # Decode URL-encoded peer: "ipv6:%5B::1%5D:46320" → "[::1]:46320"
    peer = unquote(ev.peer)
    # Strip transport prefix (ipv4:, ipv6:) for brevity
    for pfx in ("ipv6:", "ipv4:"):
        if peer.startswith(pfx):
            peer = peer[len(pfx):]
            break
    entry = {
        "seq":          0,
        "ts":           ts_str,
        "event_type":   ev.event_type,
        "call_type":    ev.call_type,
        "method":       method,
        "peer":         peer,
        "summary":      ev.summary,
        "msg_bytes":    ev.msg_bytes,
        "duration_us":  ev.duration_us,
        "status_code":  ev.status_code,
        "status_msg":   ev.status_message,
    }
    with _lock:
        _seq += 1
        entry["seq"] = _seq
        _entries.append(entry)
# ── gRPC subscriber ────────────────────────────────────────────────────────────
def _run_subscriber() -> None:
    while True:
        try:
            client = BoAtClient(_GW)
            stream = client.debug.StreamEvents(
                debug_pb2.StreamRpcEventsRequest(method_filter="")
            )
            for ev in stream:
                _add(ev)
        except grpc.RpcError:
            time.sleep(2)
        except Exception:
            time.sleep(2)
# ── FastAPI ────────────────────────────────────────────────────────────────────
app = FastAPI()
@app.get("/api/gateway/health")
def api_gw_health():
    c = None
    try:
        c = BoAtClient(_GW)
        c.can.ListBuses(can_pb2.ListBusesRequest())
        return {"running": True}
    except Exception:
        return {"running": False}
    finally:
        if c: c.close()
        return {"running": False}

@app.get("/api/events")
def api_events(after: int = 0):
    with _lock:
        out = [e for e in _entries if e["seq"] > after]
    return JSONResponse({"events": out})
@app.get("/api/clear")
def api_clear():
    global _seq
    with _lock:
        _entries.clear()
        _seq = 0
    return {"ok": True}
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(_HTML)
# ── HTML ───────────────────────────────────────────────────────────────────────
_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt gRPC Debug</title>
<style>
  :root {
    --bg:      #0d1117;
    --surface: #161b22;
    --border:  #30363d;
    --text:    #e6edf3;
    --muted:   #8b949e;
    --blue:    #58a6ff;
    --green:   #3fb950;
    --yellow:  #d29922;
    --orange:  #f0883e;
    --red:     #f85149;
    --purple:  #bc8cff;
    --cyan:    #39d0d8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; background: var(--bg); color: var(--text);
               font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
               font-size: 12px; }
  header {
    height: 46px; background: var(--surface);
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; padding: 0 16px; gap: 12px;
  }
  .logo { font-size: 16px; font-weight: 700; }
  .subtitle { color: var(--muted); }
  .spacer { flex: 1; }
  .gw-badge { font-size: 11px; color: var(--green); }
  #panel-nav {
    height: 32px; background: var(--bg);
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; padding: 0 16px; gap: 2px;
  }
  #panel-nav a {
    font-size: 11px; color: var(--muted); text-decoration: none;
    padding: 3px 11px; border-radius: 4px; transition: background .12s, color .12s;
  }
  #panel-nav a:hover  { background: #21262d; color: var(--text); }
  #panel-nav a.active { color: var(--blue); background: rgba(88,166,255,.10); font-weight: 600; }
  /* ── Toolbar ── */
  .toolbar {
    height: 40px; display: flex; align-items: center; gap: 8px;
    padding: 0 12px; background: var(--surface);
    border-bottom: 1px solid var(--border); flex-shrink: 0;
  }
  .toolbar label { color: var(--muted); font-size: 11px; }
  .tog {
    padding: 2px 8px; border-radius: 4px; border: 1px solid var(--border);
    background: transparent; color: var(--muted); cursor: pointer;
    font-size: 11px; font-family: inherit; transition: all .12s;
  }
  .tog.data-on   { border-color: var(--blue);   color: var(--blue);   background: rgba(88,166,255,.10); }
  .tog.sub-on    { border-color: var(--cyan);   color: var(--cyan);   background: rgba(57,208,216,.10); }
  .tog.start-on  { border-color: var(--muted);  color: var(--muted);  background: rgba(139,148,158,.10); }
  .tog.end-on    { border-color: var(--purple); color: var(--purple); background: rgba(188,140,255,.10); }
  .sep  { width: 1px; height: 20px; background: var(--border); margin: 0 2px; }
  #kw   { flex: 1; max-width: 280px; padding: 3px 8px; border-radius: 4px;
          border: 1px solid var(--border); background: var(--bg);
          color: var(--text); font-family: inherit; font-size: 11px; outline: none; }
  #kw:focus { border-color: var(--blue); }
  .ctrl-btn {
    padding: 2px 9px; border-radius: 4px; border: 1px solid var(--border);
    background: transparent; color: var(--muted); cursor: pointer;
    font-size: 11px; font-family: inherit; transition: all .12s;
  }
  .ctrl-btn:hover { border-color: var(--red); color: var(--red); }
  .pause-btn { padding: 2px 9px; border-radius: 4px; border: 1px solid var(--border);
               background: transparent; color: var(--muted); cursor: pointer;
               font-size: 11px; font-family: inherit; transition: all .12s; }
  .pause-btn.paused { border-color: var(--yellow); color: var(--yellow); background: rgba(210,153,34,.10); }
  #count { font-size: 11px; color: var(--muted); margin-left: auto; }
  /* ── Table ── */
  .log-wrap { height: calc(100vh - 46px - 32px - 40px); overflow-y: auto; }
  table { width: 100%; border-collapse: collapse; table-layout: fixed; }
  col.c-ts   { width: 90px; }
  col.c-ev   { width: 110px; }
  col.c-meth { width: 230px; }
  col.c-peer { width: 140px; }
  col.c-sum  { width: auto; }
  col.c-dur  { width: 70px; }
  col.c-st   { width: 80px; }
  thead th {
    position: sticky; top: 0; z-index: 1; background: var(--surface);
    padding: 4px 8px; text-align: left; color: var(--muted); font-weight: 500;
    border-bottom: 1px solid var(--border); font-size: 11px; white-space: nowrap;
  }
  tbody tr { border-bottom: 1px solid rgba(48,54,61,.4); }
  tbody tr:hover { background: rgba(255,255,255,.03); }
  td { padding: 3px 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  /* event-type colours */
  .ev-DATA           { color: var(--blue); }
  .ev-SUBSCRIBE_OPEN { color: var(--cyan); }
  .ev-CALL_START     { color: var(--muted); }
  .ev-MSG_RECV       { color: var(--muted); }
  .ev-MSG_SEND       { color: var(--muted); }
  .ev-CALL_END       { color: var(--purple);}
  .meth { color: var(--text); }
  .peer { color: var(--muted); font-size: 11px; }
  .sum  { color: var(--text); font-family: ui-monospace, monospace; }
  .dur  { color: var(--yellow); text-align: right; }
  .st-ok  { color: var(--muted); }
  .st-err { color: var(--red); }
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
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
  <span class="subtitle">gRPC Traffic Inspector</span>
  <span class="gw-status-badge off" id="gw-status-badge">○ gateway</span>
  <span class="spacer"></span>
  <span class="gw-badge" id="gw-badge">● gateway :50051</span>
</header>
<nav id="panel-nav">
  <a class="nav-link" data-port="8086">Launcher</a>
  <a class="nav-link" data-port="8080">Dashboard</a>
  <a class="nav-link" data-port="8081">Nodes</a>
  <a class="nav-link" data-port="8082">Commander</a>
  <a class="nav-link" data-port="8083">Recorder</a>
</nav>
<div class="toolbar">
  <label>Show:</label>
  <button class="tog data-on"  id="tog-data"  onclick="toggle('DATA')">DATA</button>
  <button class="tog sub-on"   id="tog-sub"   onclick="toggle('SUBSCRIBE_OPEN')">SUB_OPEN</button>
  <button class="tog start-on" id="tog-start" onclick="toggle('CALL_START')">CALL_START</button>
  <button class="tog end-on"   id="tog-end"   onclick="toggle('CALL_END')">CALL_END</button>
  <div class="sep"></div>
  <input id="kw" type="text" placeholder="filter: method / peer / content…" oninput="render()"/>
  <div class="sep"></div>
  <button class="pause-btn" id="btn-pause" onclick="togglePause()">Pause</button>
  <button class="ctrl-btn"  onclick="clearLog()">Clear</button>
  <span id="count">0 events</span>
</div>
<div class="log-wrap" id="log-wrap">
  <table>
    <colgroup>
      <col class="c-ts"/><col class="c-ev"/><col class="c-meth"/>
      <col class="c-peer"/><col class="c-sum"/><col class="c-dur"/><col class="c-st"/>
    </colgroup>
    <thead>
      <tr>
        <th>Time</th>
        <th>Event</th>
        <th>Method</th>
        <th>Peer</th>
        <th>Content / Summary</th>
        <th style="text-align:right">µs</th>
        <th>Status</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>
<script>
// ── State ─────────────────────────────────────────────────────────────────────
let all        = [];
let lastSeq    = 0;
let paused     = false;
let autoScroll = true;
// DATA and SUBSCRIBE_OPEN on by default; raw lifecycle events off by default
const show = { DATA: true, SUBSCRIBE_OPEN: true, CALL_START: false, MSG_RECV: false, MSG_SEND: false, CALL_END: false };
// ── Fetch ─────────────────────────────────────────────────────────────────────
async function fetchEvents() {
  if (paused) return;
  try {
    const r = await fetch('/api/events?after=' + lastSeq);
    const d = await r.json();
    if (d.events.length) {
      all = all.concat(d.events);
      lastSeq = d.events[d.events.length - 1].seq;
      if (all.length > 5000) all = all.slice(-5000);
      render();
    }
  } catch {}
}
// ── Render ────────────────────────────────────────────────────────────────────
function render() {
  const kw = document.getElementById('kw').value.toLowerCase();
  const tbody = document.getElementById('tbody');
  const rows = [];
  let shown = 0;
  for (const e of all) {
    if (!show[e.event_type]) continue;
    if (kw && !matchEvent(e, kw)) continue;
    rows.push(rowHTML(e));
    shown++;
  }
  tbody.innerHTML = rows.join('');
  document.getElementById('count').textContent =
      shown + ' / ' + all.length + ' events';
  if (autoScroll) {
    const w = document.getElementById('log-wrap');
    w.scrollTop = w.scrollHeight;
  }
}
function matchEvent(e, kw) {
  return e.method.toLowerCase().includes(kw)     ||
         e.peer.toLowerCase().includes(kw)        ||
         e.event_type.toLowerCase().includes(kw)  ||
         e.call_type.toLowerCase().includes(kw)   ||
         (e.summary && e.summary.toLowerCase().includes(kw));
}
function rowHTML(e) {
  const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');
  const dur = e.duration_us ? e.duration_us.toLocaleString() + ' µs' : '';
  let stClass = 'st-ok', stText = '';
  if (e.event_type === 'CALL_END') {
    stClass = e.status_code === 0 ? 'st-ok' : 'st-err';
    stText  = e.status_code === 0
        ? 'OK'
        : 'ERR ' + e.status_code + (e.status_msg ? ': ' + e.status_msg : '');
  }
  // Content column: summary if present, else msg_bytes hint
  let content = e.summary || '';
  if (!content && e.msg_bytes) content = e.msg_bytes + ' B';
  return `<tr>
    <td>${esc(e.ts)}</td>
    <td class="ev-${e.event_type}">${e.event_type}</td>
    <td class="meth" title="${esc(e.method)}">${esc(e.method)}</td>
    <td class="peer" title="${esc(e.peer)}">${esc(e.peer)}</td>
    <td class="sum"  title="${esc(content)}">${esc(content)}</td>
    <td class="dur">${esc(dur)}</td>
    <td class="${stClass}">${esc(stText)}</td>
  </tr>`;
}
// ── Controls ──────────────────────────────────────────────────────────────────
const togClass = {
  DATA: 'data-on', SUBSCRIBE_OPEN: 'sub-on',
  CALL_START: 'start-on', CALL_END: 'end-on'
};
const togId = {
  DATA: 'tog-data', SUBSCRIBE_OPEN: 'tog-sub',
  CALL_START: 'tog-start', CALL_END: 'tog-end'
};
function toggle(ev) {
  show[ev] = !show[ev];
  document.getElementById(togId[ev]).classList.toggle(togClass[ev], show[ev]);
  render();
}
function togglePause() {
  paused = !paused;
  const btn = document.getElementById('btn-pause');
  btn.textContent = paused ? 'Resume' : 'Pause';
  btn.classList.toggle('paused', paused);
  if (!paused) fetchEvents();
}
async function clearLog() {
  all = []; lastSeq = 0;
  await fetch('/api/clear');
  render();
}
document.getElementById('log-wrap').addEventListener('scroll', function() {
  autoScroll = (this.scrollTop + this.clientHeight >= this.scrollHeight - 8);
});
// ── Nav bar ───────────────────────────────────────────────────────────────────
(function() {
  const h = window.location.hostname, p = window.location.port;
  document.querySelectorAll('.nav-link').forEach(a => {
    a.href = 'http://' + h + ':' + a.dataset.port + '/';
    if (a.dataset.port === p) a.classList.add('active');
  });
})();
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
// ── Boot ──────────────────────────────────────────────────────────────────────
setInterval(fetchEvents, 200);
fetchEvents();
</script>
</body>
</html>
"""
# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    t = threading.Thread(target=_run_subscriber, daemon=True)
    t.start()
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
