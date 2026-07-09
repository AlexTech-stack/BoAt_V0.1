"""
BoAt Platform — Trace Analyzer
Read BLF trace files, analyze CAN messages, derive PDU database JSON.
Run:  python3 tools/trace_analyzer.py
Open: http://localhost:8088
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from boat.trace_analyzer import TraceAnalyzer
from boat.trace_reverse_engineer import TraceReverseEngineer

_PORT = int(os.environ.get("BOAT_TRACE_ANALYZER_PORT", "8088"))
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "boat-platform" / "config"
_EXPORT_DIR = Path(__file__).resolve().parent.parent / "traces"
_EXPORT_DIR.mkdir(exist_ok=True)

app = FastAPI()

_analysis_cache: dict[str, Any] = {}
_analysis_lock = threading.Lock()

# ── API routes ──────────────────────────────────────────────────────────────

@app.get("/api/blf/list")
def api_blf_list():
    files = []
    for d in [_EXPORT_DIR, _CONFIG_DIR, Path.home(), Path("/")]:
        try:
            for f in Path(d).glob("*.blf"):
                files.append(str(f))
        except Exception:
            pass
    files = sorted(set(files))[:200]
    return {"files": files, "export_dir": str(_EXPORT_DIR)}

@app.post("/api/blf/analyze")
def api_blf_analyze(body: dict):
    path = body.get("path", "")
    fp = Path(path).expanduser()
    if not fp.exists():
        raise HTTPException(404, f"File not found: {fp}")
    if fp.suffix.lower() not in (".blf",):
        raise HTTPException(400, f"Unsupported format: {fp.suffix}. Only .blf is supported.")

    try:
        analyzer = TraceAnalyzer(str(fp))
        analysis = analyzer.analyze()
    except Exception as e:
        raise HTTPException(400, f"Analysis failed: {e}")

    bus_mapping_raw = body.get("bus_mapping", {})
    bus_mapping = {int(k): v for k, v in bus_mapping_raw.items()}
    message_names_raw = body.get("message_names", {})
    message_names = {int(k, 0) if k.startswith("0x") else int(k): v for k, v in message_names_raw.items()}
    include_signals = body.get("include_signals", False)

    result: dict[str, Any] = {
        "path": str(fp),
        "file_name": fp.name,
        "file_size": fp.stat().st_size,
        "total_frames": analysis.total_frames,
        "unique_ids": analysis.unique_ids,
        "channels": sorted(analysis.channels),
        "can_ids": [],
        "pdu_db": {},
    }

    for aid in sorted(analysis.can_stats.keys()):
        s = analysis.can_stats[aid]
        cycle_ms = analysis.cycle_times_ms.get(aid, 0)
        max_dlc = max(s.dlc_values) if s.dlc_values else 0
        result["can_ids"].append({
            "can_id": aid,
            "can_id_hex": f"0x{aid:X}",
            "channel": s.channel,
            "count": s.count,
            "max_dlc": max_dlc,
            "is_extended": s.is_extended,
            "is_fd": s.is_fd,
            "cycle_time_ms": cycle_ms,
            "send_type": "Cyclic" if cycle_ms > 0 else "Spontaneous",
        })

    try:
        if include_signals:
            engineer = TraceReverseEngineer(analyzer)
            pdu_db = engineer.to_pdu_db(bus_mapping=bus_mapping, message_names=message_names)
            result["signal_count"] = sum(len(m.get("signals", [])) for m in pdu_db.get("messages", []))
        else:
            pdu_db = analyzer.to_pdu_db(bus_mapping=bus_mapping, message_names=message_names)
            result["signal_count"] = 0
        result["pdu_db"] = pdu_db
    except Exception as e:
        pdu_db = analyzer.to_pdu_db(bus_mapping=bus_mapping, message_names=message_names)
        result["pdu_db"] = pdu_db
        result["signal_error"] = str(e)
        result["signal_count"] = 0

    with _analysis_lock:
        _analysis_cache["last"] = result

    return result

@app.post("/api/blf/export")
def api_blf_export(body: dict):
    with _analysis_lock:
        pdu_db = body.get("pdu_db") or _analysis_cache.get("last", {}).get("pdu_db", {})
    if not pdu_db or not pdu_db.get("messages"):
        raise HTTPException(400, "No analysis data to export")

    name = body.get("name", "trace_analysis")
    fp = _CONFIG_DIR / f"{name}.json"
    fp.write_text(json.dumps(pdu_db, indent=2))
    return {"status": "ok", "path": str(fp), "message_count": len(pdu_db.get("messages", []))}

@app.get("/api/db/list")
def api_db_list():
    files = sorted(f.name for f in _CONFIG_DIR.glob("*.json") if f.is_file())
    return {"files": files, "config_dir": str(_CONFIG_DIR)}

# ── HTML ────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — Trace Analyzer</title>
<style>
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
  --mono:   "SFMono-Regular",Consolas,"Liberation Mono",monospace;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; font-size:14px; }
header {
  height:46px; background:var(--panel); border-bottom:1px solid var(--border);
  display:flex; align-items:center; padding:0 16px; gap:12px;
}
.logo { font-weight:700; color:var(--blue); font-size:16px; }
.subtitle { color:var(--muted); font-size:13px; }
.spacer { flex:1; }
.gw-badge { font-size:11px; color:var(--green); font-family:var(--mono); padding:2px 8px; border:1px solid var(--border); border-radius:4px; }
#panel-nav {
  height:32px; background:#0d1117; border-bottom:1px solid var(--border);
  display:flex; align-items:center; padding:0 16px; gap:8px;
}
#panel-nav .nav-link { color:var(--muted); font-size:12px; text-decoration:none; padding:4px 10px; border-radius:4px; }
#panel-nav .nav-link:hover { color:var(--text); background:var(--panel); }
#panel-nav .nav-link.active { color:var(--blue); background:rgba(88,166,255,0.1); }
.layout { display:flex; height:calc(100vh - 78px); }
.sidebar {
  width:360px; min-width:360px; background:var(--panel); border-right:1px solid var(--border);
  display:flex; flex-direction:column; overflow:hidden;
}
.sidebar-toolbar { padding:8px; display:flex; gap:4px; border-bottom:1px solid var(--border); flex-wrap:wrap; }
.sidebar-toolbar button, button.btn { padding:5px 10px; border:1px solid var(--border); border-radius:4px; background:var(--bg); color:var(--text); cursor:pointer; font-size:12px; }
.sidebar-toolbar button:hover, button.btn:hover { background:var(--panel); }
.btn-primary { color:var(--blue) !important; border-color:var(--blue) !important; }
.btn-primary:hover { background:rgba(88,166,255,0.1) !important; }
.btn-add { color:var(--green) !important; border-color:var(--green) !important; }
.btn-add:hover { background:rgba(63,185,80,0.1) !important; }
.btn-danger { color:var(--red) !important; border-color:var(--red) !important; }
.main { flex:1; overflow-y:auto; padding:16px; }
.pane { max-width:960px; margin:0 auto; }
h2 { font-size:16px; font-weight:600; margin:0 0 12px; }
h3 { font-size:14px; font-weight:600; margin:16px 0 8px; color:var(--text); }
.field { margin-bottom:8px; }
.field label { display:block; font-size:11px; color:var(--muted); margin-bottom:2px; }
.field input, .field select, .field textarea {
  width:100%; padding:5px 8px; background:var(--bg); border:1px solid var(--border); border-radius:4px; color:var(--text); font-size:13px; font-family:var(--mono);
}
.field input:focus, .field select:focus { border-color:var(--blue); outline:none; }
.field-row { display:flex; gap:8px; }
.field-row .field { flex:1; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th { text-align:left; padding:6px 8px; border-bottom:2px solid var(--border); color:var(--muted); font-weight:600; font-size:11px; position:sticky; top:0; background:var(--bg); white-space:nowrap; }
td { padding:5px 8px; border-bottom:1px solid var(--border); font-family:var(--mono); font-size:11px; }
tr:hover td { background:rgba(88,166,255,0.03); }
.stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:8px; margin-bottom:16px; }
.stat-card { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:12px; text-align:center; }
.stat-card .value { font-size:24px; font-weight:700; color:var(--blue); font-family:var(--mono); }
.stat-card .label { font-size:11px; color:var(--muted); margin-top:2px; }
.config-panel { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:12px; margin-bottom:16px; }
.config-panel h3 { margin-top:0; }
.mapping-row { display:flex; gap:8px; align-items:center; margin-bottom:4px; }
.mapping-row input { flex:1; padding:3px 6px; background:var(--panel); border:1px solid var(--border); border-radius:3px; color:var(--text); font-family:var(--mono); font-size:12px; }
.mapping-row button { padding:2px 6px; font-size:11px; }
.empty-state { text-align:center; padding:60px 20px; color:var(--muted); }
.empty-state h2 { font-size:20px; margin-bottom:8px; }
.empty-state p { font-size:14px; margin-bottom:16px; }
.switch { position:relative; display:inline-block; width:36px; height:20px; }
.switch input { opacity:0; width:0; height:0; }
.slider { position:absolute; cursor:pointer; top:0; left:0; right:0; bottom:0; background-color:var(--border); transition:.3s; border-radius:10px; }
.slider:before { position:absolute; content:""; height:14px; width:14px; left:3px; bottom:3px; background-color:var(--text); transition:.3s; border-radius:50%; }
input:checked + .slider { background-color:var(--blue); }
input:checked + .slider:before { transform:translateX(16px); }
.toast {
  position:fixed; bottom:20px; right:20px; padding:10px 20px; border-radius:6px; font-size:13px;
  z-index:9999; animation:fadeIn 0.2s;
}
.toast.info { background:var(--blue); color:#fff; }
.toast.error { background:var(--red); color:#fff; }
.toast.success { background:var(--green); color:#fff; }
@keyframes fadeIn { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
#progress { display:none; text-align:center; padding:20px; }
#progress .spinner { display:inline-block; width:24px; height:24px; border:3px solid var(--border); border-top-color:var(--blue); border-radius:50%; animation:spin 0.8s linear infinite; }
@keyframes spin { to{transform:rotate(360deg)} }
::-webkit-scrollbar { width:4px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--border); border-radius:2px; }
.badge { display:inline-block; padding:1px 5px; border-radius:3px; font-size:10px; font-weight:600; }
.badge-cyclic { background:rgba(63,185,80,0.15); color:var(--green); }
.badge-spont { background:rgba(210,153,34,0.15); color:var(--yellow); }
</style>
</head>
<body>

<header>
  <span class="logo">⛵ BoAt</span>
  <span class="subtitle">Trace Analyzer</span>
  <span class="spacer"></span>
</header>

<nav id="panel-nav">
  <a class="nav-link" data-port="8086">Launcher</a>
  <a class="nav-link" data-port="8080">Dashboard</a>
  <a class="nav-link" data-port="8081">Nodes</a>
  <a class="nav-link" data-port="8082">Commander</a>
  <a class="nav-link" data-port="8083">Recorder</a>
  <a class="nav-link" data-port="8087">PDU Editor</a>
  <a class="nav-link" data-port="8088" style="color:var(--blue)">Trace Analyzer</a>
</nav>

<div class="layout">
  <div class="sidebar">
    <div class="sidebar-toolbar">
      <input id="file-path" type="text" placeholder="/path/to/trace.blf" style="flex:1;padding:4px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px"/>
      <button class="btn-primary" onclick="browseFile()">Browse</button>
      <button class="btn-add" onclick="analyze()">Analyze</button>
    </div>
    <div class="sidebar-search" style="padding:6px 8px;border-bottom:1px solid var(--border)">
      <label style="font-size:11px;color:var(--muted);display:flex;align-items:center;gap:6px">
        <span>Reverse-engineer signals</span>
        <label class="switch"><input type="checkbox" id="include-signals" checked/><span class="slider"></span></label>
      </label>
    </div>
    <div class="sidebar-list" id="config-panel" style="flex:1;overflow-y:auto;padding:8px">
      <div class="config-panel" id="mapping-panel" style="display:none">
        <h3>Bus Mapping</h3>
        <div id="bus-mappings"></div>
        <h3 style="margin-top:12px">Message Names</h3>
        <div id="msg-name-mappings"></div>
      </div>
    </div>
  </div>

  <div class="main" id="main-content">
    <div class="empty-state" id="empty-state">
      <h2>No trace file analyzed</h2>
      <p>Enter a path to a .blf file and click Analyze, or browse for files on the system.</p>
    </div>
    <div id="results" style="display:none">
      <div class="stat-grid" id="stats-bar"></div>
      <div class="pane" id="results-table"></div>
    </div>
    <div id="progress"><div class="spinner"></div><p style="margin-top:8px;color:var(--muted)">Analyzing trace file...</p></div>
  </div>
</div>

<div id="toast-container"></div>

<script>
let lastResult = null;

function toast(msg, type="info") {
  const el = document.createElement("div");
  el.className = "toast " + type; el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

async function api(method, url, body) {
  const opts = {method, headers:{"Accept":"application/json"}};
  if (body !== undefined) {opts.headers["Content-Type"]="application/json"; opts.body=JSON.stringify(body);}
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function browseFile() {
  const r = await api("GET","/api/blf/list");
  const files = r.files;
  if (!files.length) { toast("No .blf files found on the system","error"); return; }
  const fp = prompt("Enter path or paste from known files:\n\n" + files.slice(0,30).join("\n"));
  if (!fp) return;
  document.getElementById("file-path").value = fp;
}

async function analyze() {
  const path = document.getElementById("file-path").value.trim();
  if (!path) { toast("Enter a path to a .blf file","error"); return; }

  document.getElementById("empty-state").style.display = "none";
  document.getElementById("results").style.display = "none";
  document.getElementById("progress").style.display = "block";

  const includeSignals = document.getElementById("include-signals").checked;
  let busMappings = {};
  let nameMappings = {};

  if (lastResult && lastResult.can_ids) {
    document.querySelectorAll("#bus-mappings .mapping-row").forEach(row => {
      const chan = row.querySelector(".chan-input").value;
      const bus = row.querySelector(".bus-input").value;
      if (chan && bus) busMappings[chan] = bus;
    });
    document.querySelectorAll("#msg-name-mappings .mapping-row").forEach(row => {
      const id = row.querySelector(".id-input").value;
      const name = row.querySelector(".name-input").value;
      if (id && name) nameMappings[id] = name;
    });
  }

  try {
    lastResult = await api("POST","/api/blf/analyze", {
      path, include_signals: includeSignals,
      bus_mapping: busMappings,
      message_names: nameMappings,
    });
    renderResults(lastResult);
    document.getElementById("progress").style.display = "none";
    document.getElementById("results").style.display = "block";
    toast(`Analyzed ${lastResult.file_name}: ${lastResult.total_frames} frames, ${lastResult.unique_ids} CAN IDs`,"success");
  } catch(e) {
    document.getElementById("progress").style.display = "none";
    document.getElementById("empty-state").style.display = "block";
    toast("Analysis failed: " + e.message,"error");
  }
}

function renderResults(result) {
  const stats = document.getElementById("stats-bar");
  stats.innerHTML = `
    <div class="stat-card"><div class="value">${result.total_frames.toLocaleString()}</div><div class="label">Total Frames</div></div>
    <div class="stat-card"><div class="value">${result.unique_ids}</div><div class="label">Unique CAN IDs</div></div>
    <div class="stat-card"><div class="value">${result.channels.join(", ")}</div><div class="label">Channels</div></div>
    <div class="stat-card"><div class="value">${result.signal_count}</div><div class="label">Discovered Signals</div></div>
    <div class="stat-card"><div class="value">${(result.file_size / 1024).toFixed(0)} KB</div><div class="label">File Size</div></div>
  `;

  const table = document.getElementById("results-table");
  const ids = result.can_ids || [];
  const btns = `<div style="margin-bottom:12px;display:flex;gap:6px">
    <button class="btn btn-add" onclick="exportDb()">📥 Export to PDU DB</button>
    <button class="btn btn-primary" onclick="toggleConfig()">⚙ Configure Mapping</button>
  </div>`;

  table.innerHTML = btns + `
    <table><thead><tr>
      <th>CAN ID</th><th>Ch</th><th>Count</th><th>DLC</th><th>Type</th><th>Cycle</th><th>SendType</th>
    </tr></thead><tbody>
    ${ids.map(d => `<tr>
      <td><strong>${d.can_id_hex}</strong> (${d.can_id})</td>
      <td>${d.channel}</td>
      <td>${d.count.toLocaleString()}</td>
      <td>${d.max_dlc}</td>
      <td>${d.is_fd ? "CANFD" : "CAN"} ${d.is_extended ? "· Ext" : ""}</td>
      <td>${d.cycle_time_ms ? d.cycle_time_ms.toFixed(1) + " ms" : "—"}</td>
      <td><span class="badge ${d.send_type === 'Cyclic' ? 'badge-cyclic' : 'badge-spont'}">${d.send_type}</span></td>
    </tr>`).join("")}
    </tbody></table>
  `;

  const panel = document.getElementById("mapping-panel");
  panel.style.display = "none";
  const busDiv = document.getElementById("bus-mappings");
  const nameDiv = document.getElementById("msg-name-mappings");

  const channels = [...new Set(ids.map(d => d.channel))].sort();
  busDiv.innerHTML = channels.map(ch => `
    <div class="mapping-row">
      <span style="font-family:var(--mono);font-size:12px;color:var(--muted);width:50px">Ch ${ch}</span>
      <input class="chan-input" type="hidden" value="${ch}"/>
      <input class="bus-input" type="text" value="CAN_${ch}" placeholder="bus name" onchange="markDirty()"/>
    </div>
  `).join("");

  const canIds = [...new Set(ids.map(d => d.can_id))].sort((a,b) => a-b);
  nameDiv.innerHTML = canIds.slice(0,50).map(id => `
    <div class="mapping-row">
      <span style="font-family:var(--mono);font-size:12px;color:var(--muted);width:70px">0x${id.toString(16).toUpperCase()}</span>
      <input class="id-input" type="hidden" value="0x${id.toString(16).toUpperCase()}"/>
      <input class="name-input" type="text" value="Msg_0x${id.toString(16).toUpperCase()}" placeholder="message name"/>
    </div>
  `).join("");
}

function toggleConfig() {
  const panel = document.getElementById("mapping-panel");
  panel.style.display = panel.style.display === "none" ? "block" : "none";
}

function markDirty() {}

async function exportDb() {
  if (!lastResult || !lastResult.pdu_db) {
    toast("No analysis data to export","error");
    return;
  }

  let busMappings = {};
  let nameMappings = {};
  document.querySelectorAll("#bus-mappings .mapping-row").forEach(row => {
    const chan = row.querySelector(".chan-input").value;
    const bus = row.querySelector(".bus-input").value;
    if (chan && bus) busMappings[chan] = bus;
  });
  document.querySelectorAll("#msg-name-mappings .mapping-row").forEach(row => {
    const id = row.querySelector(".id-input").value;
    const name = row.querySelector(".name-input").value;
    if (id && name) nameMappings[id] = name;
  });

  const includeSignals = document.getElementById("include-signals").checked;

  try {
    const r = await api("POST","/api/blf/analyze", {
      path: lastResult.path,
      include_signals: includeSignals,
      bus_mapping: busMappings,
      message_names: nameMappings,
    });

    const exportResult = await api("POST","/api/blf/export", {
      pdu_db: r.pdu_db,
      name: lastResult.file_name.replace(/\.[^.]+$/, "") + "_pdu_db",
    });
    toast(`Exported ${exportResult.message_count} messages to ${exportResult.path}`,"success");

    lastResult = r;
    renderResults(lastResult);
  } catch(e) {
    toast("Export failed: " + e.message,"error");
  }
}

(function() {
  const h = window.location.hostname, p = window.location.port;
  document.querySelectorAll('.nav-link').forEach(a => {
    a.href = 'http://' + h + ':' + a.dataset.port + '/';
    if (a.dataset.port === p) a.classList.add('active');
  });
})();
</script>
</body>
</html>
"""

# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"BoAt Trace Analyzer → http://localhost:{_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
