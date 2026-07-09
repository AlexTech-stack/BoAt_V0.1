"""
BoAt Platform — Node Control Panel
Run:  python3 demo/control_panel.py
Open: http://localhost:8081
"""
from __future__ import annotations
import os
import re
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
# ── Configuration ──────────────────────────────────────────────────────────────
_NODES_DIR   = Path(__file__).parent.parent / "boat-platform" / "nodes"
_SDK_PATH    = Path(__file__).parent.parent / "boat-platform" / "sdk" / "python"
_DEFAULT_GW  = os.environ.get("BOAT_GATEWAY", "localhost:50051")
_LOG_LINES   = 120   # rolling log lines kept per node
_PORT        = int(os.environ.get("BOAT_CP_PORT", "8081"))
# ── Node state ─────────────────────────────────────────────────────────────────
@dataclass
class NodeInfo:
    name: str                    # filename without .py
    path: Path                   # absolute path
    docstring: str               # first """ line of the file
    interactive: bool            # uses input() → can't run headlessly
    process: Optional[subprocess.Popen] = field(default=None, repr=False)
    exit_code: Optional[int]     = None
    _log: deque                  = field(default_factory=lambda: deque(maxlen=_LOG_LINES))
    _lock: threading.Lock        = field(default_factory=threading.Lock)
    _log_thread: Optional[threading.Thread] = field(default=None, repr=False)
    # ── log access ────────────────────────────────────────────────────────────
    def append_log(self, line: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with self._lock:
            self._log.append({"ts": ts, "text": line.rstrip()})
    def get_log(self) -> List[dict]:
        with self._lock:
            return list(self._log)
    # ── status ────────────────────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None
    @property
    def status(self) -> str:
        if self.process is None:
            return "stopped"
        if self.process.poll() is None:
            return "running"
        code = self.process.poll()
        return "stopped" if code == 0 else f"exited:{code}"
    def pid(self) -> Optional[int]:
        if self.running and self.process is not None:
            return self.process.pid
        return None
    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start(self, gateway: str) -> None:
        if self.running:
            return
        env = os.environ.copy()
        paths = [str(_SDK_PATH)] + env.get("PYTHONPATH", "").split(":")
        env["PYTHONPATH"] = ":".join(p for p in paths if p)
        cmd = [sys.executable, str(self.path), "--address", gateway]
        self.exit_code = None
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            text=True,
            bufsize=1,
        )
        self.append_log(f"[control-panel] started PID {self.process.pid}")
        self._log_thread = threading.Thread(
            target=self._drain_output, daemon=True,
            name=f"log-{self.name}"
        )
        self._log_thread.start()
    def stop(self) -> None:
        if not self.running or self.process is None:
            return
        self.append_log("[control-panel] sending SIGTERM…")
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.append_log("[control-panel] timeout — sending SIGKILL")
            self.process.kill()
            self.process.wait()
        self.exit_code = self.process.returncode
        self.append_log(f"[control-panel] exited with code {self.exit_code}")
    def _drain_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.append_log(line)
        self.exit_code = self.process.wait()
# ── Node discovery ─────────────────────────────────────────────────────────────
def _extract_docstring(path: Path) -> str:
    """Return the first docstring line from a Python file, or ''."""
    try:
        src = path.read_text(encoding="utf-8")
        m = re.search(r'"""(.*?)"""', src, re.DOTALL)
        if m:
            first_line = m.group(1).strip().splitlines()[0].strip()
            return first_line[:120]
    except Exception:
        pass
    return ""
def _is_interactive(path: Path) -> bool:
    try:
        return "input(" in path.read_text(encoding="utf-8")
    except Exception:
        return False
def _discover_nodes() -> Dict[str, NodeInfo]:
    nodes: Dict[str, NodeInfo] = {}
    for py in sorted(_NODES_DIR.glob("*.py")):
        if py.name.startswith("_"):
            continue
        name = py.stem
        nodes[name] = NodeInfo(
            name=name,
            path=py.absolute(),
            docstring=_extract_docstring(py),
            interactive=_is_interactive(py),
        )
    return nodes
_nodes: Dict[str, NodeInfo] = _discover_nodes()
# ── REST API ───────────────────────────────────────────────────────────────────
app = FastAPI()
@app.get("/api/gateway/health")
def api_gw_health():
    try:
        from boat.client import BoAtClient
        from boat.v1 import can_pb2
        c = BoAtClient(_DEFAULT_GW)
        c.can.ListBuses(can_pb2.ListBusesRequest())
        return {"running": True}
    except Exception:
        return {"running": False}
@app.get("/api/nodes")
def api_list_nodes():
    out = []
    for n in _nodes.values():
        out.append({
            "name":        n.name,
            "docstring":   n.docstring,
            "interactive": n.interactive,
            "status":      n.status,
            "pid":         n.pid(),
        })
    return {"nodes": out}
@app.post("/api/nodes/{name}/start")
def api_start(name: str, address: str = _DEFAULT_GW):
    if name not in _nodes:
        raise HTTPException(status_code=404, detail="Node not found")
    n = _nodes[name]
    if n.interactive:
        raise HTTPException(status_code=400, detail="Node requires interactive terminal")
    n.start(address)
    return {"ok": True, "pid": n.pid()}
@app.post("/api/nodes/{name}/stop")
def api_stop(name: str):
    if name not in _nodes:
        raise HTTPException(status_code=404, detail="Node not found")
    _nodes[name].stop()
    return {"ok": True}
@app.get("/api/nodes/{name}/log")
def api_log(name: str):
    if name not in _nodes:
        raise HTTPException(status_code=404, detail="Node not found")
    return {"log": _nodes[name].get_log()}
@app.get("/api/gateway")
def api_gateway():
    return {"address": _DEFAULT_GW}
# ── HTML ───────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — Node Control Panel</title>
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
    min-height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    font-size: 13px;
  }
  /* ── header ── */
  header {
    height: 46px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    padding: 0 20px;
    gap: 14px;
    position: sticky; top: 0; z-index: 10;
  }
  .logo       { font-weight: 700; font-size: 15px; color: var(--blue); letter-spacing: .4px; }
  .subtitle   { color: var(--muted); font-size: 12px; }
  header .spacer { flex: 1; }
  .gw-badge {
    font-size: 11px; padding: 2px 10px; border-radius: 12px;
    background: #1f3a1f; color: var(--green); border: 1px solid #2ea043;
    font-family: var(--mono);
  }
  .gw-input {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 4px;
    padding: 3px 9px;
    font-size: 12px;
    font-family: var(--mono);
    width: 200px;
    outline: none;
  }
  .gw-input:focus { border-color: var(--blue); }
  /* ── main grid ── */
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
    gap: 16px;
    padding: 20px;
  }
  /* ── node card ── */
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .card-header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 14px 10px;
    border-bottom: 1px solid var(--border);
  }
  .status-dot {
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
    background: var(--muted);
    transition: background .3s;
  }
  .status-dot.running  { background: var(--green); animation: pulse 2s infinite; }
  .status-dot.exited   { background: var(--red); }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .card-name {
    font-family: var(--mono);
    font-size: 13px;
    font-weight: 600;
    color: var(--blue);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .pid-badge {
    font-size: 10px; font-family: var(--mono);
    color: var(--muted); background: #1c2128;
    border: 1px solid var(--border);
    border-radius: 4px; padding: 1px 6px;
  }
  .interactive-badge {
    font-size: 10px; padding: 1px 7px;
    border-radius: 10px; background: #2d2200; color: var(--yellow);
    border: 1px solid #6e4c00; flex-shrink: 0;
  }
  .card-doc {
    padding: 8px 14px;
    font-size: 12px;
    color: var(--muted);
    line-height: 1.5;
    border-bottom: 1px solid var(--border);
    min-height: 34px;
  }
  .card-controls {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    border-bottom: 1px solid var(--border);
  }
  .status-text {
    font-size: 11px; font-family: var(--mono);
    flex: 1;
  }
  .status-text.running { color: var(--green); }
  .status-text.stopped { color: var(--muted); }
  .status-text.error   { color: var(--red); }
  .btn {
    font-size: 11px; padding: 4px 14px;
    border-radius: 5px; cursor: pointer;
    border: 1px solid var(--border);
    font-weight: 600; transition: background .15s, border-color .15s;
  }
  .btn:disabled { opacity: .4; cursor: not-allowed; }
  .btn-start {
    background: #1a3a1a; color: var(--green); border-color: #2ea043;
  }
  .btn-start:hover:not(:disabled) { background: #1f4a1f; border-color: var(--green); }
  .btn-stop {
    background: #3a1a1a; color: var(--red); border-color: #8b2020;
  }
  .btn-stop:hover:not(:disabled) { background: #4a2020; border-color: var(--red); }
  .btn-log {
    background: var(--bg); color: var(--muted); border-color: var(--border);
  }
  .btn-log:hover { background: #1c2128; color: var(--text); }
  /* ── log panel ── */
  .card-log {
    display: none;
    flex-direction: column;
  }
  .card-log.open { display: flex; }
  .log-scroll {
    max-height: 200px;
    overflow-y: auto;
    padding: 8px 14px;
    font-family: var(--mono);
    font-size: 11px;
    line-height: 1.6;
    background: #0a0d12;
  }
  .log-line { display: flex; gap: 10px; }
  .log-ts   { color: #555; flex-shrink: 0; width: 78px; }
  .log-text { color: #b0c4d8; word-break: break-all; }
  .log-text.control { color: var(--muted); font-style: italic; }
  /* ── empty state ── */
  .empty {
    grid-column: 1 / -1;
    text-align: center;
    padding: 60px;
    color: var(--muted);
    font-size: 14px;
  }
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
  /* ── Nav bar ── */
  #panel-nav {
    height: 32px; position: sticky; top: 46px; z-index: 9;
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
  <span class="subtitle">Node Control Panel</span>
  <span class="gw-status-badge off" id="gw-status-badge">○ gateway</span>
  <span class="spacer"></span>
  <input class="gw-input" id="gw-input" placeholder="gateway address" title="Gateway gRPC address"/>
  <span class="gw-badge" id="gw-badge">● :50051</span>
</header>
<nav id="panel-nav">
  <a class="nav-link" data-port="8086">Launcher</a>
  <a class="nav-link" data-port="8080">Dashboard</a>
  <a class="nav-link" data-port="8081">Nodes</a>
  <a class="nav-link" data-port="8082">Commander</a>
  <a class="nav-link" data-port="8083">Recorder</a>
</nav>
<div class="grid" id="grid">
  <div class="empty">Loading nodes…</div>
</div>
<script>
// ── State ─────────────────────────────────────────────────────────────────────
const nodeState = {};   // name → {status, pid, logOpen, logData}
let gateway = "localhost:50051";
// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  const r = await fetch('/api/gateway');
  const d = await r.json();
  gateway = d.address;
  document.getElementById('gw-input').value = gateway;
  document.getElementById('gw-badge').textContent = '● ' + gateway;
  await renderNodes();
  setInterval(poll, 1000);
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
document.getElementById('gw-input').addEventListener('change', e => {
  gateway = e.target.value.trim() || 'localhost:50051';
  document.getElementById('gw-badge').textContent = '● ' + gateway;
});
// ── First render ──────────────────────────────────────────────────────────────
async function renderNodes() {
  const r = await fetch('/api/nodes');
  const d = await r.json();
  const grid = document.getElementById('grid');
  if (!d.nodes.length) {
    grid.innerHTML = '<div class="empty">No nodes found in nodes/ directory.</div>';
    return;
  }
  grid.innerHTML = '';
  for (const n of d.nodes) {
    nodeState[n.name] = { status: n.status, pid: n.pid, interactive: n.interactive, logOpen: false, logData: [] };
    grid.appendChild(buildCard(n));
  }
}
// ── Build card DOM ────────────────────────────────────────────────────────────
function buildCard(n) {
  const div = document.createElement('div');
  div.className = 'card';
  div.id = 'card-' + n.name;
  div.innerHTML = cardHTML(n);
  return div;
}
function statusClass(status) {
  if (status === 'running') return 'running';
  if (status.startsWith('exited:') && status !== 'exited:0') return 'error';
  return 'stopped';
}
function statusLabel(status) {
  if (status === 'running') return 'running';
  if (status === 'stopped') return 'stopped';
  if (status.startsWith('exited:')) {
    const code = status.split(':')[1];
    return code === '0' ? 'stopped (exit 0)' : `exited (code ${code})`;
  }
  return status;
}
function cardHTML(n) {
  const sc    = statusClass(n.status);
  const isRun = n.status === 'running';
  const pidHtml = n.pid ? `<span class="pid-badge">PID&nbsp;${n.pid}</span>` : '';
  const intHtml = n.interactive
    ? '<span class="interactive-badge">interactive</span>' : '';
  const startDis = isRun || n.interactive ? 'disabled' : '';
  const stopDis  = isRun ? '' : 'disabled';
  return `
    <div class="card-header">
      <div class="status-dot ${sc}"></div>
      <span class="card-name">${n.name}</span>
      ${pidHtml}
      ${intHtml}
    </div>
    <div class="card-doc">${escHtml(n.docstring || '—')}</div>
    <div class="card-controls">
      <span class="status-text ${sc}">${statusLabel(n.status)}</span>
      <button class="btn btn-start" id="btn-start-${n.name}" ${startDis}
              onclick="startNode('${n.name}')">▶ Start</button>
      <button class="btn btn-stop"  id="btn-stop-${n.name}"  ${stopDis}
              onclick="stopNode('${n.name}')">■ Stop</button>
      <button class="btn btn-log" onclick="toggleLog('${n.name}')">Log ▾</button>
    </div>
    <div class="card-log" id="log-panel-${n.name}">
      <div class="log-scroll" id="log-${n.name}"></div>
    </div>`;
}
// ── Poll ──────────────────────────────────────────────────────────────────────
async function poll() {
  let r, d;
  try {
    r = await fetch('/api/nodes');
    d = await r.json();
  } catch { return; }
  for (const n of d.nodes) {
    const prev = nodeState[n.name];
    if (!prev) continue;
    const changed = prev.status !== n.status || prev.pid !== n.pid;
    if (changed) {
      prev.status = n.status;
      prev.pid    = n.pid;
      prev.interactive = n.interactive;
      refreshCard(n);
    }
    if (prev.logOpen) {
      await fetchLog(n.name);
    }
  }
}
function refreshCard(n) {
  const card = document.getElementById('card-' + n.name);
  if (!card) return;
  const sc    = statusClass(n.status);
  const isRun = n.status === 'running';
  card.querySelector('.status-dot').className = 'status-dot ' + sc;
  card.querySelector('.status-text').className = 'status-text ' + sc;
  card.querySelector('.status-text').textContent = statusLabel(n.status);
  const pb = card.querySelector('.pid-badge');
  if (n.pid) {
    if (pb) { pb.textContent = 'PID\u00a0' + n.pid; }
    else {
      const badge = document.createElement('span');
      badge.className = 'pid-badge';
      badge.textContent = 'PID\u00a0' + n.pid;
      card.querySelector('.card-name').after(badge);
    }
  } else if (pb) { pb.remove(); }
  const startBtn = document.getElementById('btn-start-' + n.name);
  const stopBtn  = document.getElementById('btn-stop-'  + n.name);
  if (startBtn) startBtn.disabled = isRun || nodeState[n.name].interactive;
  if (stopBtn)  stopBtn.disabled  = !isRun;
}
// ── Log ───────────────────────────────────────────────────────────────────────
async function fetchLog(name) {
  let r, d;
  try {
    r = await fetch('/api/nodes/' + name + '/log');
    d = await r.json();
  } catch { return; }
  const el = document.getElementById('log-' + name);
  if (!el) return;
  const newLines = d.log;
  const state    = nodeState[name];
  if (!state) return;
  const prevLen = state.logData.length;
  if (newLines.length === prevLen) return;
  state.logData = newLines;
  el.innerHTML  = '';
  const atBottom = el.scrollHeight - el.clientHeight - el.scrollTop < 40;
  for (const line of newLines) {
    el.appendChild(logLineEl(line));
  }
  if (atBottom || newLines.length - prevLen > 0) {
    el.scrollTop = el.scrollHeight;
  }
}
function logLineEl(line) {
  const row  = document.createElement('div');
  row.className = 'log-line';
  const isControl = line.text.startsWith('[control-panel]');
  row.innerHTML =
    `<span class="log-ts">${line.ts}</span>` +
    `<span class="log-text${isControl ? ' control' : ''}">${escHtml(line.text)}</span>`;
  return row;
}
function toggleLog(name) {
  const panel = document.getElementById('log-panel-' + name);
  const state = nodeState[name];
  if (!panel || !state) return;
  state.logOpen = !state.logOpen;
  panel.classList.toggle('open', state.logOpen);
  const btn = document.querySelector(`#card-${name} .btn-log`);
  if (btn) btn.textContent = state.logOpen ? 'Log ▴' : 'Log ▾';
  if (state.logOpen) fetchLog(name);
}
// ── Actions ───────────────────────────────────────────────────────────────────
async function startNode(name) {
  document.getElementById('btn-start-' + name).disabled = true;
  try {
    const r = await fetch(`/api/nodes/${name}/start?address=${encodeURIComponent(gateway)}`,
                          { method: 'POST' });
    if (!r.ok) {
      const e = await r.json();
      alert('Failed to start ' + name + ': ' + (e.detail || r.status));
    }
  } catch (e) { alert('Error: ' + e); }
}
async function stopNode(name) {
  document.getElementById('btn-stop-' + name).disabled = true;
  try {
    await fetch(`/api/nodes/${name}/stop`, { method: 'POST' });
  } catch (e) { alert('Error: ' + e); }
}
// ── Helpers ───────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
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
init();
</script>
</body>
</html>
"""
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)
if __name__ == "__main__":
    print(f"BoAt Node Control Panel → http://localhost:{_PORT}")
    print(f"Nodes directory : {_NODES_DIR}")
    print(f"Default gateway : {_DEFAULT_GW}")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
