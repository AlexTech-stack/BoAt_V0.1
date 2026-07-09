"""
BoAt Platform — System Overview Dashboard
Run:  python3 demo/system_dashboard.py
Open: http://localhost:8081
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from boat.client import BoAtClient
from boat.v1 import can_pb2, simulation_pb2
app = FastAPI()
client = BoAtClient("localhost:50051")

@app.get("/api/gateway/health")
def api_gw_health():
    c = None
    try:
        c = BoAtClient("localhost:50051")
        c.can.ListBuses(can_pb2.ListBusesRequest())
        return {"running": True}
    except Exception:
        return {"running": False}
    finally:
        if c: c.close()

# ── API ────────────────────────────────────────────────────────────────────────
@app.get("/api/system")
def api_system():
    topology = {
        "gateway": {"connected": False, "address": "localhost:50051"},
        "can_buses": [],
        "simulations": [],
    }
    # Gateway + CAN buses
    try:
        resp = client.can.ListBuses(can_pb2.ListBusesRequest())
        topology["can_buses"] = [{"iface": i} for i in resp.ifaces]
        topology["gateway"]["connected"] = True
    except Exception:
        pass
    # Simulations
    try:
        resp = client.simulation.ListSimulations(simulation_pb2.ListSimulationsRequest())
        STATE_NAMES = {0:"UNKNOWN",1:"IDLE",2:"RUNNING",3:"PAUSED",4:"STOPPED",5:"ERROR"}
        topology["simulations"] = [
            {
                "id": s.simulation_id,
                "short_id": s.simulation_id[-8:] if len(s.simulation_id) > 8 else s.simulation_id,
                "scenario_id": s.scenario_id,
                "state": STATE_NAMES.get(int(s.state), "?"),
                "tick": s.tick,
            }
            for s in resp.simulations
        ]
    except Exception:
        pass
    return topology
# ── HTML ───────────────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — System</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg:      #0d1117;
    --panel:   #161b22;
    --border:  #30363d;
    --text:    #e6edf3;
    --muted:   #8b949e;
    --blue:    #58a6ff;
    --green:   #3fb950;
    --yellow:  #d29922;
    --red:     #f85149;
    --purple:  #d2a8ff;
    --orange:  #ffa657;
    --mono:    "SFMono-Regular", Consolas, monospace;
  }
  html, body {
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 13px;
    overflow: hidden;
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
    transition: all .3s;
  }
  .gw-badge.offline {
    background: #3d0b0b; color: var(--red); border-color: var(--red);
  }
  .poll-ts { font-size: 11px; color: var(--muted); font-family: var(--mono); }
  /* ── Diagram area ── */
  #diagram {
    position: relative;
    width: 100%;
    height: calc(100vh - 46px);
    overflow: hidden;
  }
  /* SVG overlay — sits behind cards */
  #svg-layer {
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 0;
  }
  /* ── Columns ── */
  .col-left, .col-center {
    position: absolute;
    top: 0; bottom: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    z-index: 1;
  }
  .col-left   { left: 0;    width: 25%; }
  .col-center { left: 25%;  width: 75%; }
  /* ── Section labels ── */
  .col-label {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .9px;
    color: var(--muted);
    margin-top: 16px;
    margin-bottom: 10px;
  }
  /* ── Cards ── */
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
    width: 90%;
    margin-bottom: 10px;
    position: relative;
    transition: border-color .3s;
  }
  .card:hover { border-color: #58a6ff55; }
  .card-title {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .7px;
    color: var(--muted);
    margin-bottom: 6px;
  }
  .card-name {
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
    font-family: var(--mono);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .card-sub {
    font-size: 11px;
    color: var(--muted);
    font-family: var(--mono);
    margin-top: 3px;
  }
  /* Gateway card */
  .card-gateway {
    border-color: #1f6feb;
    background: #0d1e33;
    width: 60%;
    margin: 20px auto 0;
    text-align: center;
  }
  .card-gateway .card-name { font-size: 16px; color: var(--blue); }
  /* Status dot */
  .status-dot {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    margin-right: 6px;
    vertical-align: middle;
  }
  .dot-green  { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot-red    { background: var(--red);   box-shadow: 0 0 6px var(--red); }
  .dot-yellow { background: var(--yellow); }
  .dot-muted  { background: var(--muted); }
  /* CAN bus cards */
  .card-canbus { border-left: 3px solid var(--blue); }
  /* Simulation cards */
  .card-sim { border-left: 3px solid var(--green); }
  .state-badge {
    display: inline-block;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 10px;
    font-family: var(--mono);
    margin-top: 4px;
  }
  .state-RUNNING { background: #1f3a1f; color: var(--green); }
  .state-PAUSED  { background: #2d2208; color: var(--yellow); }
  .state-STOPPED { background: #3d0b0b; color: var(--red); }
  .state-IDLE    { background: #1c2128; color: var(--muted); }
  .state-UNKNOWN { background: #1c2128; color: var(--muted); }
  .state-ERROR   { background: #3d0b0b; color: var(--red); }
  .gw-status-badge {
    font-size: 11px; padding: 2px 10px; border-radius: 12px;
    font-family: var(--mono); transition: all .3s; flex-shrink: 0;
  }
  .gw-status-badge.on { background: #1f3a1f; color: var(--green); border: 1px solid #2ea043; }
  .gw-status-badge.off { background: #3d0b0b; color: var(--red); border: 1px solid #8b2020; }
  /* Empty state */
  .empty-hint {
    font-size: 11px;
    color: var(--muted);
    font-style: italic;
    text-align: center;
    padding: 20px 0;
  }
  /* Sim section within center column */
  .center-gateway { flex-shrink: 0; width: 100%; display: flex; justify-content: center; }
  .center-sims    { flex: 1; overflow-y: auto; width: 100%; padding: 0 10px; }
</style>
</head>
<body>
<header>
  <span class="logo">⛵ BoAt</span>
  <span class="subtitle">System Overview</span>
  <span class="gw-status-badge off" id="gw-status-badge">○ gateway</span>
  <span class="spacer"></span>
  <span class="poll-ts" id="poll-ts"></span>
  <span class="gw-badge" id="gw-badge">● gateway :50051</span>
</header>
<div id="diagram">
  <svg id="svg-layer"></svg>
  <!-- Left: CAN Buses -->
  <div class="col-left" id="col-canbuses">
    <div class="col-label">CAN Buses</div>
    <div id="canbus-cards" style="width:100%;padding:0 8px;"></div>
    <div class="empty-hint" id="canbus-empty" style="display:none">No buses registered</div>
  </div>
  <!-- Center: Gateway + Simulations -->
  <div class="col-center">
    <div class="center-gateway">
      <div class="card card-gateway" id="card-gateway">
        <div class="card-title">Gateway</div>
        <div class="card-name" id="gw-name">localhost:50051</div>
        <div class="card-sub" id="gw-status">
          <span class="status-dot dot-muted"></span>connecting…
        </div>
      </div>
    </div>
    <div style="font-size:10px;font-weight:600;text-transform:uppercase;
                letter-spacing:.9px;color:var(--muted);margin:10px 0 8px;text-align:center;">
      Simulations
    </div>
    <div class="center-sims" id="sim-cards"></div>
    <div class="empty-hint" id="sim-empty">No active simulations</div>
  </div>
</div><!-- #diagram -->
<script>
// ── SVG connections ───────────────────────────────────────────────────────────
const svg = document.getElementById('svg-layer');
// Define an arrowhead marker
function ensureMarkers() {
  if (svg.querySelector('defs')) return;
  svg.innerHTML = `
    <defs>
      <marker id="arrow-blue"   markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
        <path d="M0,0 L0,6 L8,3 z" fill="#58a6ff55"/>
      </marker>
      <marker id="arrow-purple" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
        <path d="M0,0 L0,6 L8,3 z" fill="#d2a8ff55"/>
      </marker>
      <marker id="arrow-green"  markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
        <path d="M0,0 L0,6 L8,3 z" fill="#3fb95055"/>
      </marker>
    </defs>`;
}
function pt(el, edge) {
  const r  = el.getBoundingClientRect();
  const sr = svg.getBoundingClientRect();
  const cx = r.left + r.width  / 2 - sr.left;
  const cy = r.top  + r.height / 2 - sr.top;
  switch (edge) {
    case 'left':   return { x: r.left   - sr.left, y: cy };
    case 'right':  return { x: r.right  - sr.left, y: cy };
    case 'top':    return { x: cx, y: r.top    - sr.top };
    case 'bottom': return { x: cx, y: r.bottom - sr.top };
  }
}
function bezier(p1, p2, stroke, dashed, marker) {
  const dx = (p2.x - p1.x) * 0.5;
  const cp1 = { x: p1.x + dx, y: p1.y };
  const cp2 = { x: p2.x - dx, y: p2.y };
  const d = `M${p1.x},${p1.y} C${cp1.x},${cp1.y} ${cp2.x},${cp2.y} ${p2.x},${p2.y}`;
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', d);
  path.setAttribute('stroke', stroke);
  path.setAttribute('stroke-width', '1.5');
  path.setAttribute('fill', 'none');
  if (dashed) path.setAttribute('stroke-dasharray', '5 4');
  if (marker) path.setAttribute('marker-end', `url(#${marker})`);
  svg.appendChild(path);
}
function drawConnections(data) {
  // Clear old paths (keep defs)
  const defs = svg.querySelector('defs');
  svg.innerHTML = '';
  if (defs) svg.appendChild(defs);
  ensureMarkers();
  const gw = document.getElementById('card-gateway');
  if (!gw) return;
  // Gateway → CAN buses
  data.can_buses.forEach(b => {
    const el = document.getElementById('card-canbus-' + b.iface);
    if (!el) return;
    bezier(pt(gw, 'left'), pt(el, 'right'), '#58a6ff44', false, 'arrow-blue');
  });
  // Gateway → Simulations
  data.simulations.forEach(s => {
    const el = document.getElementById('card-sim-' + s.id);
    if (!el) return;
    bezier(pt(gw, 'bottom'), pt(el, 'top'), '#3fb95044', false, 'arrow-green');
  });
}
// ── Render ────────────────────────────────────────────────────────────────────
function renderCanbuses(buses) {
  const container = document.getElementById('canbus-cards');
  const empty     = document.getElementById('canbus-empty');
  if (!buses.length) {
    container.innerHTML = '';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  container.innerHTML = buses.map(b => `
    <div class="card card-canbus" id="card-canbus-${b.iface}">
      <div class="card-title">CAN Interface</div>
      <div class="card-name">${b.iface}</div>
    </div>`).join('');
}
function renderSims(sims) {
  const container = document.getElementById('sim-cards');
  const empty     = document.getElementById('sim-empty');
  if (!sims.length) {
    container.innerHTML = '';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  container.innerHTML = sims.map(s => `
    <div class="card card-sim" id="card-sim-${s.id}">
      <div class="card-title">Simulation</div>
      <div class="card-name" style="font-size:12px">…${s.short_id}</div>
      <div class="card-sub">scenario: ${s.scenario_id}</div>
      <div>
        <span class="state-badge state-${s.state}">${s.state}</span>
        <span style="font-size:11px;color:var(--muted);margin-left:8px;font-family:var(--mono)">
          tick ${s.tick}
        </span>
      </div>
    </div>`).join('');
}
function renderGateway(gw) {
  const badge  = document.getElementById('gw-badge');
  const status = document.getElementById('gw-status');
  if (gw.connected) {
    badge.className = 'gw-badge';
    badge.textContent = '● gateway :50051';
    status.innerHTML = '<span class="status-dot dot-green"></span>connected';
  } else {
    badge.className = 'gw-badge offline';
    badge.textContent = '○ gateway offline';
    status.innerHTML = '<span class="status-dot dot-red"></span>unreachable';
  }
}
// ── Poll ──────────────────────────────────────────────────────────────────────
function poll() {
  fetch('/api/system')
    .then(r => r.json())
    .then(data => {
      renderGateway(data.gateway);
      renderCanbuses(data.can_buses);
      renderSims(data.simulations);
      // Connections need one frame for layout to settle
      requestAnimationFrame(() => drawConnections(data));
      const now = new Date();
      document.getElementById('poll-ts').textContent =
        now.toTimeString().slice(0, 8);
    })
    .catch(() => {
      document.getElementById('gw-badge').className = 'gw-badge offline';
    });
}
// Redraw SVG lines whenever window resizes
window.addEventListener('resize', () => {
  // Re-trigger last poll result — easiest: just poll again
  poll();
});
ensureMarkers();
poll();
setInterval(poll, 2000);
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
    port = int(os.environ.get("BOAT_SYSTEM_PORT", "8081"))
    print(f"BoAt System Dashboard → http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
