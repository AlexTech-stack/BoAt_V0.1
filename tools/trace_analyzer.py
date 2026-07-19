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
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from boat.trace_analyzer import TraceAnalyzer
from boat.trace_reverse_engineer import TraceReverseEngineer, _HAS_NUMPY, guess_e2e_profile

_PORT = int(os.environ.get("BOAT_TRACE_ANALYZER_PORT", "8088"))
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "boat-platform" / "config"
# The Trace Editor's own default save/scan location -- also used as the
# target of the "Convert & Send to Trace Editor" action below, so a
# converted .trace file shows up there with zero Trace Editor changes.
_EXPORT_DIR = Path(__file__).resolve().parent.parent / "traces"
_EXPORT_DIR.mkdir(exist_ok=True)
# Where recordings actually land in practice (BOAT_HIL/recorder output).
_RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "boat-platform" / "traces"

app = FastAPI()

# Staged-analysis state for whatever file was last loaded via stage 1 --
# holds the live TraceAnalyzer/TraceReverseEngineer instances (not just
# their JSON-serializable results) so stage 2/3 calls can build on stage 1's
# work without re-reading/re-parsing the trace file. Loading a *different*
# path resets everything (see api_blf_analyze) -- stage 2/3 results only
# ever mean something for the file stage 1 was last run against.
_stage_cache: dict[str, Any] = {}
_stage_lock = threading.Lock()

# ── API routes ──────────────────────────────────────────────────────────────

@app.get("/api/blf/list")
def api_blf_list():
    files = []
    for d in [_RECORDINGS_DIR, _EXPORT_DIR, _CONFIG_DIR, Path("/tmp"), Path.home(), Path.home() / "traces"]:
        try:
            for pattern in ("*.blf", "*.asc", "*.trace", "*.pcapng"):
                for f in Path(d).glob(pattern):
                    files.append(str(f))
        except Exception:
            pass
    files = sorted(set(files))[:200]
    return {"files": files, "export_dir": str(_EXPORT_DIR)}

_SUPPORTED_SUFFIXES = (".blf", ".asc", ".trace", ".pcapng")

def _signal_preview(sig) -> dict[str, Any]:
    """UI-only preview of a discovered signal -- never written to the
    exported PDU DB, which follows the documented config schema."""
    return {
        "name": sig.name,
        "value_type": sig.value_type,
        "confidence": round(sig.confidence, 2),
        "is_counter": sig.is_counter,
        "is_checksum": sig.is_checksum,
        "crc_algorithm": sig.crc_algorithm,
        "physical_values": sig.physical_values[:50],
    }

def _signals_preview_by_id(signals_by_id: dict[int, list]) -> dict[str, list]:
    return {str(aid): [_signal_preview(sig) for sig in sigs] for aid, sigs in signals_by_id.items()}

@app.post("/api/blf/analyze")
def api_blf_analyze(body: dict):
    """Stage 1: read the file, resolve multi-channel duplicates, detect
    cycle times. Fast (no signal reverse-engineering) -- caches the live
    TraceAnalyzer/TraceAnalysis so stage 2/3 calls can build on this
    without re-reading the file. Loading a different path resets any
    previously-cached stage 2/3 results, since they only mean something for
    the file they were computed against."""
    path = body.get("path", "")
    fp = Path(path).expanduser()
    if not fp.exists():
        raise HTTPException(404, f"File not found: {fp}")
    if fp.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise HTTPException(400, f"Unsupported format: {fp.suffix}. Supported: .blf, .asc, .trace, .pcapng")

    bus_mapping_raw = body.get("bus_mapping", {})
    bus_mapping = {int(k): v for k, v in bus_mapping_raw.items()}
    message_names_raw = body.get("message_names", {})
    message_names = {int(k, 0) if k.startswith("0x") else int(k): v for k, v in message_names_raw.items()}

    t0 = time.perf_counter()
    try:
        analyzer = TraceAnalyzer(str(fp))
        analysis = analyzer.analyze()
    except Exception as e:
        raise HTTPException(400, f"Analysis failed: {e}")
    elapsed = time.perf_counter() - t0

    with _stage_lock:
        _stage_cache.clear()
        _stage_cache.update({
            "path": str(fp),
            "analyzer": analyzer,
            "analysis": analysis,
            "engineer": None,
            "counters_by_id": None,
            "crcs_by_id": None,
            "app_signals_by_id": None,
            "bus_mapping": bus_mapping,
            "message_names": message_names,
        })

    result: dict[str, Any] = {
        "path": str(fp),
        "file_name": fp.name,
        "file_size": fp.stat().st_size,
        "total_frames": analysis.total_frames,
        "unique_ids": analysis.unique_ids,
        "channels": sorted(analysis.channels),
        "can_ids": [],
        "warnings": list(analysis.errors),
        "elapsed_s": round(elapsed, 2),
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
            "duplicate_channels": s.duplicate_channels,
        })

    return result

def _require_stage1(fp: Path) -> tuple[TraceAnalyzer, TraceReverseEngineer]:
    with _stage_lock:
        if _stage_cache.get("path") != str(fp):
            raise HTTPException(400, "Run Stage 1 (Identify Messages) for this file first")
        analyzer = _stage_cache["analyzer"]
        engineer = _stage_cache.get("engineer") or TraceReverseEngineer(analyzer)
        _stage_cache["engineer"] = engineer
    return analyzer, engineer

@app.post("/api/blf/stage/counters")
def api_stage_counters(body: dict):
    """Stage 2: dedicated AUTOSAR counter scan (4/8/32-bit) followed by a
    CRC scan anchored on each counter found (AUTOSAR keeps a message's
    counter and CRC fields adjacent) -- together identifying, where
    possible, the AUTOSAR E2E profile in use. Independent of Stage 3 --
    can run before it, or be skipped entirely."""
    fp = Path(body.get("path", "")).expanduser()
    _analyzer, engineer = _require_stage1(fp)

    t0 = time.perf_counter()
    counters_by_id = engineer.find_counters()
    crcs_by_id = engineer.find_crcs(counters_by_id)
    elapsed = time.perf_counter() - t0

    with _stage_lock:
        _stage_cache["counters_by_id"] = counters_by_id
        _stage_cache["crcs_by_id"] = crcs_by_id

    merged = {
        aid: counters_by_id.get(aid, []) + crcs_by_id.get(aid, [])
        for aid in set(counters_by_id) | set(crcs_by_id)
    }
    e2e_profiles = {}
    for aid, counters in counters_by_id.items():
        crcs = crcs_by_id.get(aid)
        if counters and crcs:
            profile = guess_e2e_profile(counters[0], crcs[0])
            if profile:
                e2e_profiles[str(aid)] = profile

    return {
        "elapsed_s": round(elapsed, 2),
        "counter_count": sum(len(v) for v in counters_by_id.values()),
        "crc_count": sum(len(v) for v in crcs_by_id.values()),
        "discovered_signals": _signals_preview_by_id(merged),
        "e2e_profiles": e2e_profiles,
        "numpy_available": _HAS_NUMPY,
    }

@app.post("/api/blf/stage/signals")
def api_stage_signals(body: dict):
    """Stage 3: generic bit-correlation clustering for application signals,
    across every CAN ID found by Stage 1. Uses Stage 2's counters and CRCs
    (if it was run) to exclude their bits from clustering; runs without
    them (clustering every bit) otherwise."""
    fp = Path(body.get("path", "")).expanduser()
    _analyzer, engineer = _require_stage1(fp)
    with _stage_lock:
        counters_by_id = _stage_cache.get("counters_by_id")
        crcs_by_id = _stage_cache.get("crcs_by_id")

    t0 = time.perf_counter()
    app_signals_by_id = engineer.find_application_signals(counters_by_id, crcs_by_id)
    elapsed = time.perf_counter() - t0

    with _stage_lock:
        _stage_cache["app_signals_by_id"] = app_signals_by_id

    return {
        "elapsed_s": round(elapsed, 2),
        "signal_count": sum(len(v) for v in app_signals_by_id.values()),
        "discovered_signals": _signals_preview_by_id(app_signals_by_id),
        "numpy_available": _HAS_NUMPY,
        "ran_without_counters": counters_by_id is None,
    }

@app.post("/api/blf/export")
def api_blf_export(body: dict):
    """Builds the PDU DB straight from whatever's currently cached -- Stage 1
    alone exports fine (no signals); Stage 2/3 results, if present, are
    merged in via combine_results(). Never re-runs analysis -- bus_mapping/
    message_names come fresh from the request (the user may have edited the
    Configure Mapping panel after the stages ran), everything else is reused
    as-is from the cache."""
    with _stage_lock:
        analyzer = _stage_cache.get("analyzer")
        analysis = _stage_cache.get("analysis")
        engineer = _stage_cache.get("engineer")
        counters_by_id = _stage_cache.get("counters_by_id")
        crcs_by_id = _stage_cache.get("crcs_by_id")
        app_signals_by_id = _stage_cache.get("app_signals_by_id")

    if analyzer is None or analysis is None:
        raise HTTPException(400, "No analysis data to export -- run Stage 1 first")

    bus_mapping_raw = body.get("bus_mapping", {})
    bus_mapping = {int(k): v for k, v in bus_mapping_raw.items()}
    message_names_raw = body.get("message_names", {})
    message_names = {int(k, 0) if k.startswith("0x") else int(k): v for k, v in message_names_raw.items()}

    if counters_by_id is not None or crcs_by_id is not None or app_signals_by_id is not None:
        engineer = engineer or TraceReverseEngineer(analyzer)
        combined = engineer.combine_results(counters_by_id, app_signals_by_id, crcs_by_id)
        pdu_db = engineer.to_pdu_db(bus_mapping=bus_mapping, message_names=message_names, result=combined)
    else:
        pdu_db = analyzer.to_pdu_db(bus_mapping=bus_mapping, message_names=message_names)

    if not pdu_db.get("messages"):
        raise HTTPException(400, "No analysis data to export")

    name = body.get("name", "trace_analysis")
    fp = _CONFIG_DIR / f"{name}.json"
    fp.write_text(json.dumps(pdu_db, indent=2))
    return {"status": "ok", "path": str(fp), "message_count": len(pdu_db.get("messages", []))}

@app.post("/api/blf/convert")
def api_blf_convert(body: dict):
    """Convert an analyzed .blf/.asc/.trace/.pcapng file into the Trace
    Editor's binary trace format and drop it in _EXPORT_DIR (the Trace
    Editor's own default save/scan location), so it shows up there with no
    Trace Editor changes needed. Reuses the exact conversion `boat replay
    import` uses."""
    from boat.trace_replay import TraceReplayer, TraceReplayError

    path = body.get("path", "")
    fp = Path(path).expanduser()
    if not fp.exists():
        raise HTTPException(404, f"File not found: {fp}")
    if fp.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise HTTPException(400, f"Unsupported format: {fp.suffix}. Supported: .blf, .asc, .trace, .pcapng")
    if fp.suffix.lower() == ".trace":
        raise HTTPException(400, "File is already in the Trace Editor's binary format")

    try:
        binary_data = TraceReplayer().convert_to_binary(fp)
    except TraceReplayError as e:
        raise HTTPException(400, f"Conversion failed: {e}")

    out_name = body.get("name") or fp.stem
    out_path = _EXPORT_DIR / f"{out_name}.trace"
    out_path.write_bytes(binary_data)
    return {"status": "ok", "path": str(out_path)}

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
#toast-container {
  position:fixed; bottom:20px; right:20px; z-index:9999;
  display:flex; flex-direction:column-reverse; gap:8px; align-items:flex-end;
}
.toast {
  padding:10px 20px; border-radius:6px; font-size:13px; max-width:420px;
  animation:fadeIn 0.2s;
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
  <a class="nav-link" data-port="8089">Trace Editor</a>
  <a class="nav-link" data-port="8088" style="color:var(--blue)">Trace Analyzer</a>
  <a class="nav-link" data-port="8090">Eth Analyzer</a>
  <a class="nav-link" data-port="8087">PDU Editor</a>
</nav>

<div class="layout">
  <div class="sidebar">
    <div class="sidebar-toolbar">
      <input id="file-path" type="text" placeholder="/path/to/trace.blf|.asc|.trace|.pcapng" style="flex:1;padding:4px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);font-size:12px"/>
      <button class="btn-primary" onclick="browseFile()">Browse</button>
    </div>
    <div class="sidebar-search" style="padding:8px;border-bottom:1px solid var(--border);display:flex;flex-direction:column;gap:6px">
      <button class="btn btn-add" id="stage1-btn" onclick="runStage1()" style="width:100%">1. Identify Messages</button>
      <button class="btn btn-primary" id="stage2-btn" onclick="runStage2()" disabled style="width:100%">2. Find AUTOSAR Counters &amp; CRCs</button>
      <button class="btn btn-primary" id="stage3-btn" onclick="runStage3()" disabled style="width:100%">3. Discover Application Signals</button>
      <button class="btn" id="stage4-btn" disabled title="Not yet implemented -- detecting when one message's data is derived from/relayed into a different CAN ID" style="width:100%;opacity:0.5;cursor:not-allowed">4. Routing Relationships</button>
      <div id="stage-progress" style="display:none;align-items:center;gap:6px;font-size:11px;color:var(--muted)">
        <div class="spinner" style="width:12px;height:12px;border-width:2px;display:inline-block;border:2px solid var(--border);border-top-color:var(--blue);border-radius:50%;animation:spin 0.8s linear infinite"></div>
        <span id="stage-progress-text"></span>
      </div>
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
      <p>Enter a path to a .blf/.asc/.trace/.pcapng file and click Analyze, or browse for files on the system.</p>
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
let lastResult = null;       // Stage 1 result: can_ids, total_frames, etc.
let discoveredSignals = {};  // accumulated Stage 2 + Stage 3 signals, keyed by CAN ID string

function toast(msg, type="info") {
  const el = document.createElement("div");
  el.className = "toast " + type; el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  // Longer messages get more time to read; multiple toasts stack in the
  // container (column-reverse) instead of overlapping at the same spot.
  const duration = Math.min(8000, Math.max(3000, msg.length * 60));
  setTimeout(() => el.remove(), duration);
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
  if (!files.length) { toast("No .blf/.asc/.trace/.pcapng files found on the system","error"); return; }
  const fp = prompt("Enter path or paste from known files:\n\n" + files.slice(0,30).join("\n"));
  if (!fp) return;
  document.getElementById("file-path").value = fp;
}

function setStageButtonsEnabled(s1, s2, s3) {
  document.getElementById("stage1-btn").disabled = !s1;
  document.getElementById("stage2-btn").disabled = !s2;
  document.getElementById("stage3-btn").disabled = !s3;
}

function showStageProgress(text) {
  document.getElementById("stage-progress-text").textContent = text;
  document.getElementById("stage-progress").style.display = "flex";
}

function hideStageProgress() {
  document.getElementById("stage-progress").style.display = "none";
}

function numpyHint(numpyAvailable) {
  if (numpyAvailable === false) {
    toast("numpy not installed — used the slower pure-Python fallback. Install with: pip install -e ./boat-platform/sdk/python[analysis]","info");
  }
}

// Stage 1: read the file, resolve multi-channel duplicates, detect cycle
// times. Fast -- no signal reverse-engineering. Resets any previously
// discovered Stage 2/3 signals, since those only mean something for the
// file Stage 1 was just run against.
async function runStage1() {
  const path = document.getElementById("file-path").value.trim();
  if (!path) { toast("Enter a path to a .blf/.asc/.trace/.pcapng file","error"); return; }

  document.getElementById("empty-state").style.display = "none";
  document.getElementById("results").style.display = "none";
  document.getElementById("progress").style.display = "block";
  setStageButtonsEnabled(false, false, false);

  let busMappings = {};
  let nameMappings = {};
  if (lastResult && lastResult.can_ids) {
    // preserve any hand-edited mappings across a re-run of Stage 1 on the same file
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
    lastResult = await api("POST","/api/blf/analyze", {path, bus_mapping: busMappings, message_names: nameMappings});
    discoveredSignals = {};
    e2eProfiles = {};
    renderMappingPanel();
    renderResults();
    document.getElementById("progress").style.display = "none";
    document.getElementById("results").style.display = "block";
    toast(`Stage 1 done in ${lastResult.elapsed_s}s: ${lastResult.file_name} — ${lastResult.total_frames} frames, ${lastResult.unique_ids} CAN IDs`,"success");
    (lastResult.warnings || []).forEach(w => toast(w, "info"));
    setStageButtonsEnabled(true, true, true);
  } catch(e) {
    document.getElementById("progress").style.display = "none";
    document.getElementById("empty-state").style.display = "block";
    toast("Stage 1 failed: " + e.message,"error");
    setStageButtonsEnabled(true, false, false);
  }
}

function mergeDiscoveredSignals(newSignals) {
  for (const [canId, sigs] of Object.entries(newSignals || {})) {
    discoveredSignals[canId] = (discoveredSignals[canId] || []).concat(sigs);
  }
}

// Stage 2: dedicated AUTOSAR counter scan, plus a CRC scan anchored on
// each counter found (and, when both are found, an E2E profile hint).
// Independent of Stage 3 -- can run before it or be skipped entirely.
let e2eProfiles = {};
async function runStage2() {
  if (!lastResult) { toast("Run Stage 1 first","error"); return; }
  setStageButtonsEnabled(false, false, false);
  showStageProgress("Stage 2: scanning for AUTOSAR counters & CRCs…");
  try {
    const r = await api("POST","/api/blf/stage/counters", {path: lastResult.path});
    mergeDiscoveredSignals(r.discovered_signals);
    e2eProfiles = r.e2e_profiles || {};
    renderResults();
    toast(`Stage 2 done in ${r.elapsed_s}s: ${r.counter_count} counter(s), ${r.crc_count} CRC(s) found`,"success");
    numpyHint(r.numpy_available);
  } catch(e) {
    toast("Stage 2 failed: " + e.message,"error");
  } finally {
    hideStageProgress();
    setStageButtonsEnabled(true, true, true);
  }
}

// Stage 3: generic bit-correlation clustering for application signals.
// Uses Stage 2's counters (if run) to exclude their bits from clustering.
async function runStage3() {
  if (!lastResult) { toast("Run Stage 1 first","error"); return; }
  setStageButtonsEnabled(false, false, false);
  showStageProgress("Stage 3: clustering application signals…");
  try {
    const r = await api("POST","/api/blf/stage/signals", {path: lastResult.path});
    mergeDiscoveredSignals(r.discovered_signals);
    renderResults();
    toast(`Stage 3 done in ${r.elapsed_s}s: ${r.signal_count} signal(s) found`,"success");
    if (r.ran_without_counters) {
      toast("Stage 2 (counters) hasn't run yet — clustering ran over every bit, which can split a counter into several small signals. Run Stage 2 first for cleaner results.","info");
    }
    numpyHint(r.numpy_available);
  } catch(e) {
    toast("Stage 3 failed: " + e.message,"error");
  } finally {
    hideStageProgress();
    setStageButtonsEnabled(true, true, true);
  }
}

// Renders the stats bar + CAN ID table from the current lastResult (Stage 1)
// and discoveredSignals (Stage 2/3, accumulated) globals. Deliberately does
// NOT touch the Configure Mapping panel's inputs -- see renderMappingPanel(),
// called only from runStage1() -- so re-rendering after Stage 2/3 never
// wipes out mapping edits the user made in between.
function renderResults() {
  const result = lastResult;
  if (!result) return;
  const totalSignals = Object.values(discoveredSignals).reduce((sum, sigs) => sum + sigs.length, 0);

  const stats = document.getElementById("stats-bar");
  stats.innerHTML = `
    <div class="stat-card"><div class="value">${result.total_frames.toLocaleString()}</div><div class="label">Total Frames</div></div>
    <div class="stat-card"><div class="value">${result.unique_ids}</div><div class="label">Unique CAN IDs</div></div>
    <div class="stat-card"><div class="value">${result.channels.join(", ")}</div><div class="label">Channels</div></div>
    <div class="stat-card"><div class="value">${totalSignals}</div><div class="label">Discovered Signals</div></div>
    <div class="stat-card"><div class="value">${(result.file_size / 1024).toFixed(0)} KB</div><div class="label">File Size</div></div>
  `;

  const table = document.getElementById("results-table");
  const ids = result.can_ids || [];
  const btns = `<div style="margin-bottom:12px;display:flex;gap:6px">
    <button class="btn btn-add" onclick="exportDb()">📥 Export to PDU DB</button>
    <button class="btn btn-primary" onclick="toggleConfig()">⚙ Configure Mapping</button>
    <button class="btn btn-primary" onclick="convertForTraceEditor()">↪ Convert & Send to Trace Editor</button>
  </div>`;

  const discovered = discoveredSignals;

  table.innerHTML = btns + `
    <table><thead><tr>
      <th></th><th>CAN ID</th><th>Ch</th><th>Count</th><th>DLC</th><th>Type</th><th>Cycle</th><th>SendType</th>
    </tr></thead><tbody>
    ${ids.map(d => {
      const sigs = discovered[String(d.can_id)] || [];
      const rowId = `sigrow-${d.can_id}`;
      const toggleCell = sigs.length
        ? `<span style="cursor:pointer;color:var(--blue)" id="toggle-${rowId}" onclick="toggleSignalRow('${rowId}')">&#9656;</span>`
        : "";
      const sigBadge = sigs.length
        ? `<span class="badge" style="background:rgba(210,168,255,0.15);color:var(--purple);margin-left:4px">${sigs.length} sig</span>`
        : "";
      const dupChannels = Object.keys(d.duplicate_channels || {});
      const dupTitle = dupChannels.length
        ? "Also seen on " + dupChannels.map(ch => `channel ${ch} (${d.duplicate_channels[ch]} frames)`).join(", ")
          + " -- treated as a relay/duplicate of this channel and ignored for cycle time / signal analysis."
        : "";
      const dupBadge = dupChannels.length
        ? `<span class="badge" style="background:rgba(255,166,87,0.15);color:var(--orange);margin-left:4px" title="${dupTitle}">multi-bus</span>`
        : "";
      const e2eProfile = e2eProfiles[String(d.can_id)];
      const e2eIsUnknown = e2eProfile === "E2E_Unknown";
      const e2eLabel = e2eIsUnknown ? "E2E?" : e2eProfile;
      const e2eTitle = e2eIsUnknown
        ? "A counter and a checksum were both found on this message -- strong evidence of AUTOSAR E2E protection -- but the checksum didn't match any known profile's algorithm, so which profile (if any standard one at all) can't be stated."
        : "Best-effort hint from the matched counter width + CRC algorithm -- not verified against the full E2E protocol spec.";
      const e2eBadge = e2eProfile
        ? `<span class="badge" style="background:rgba(210,168,255,0.15);color:var(--purple);margin-left:4px" title="${e2eTitle}">${e2eLabel}</span>`
        : "";
      const detailRow = sigs.length ? `<tr id="${rowId}" style="display:none"><td></td><td colspan="7" style="padding:8px 8px 12px 24px;background:var(--bg)">
        ${sigs.map(s => `
          <div style="display:flex;align-items:center;gap:10px;padding:4px 0;border-bottom:1px solid var(--border)">
            <span style="font-family:var(--mono);font-size:11px;min-width:100px">${s.name}</span>
            <span class="badge" style="background:rgba(88,166,255,0.15);color:var(--blue)">${s.value_type}</span>
            <span class="badge" style="background:rgba(63,185,80,0.15);color:var(--green)">conf ${s.confidence}</span>
            ${s.is_counter ? `<span class="badge badge-cyclic">counter</span>` : ""}
            ${s.is_checksum ? `<span class="badge badge-spont" title="${s.crc_algorithm ? 'Matched AUTOSAR ' + s.crc_algorithm + ' against every observed frame' : 'No exact AUTOSAR CRC algorithm matched, but it behaves like a checksum (full-range values, changes whenever the rest of the payload does, no natural ceiling) rather than a physical signal'}">${s.crc_algorithm || "checksum"}</span>` : ""}
            ${sparkline(s.physical_values)}
          </div>
        `).join("")}
      </td></tr>` : "";
      return `<tr>
      <td>${toggleCell}</td>
      <td><strong>${d.can_id_hex}</strong> (${d.can_id})</td>
      <td>${d.channel}</td>
      <td>${d.count.toLocaleString()}</td>
      <td>${d.max_dlc}</td>
      <td>${d.is_fd ? "CANFD" : "CAN"} ${d.is_extended ? "· Ext" : ""}</td>
      <td>${d.cycle_time_ms ? d.cycle_time_ms.toFixed(1) + " ms" : "—"}</td>
      <td><span class="badge ${d.send_type === 'Cyclic' ? 'badge-cyclic' : 'badge-spont'}">${d.send_type}</span>${sigBadge}${dupBadge}${e2eBadge}</td>
    </tr>${detailRow}`;
    }).join("")}
    </tbody></table>
  `;
}

// Builds the Configure Mapping panel's inputs from Stage 1's CAN ID list.
// Called only right after Stage 1 completes -- never from renderResults(),
// so it doesn't overwrite mapping values the user typed after Stage 1 when
// Stage 2/3 finish and re-render the table.
function renderMappingPanel() {
  const ids = (lastResult && lastResult.can_ids) || [];
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
  nameDiv.innerHTML = canIds.map(id => `
    <div class="mapping-row">
      <span style="font-family:var(--mono);font-size:12px;color:var(--muted);width:70px">0x${id.toString(16).toUpperCase()}</span>
      <input class="id-input" type="hidden" value="0x${id.toString(16).toUpperCase()}"/>
      <input class="name-input" type="text" value="Msg_0x${id.toString(16).toUpperCase()}" placeholder="message name"/>
    </div>
  `).join("");
}

function sparkline(values) {
  if (!values || values.length < 2) return "";
  const w = 120, h = 24, pad = 2;
  const min = Math.min(...values), max = Math.max(...values);
  const span = (max - min) || 1;
  const step = (w - pad * 2) / (values.length - 1);
  const points = values.map((v, i) => {
    const x = pad + i * step;
    const y = h - pad - ((v - min) / span) * (h - pad * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" style="display:block;flex-shrink:0">
    <polyline points="${points}" fill="none" stroke="var(--blue)" stroke-width="1.5"/>
  </svg>`;
}

function toggleSignalRow(rowId) {
  const row = document.getElementById(rowId);
  const toggle = document.getElementById("toggle-" + rowId);
  const isHidden = row.style.display === "none";
  row.style.display = isHidden ? "table-row" : "none";
  toggle.innerHTML = isHidden ? "&#9662;" : "&#9656;";
}

function toggleConfig() {
  const panel = document.getElementById("mapping-panel");
  panel.style.display = panel.style.display === "none" ? "block" : "none";
}

function markDirty() {}

async function exportDb() {
  if (!lastResult) {
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

  // Builds straight from whatever's cached server-side (Stage 1 alone, or
  // with Stage 2/3 merged in) -- no re-analysis, just the current mapping
  // panel state.
  try {
    const exportResult = await api("POST","/api/blf/export", {
      bus_mapping: busMappings,
      message_names: nameMappings,
      name: lastResult.file_name.replace(/\.[^.]+$/, "") + "_pdu_db",
    });
    toast(`Exported ${exportResult.message_count} messages to ${exportResult.path}`,"success");
  } catch(e) {
    toast("Export failed: " + e.message,"error");
  }
}

async function convertForTraceEditor() {
  if (!lastResult || !lastResult.path) {
    toast("No analysis data to convert","error");
    return;
  }
  if (lastResult.path.toLowerCase().endsWith(".trace")) {
    toast("This file is already in the Trace Editor's binary format","error");
    return;
  }
  try {
    const r = await api("POST","/api/blf/convert", {path: lastResult.path});
    const editorUrl = "http://" + window.location.hostname + ":8089/";
    toast(`Saved ${r.path} -- open it in the Trace Editor's Load dropdown`,"success");
    const link = document.createElement("a");
    link.href = editorUrl; link.target = "_blank";
    link.textContent = "Open Trace Editor →";
    link.style.cssText = "position:fixed;bottom:60px;right:20px;padding:8px 14px;background:var(--blue);color:#fff;border-radius:6px;font-size:13px;text-decoration:none;z-index:9999";
    document.body.appendChild(link);
    setTimeout(() => link.remove(), 8000);
  } catch(e) {
    toast("Convert failed: " + e.message,"error");
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
def index(path: Optional[str] = Query(None)):
    html = HTML
    if path:
        # Cross-link from the Trace Editor ("Analyze in Trace Analyzer"):
        # pre-fill the path field and run Stage 1 automatically.
        # json.dumps for safe JS string escaping, not string interpolation.
        autorun = (
            "<script>window.addEventListener(\"DOMContentLoaded\",function(){"
            f"document.getElementById(\"file-path\").value={json.dumps(path)};"
            "runStage1();});</script>"
        )
        html = html.replace("</body>", autorun + "</body>")
    return HTMLResponse(html)


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"BoAt Trace Analyzer → http://localhost:{_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
