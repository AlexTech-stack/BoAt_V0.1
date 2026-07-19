"""
BoAt Platform — Ethernet Trace Analyzer
Read .pcap/.pcapng captures, identify protocols (VLAN/EtherType, DoIP, SOME/IP),
reconstruct TCP sessions, and classify UDP flows as cyclic vs event-driven.
Run:  python3 tools/eth_trace_analyzer.py
Open: http://localhost:8090
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
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from boat.eth_trace_analyzer import EthTraceAnalyzer

_PORT = int(os.environ.get("BOAT_ETH_ANALYZER_PORT", "8090"))
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "boat-platform" / "config"
_RECORDINGS_DIR = Path(__file__).resolve().parent.parent / "boat-platform" / "traces"
# EthTraceAnalyzer supports both classic pcap (DLT_EN10MB only) and
# pcapng (a pcapng file may also carry CAN interfaces -- boat.pcapng
# extracts only the Ethernet ones, CAN interfaces are silently skipped).
_SUPPORTED_SUFFIXES = (".pcap", ".pcapng")

app = FastAPI()

# Holds the live EthTraceAnalyzer/EthTraceAnalysis after Stage 1 so a
# Stage 2 call (find_autosar_pdus(), which does its own second pass over
# the file) can reuse Stage 1's flow classification without re-running it.
# Loading a different path resets this -- Stage 2 only means something for
# the file Stage 1 was last run against.
_stage_cache: dict[str, Any] = {}
_stage_lock = threading.Lock()

# ── API routes ──────────────────────────────────────────────────────────────

@app.get("/api/pcap/list")
def api_pcap_list():
    files = []
    for d in [_RECORDINGS_DIR, _CONFIG_DIR, Path("/tmp"), Path.home(), Path.home() / "traces", Path.home() / "traces" / "pcap"]:
        try:
            for pattern in ("*.pcap", "*.pcapng"):
                for f in Path(d).glob(pattern):
                    files.append(str(f))
        except Exception:
            pass
    files = sorted(set(files))[:200]
    return {"files": files}

@app.post("/api/pcap/analyze")
def api_pcap_analyze(body: dict):
    """Single-pass bulk analysis: EtherType/VLAN histograms, node
    inventory, UDP flow stats (cyclic/event classification, SOME/IP
    recognition), TCP session reconstruction (client/server roles), and a
    DoIP server / SOME/IP service-ID+method-ID catalog."""
    path = body.get("path", "")
    fp = Path(path).expanduser()
    if not fp.exists():
        raise HTTPException(404, f"File not found: {fp}")
    if fp.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise HTTPException(400, f"Unsupported format: {fp.suffix}. Supported: .pcap, .pcapng")

    t0 = time.perf_counter()
    try:
        analyzer = EthTraceAnalyzer(str(fp))
        analysis = analyzer.analyze()
    except Exception as e:
        raise HTTPException(400, f"Analysis failed: {e}")
    elapsed = time.perf_counter() - t0

    with _stage_lock:
        _stage_cache.clear()
        _stage_cache.update({"path": str(fp), "analyzer": analyzer, "analysis": analysis})

    summary = analyzer.to_summary(analysis)
    summary["file_name"] = fp.name
    summary["file_size"] = fp.stat().st_size
    summary["elapsed_s"] = round(elapsed, 2)
    return summary

@app.post("/api/pcap/stage/pdus")
def api_pcap_stage_pdus(body: dict):
    """Stage 2: AUTOSAR PDU-multiplex deep dive. Requires Stage 1 to have
    run for this exact file (uses its flow classification); does its own
    second pass over the capture to build each PDU Header-ID's full
    history, eliminate routed/relayed duplicates, and classify sending
    behavior. No signal-level decoding -- Header-ID, length, and raw
    payload only."""
    path = body.get("path", "")
    fp = Path(path).expanduser()
    with _stage_lock:
        if _stage_cache.get("path") != str(fp):
            raise HTTPException(400, "Run Stage 1 (Analyze) for this file first")
        analyzer: EthTraceAnalyzer = _stage_cache["analyzer"]
        analysis = _stage_cache["analysis"]

    t0 = time.perf_counter()
    try:
        pdus = analyzer.find_autosar_pdus(analysis)
    except Exception as e:
        raise HTTPException(400, f"PDU analysis failed: {e}")
    elapsed = time.perf_counter() - t0

    rows = analyzer.pdu_summary(pdus, analysis)
    return {
        "elapsed_s": round(elapsed, 2),
        "pdu_count": len(rows),
        "pdus_with_duplicates": sum(1 for r in rows if r["duplicate_flows"]),
        "pdus": rows,
    }

# ── HTML ────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — Ethernet Trace Analyzer</title>
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
#panel-nav {
  height:32px; background:#0d1117; border-bottom:1px solid var(--border);
  display:flex; align-items:center; padding:0 16px; gap:8px;
}
#panel-nav .nav-link { color:var(--muted); font-size:12px; text-decoration:none; padding:4px 10px; border-radius:4px; }
#panel-nav .nav-link:hover { color:var(--text); background:var(--panel); }
#panel-nav .nav-link.active { color:var(--blue); background:rgba(88,166,255,0.1); }
.layout { display:flex; height:calc(100vh - 78px); }
.sidebar {
  width:340px; min-width:340px; background:var(--panel); border-right:1px solid var(--border);
  display:flex; flex-direction:column; overflow:hidden;
}
.sidebar-toolbar { padding:8px; display:flex; gap:4px; border-bottom:1px solid var(--border); flex-wrap:wrap; }
.sidebar-toolbar input { flex:1; padding:4px 8px; background:var(--bg); border:1px solid var(--border); border-radius:4px; color:var(--text); font-family:var(--mono); font-size:12px; }
button.btn { padding:5px 10px; border:1px solid var(--border); border-radius:4px; background:var(--bg); color:var(--text); cursor:pointer; font-size:12px; }
button.btn:hover { background:var(--panel); }
.btn-primary { color:var(--blue) !important; border-color:var(--blue) !important; }
.btn-primary:hover { background:rgba(88,166,255,0.1) !important; }
.btn-add { color:var(--green) !important; border-color:var(--green) !important; }
.btn-add:hover { background:rgba(63,185,80,0.1) !important; }
.main { flex:1; overflow-y:auto; padding:16px; }
.pane { max-width:1200px; margin:0 auto; }
h2 { font-size:16px; font-weight:600; margin:0 0 12px; }
h3 { font-size:14px; font-weight:600; margin:20px 0 8px; color:var(--text); display:flex; align-items:center; gap:8px; }
h3 .hint { font-size:11px; color:var(--muted); font-weight:400; }
.export-btn { font-size:10px; font-weight:400; padding:2px 7px; border:1px solid var(--border); border-radius:3px; background:transparent; color:var(--muted); cursor:pointer; white-space:nowrap; }
.export-btn:hover { color:var(--text); border-color:var(--blue); }
table { width:100%; border-collapse:collapse; font-size:12px; }
th { text-align:left; padding:6px 8px; border-bottom:2px solid var(--border); color:var(--muted); font-weight:600; font-size:11px; position:sticky; top:0; background:var(--bg); white-space:nowrap; }
td { padding:5px 8px; border-bottom:1px solid var(--border); font-family:var(--mono); font-size:11px; }
tr:hover td { background:rgba(88,166,255,0.03); }
.stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:8px; margin-bottom:16px; }
.stat-card { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:12px; text-align:center; }
.stat-card .value { font-size:22px; font-weight:700; color:var(--blue); font-family:var(--mono); }
.stat-card .label { font-size:11px; color:var(--muted); margin-top:2px; }
.empty-state { text-align:center; padding:60px 20px; color:var(--muted); }
.empty-state h2 { font-size:20px; margin-bottom:8px; }
.empty-state p { font-size:14px; margin-bottom:16px; }
.badge { display:inline-block; padding:1px 5px; border-radius:3px; font-size:10px; font-weight:600; }
.badge-cyclic { background:rgba(63,185,80,0.15); color:var(--green); }
.badge-bursty { background:rgba(210,153,34,0.15); color:var(--yellow); }
.badge-doip { background:rgba(210,168,255,0.15); color:var(--purple); }
.badge-someip { background:rgba(88,166,255,0.15); color:var(--blue); }
.badge-mcast { background:rgba(255,166,87,0.15); color:var(--orange); }
.section { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:12px; margin-bottom:16px; overflow-x:auto; }
#toast-container {
  position:fixed; bottom:20px; right:20px; z-index:9999;
  display:flex; flex-direction:column-reverse; gap:8px; align-items:flex-end;
}
.toast { padding:10px 20px; border-radius:6px; font-size:13px; max-width:420px; animation:fadeIn 0.2s; }
.toast.info { background:var(--blue); color:#fff; }
.toast.error { background:var(--red); color:#fff; }
.toast.success { background:var(--green); color:#fff; }
@keyframes fadeIn { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
.spinner { border:2px solid var(--border); border-top-color:var(--blue); border-radius:50%; animation:spin 0.8s linear infinite; }
@keyframes spin { to{transform:rotate(360deg)} }
::-webkit-scrollbar { width:4px; height:4px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--border); border-radius:2px; }
</style>
</head>
<body>

<header>
  <span class="logo">⛵ BoAt</span>
  <span class="subtitle">Ethernet Trace Analyzer</span>
  <span class="spacer"></span>
</header>

<nav id="panel-nav">
  <a class="nav-link" data-port="8089">Trace Editor</a>
  <a class="nav-link" data-port="8088">Trace Analyzer</a>
  <a class="nav-link" data-port="8090" style="color:var(--blue)">Eth Analyzer</a>
  <a class="nav-link" data-port="8087">PDU Editor</a>
</nav>

<div class="layout">
  <div class="sidebar">
    <div class="sidebar-toolbar">
      <input id="file-path" type="text" placeholder="/path/to/capture.pcap|.pcapng"/>
      <button class="btn btn-primary" onclick="browseFile()">Browse</button>
    </div>
    <div style="padding:8px;border-bottom:1px solid var(--border);display:flex;flex-direction:column;gap:6px">
      <button class="btn btn-add" id="analyze-btn" onclick="runAnalyze()" style="width:100%">1. Analyze</button>
      <button class="btn btn-primary" id="pdu-stage-btn" onclick="runPduStage()" disabled style="width:100%">2. Find AUTOSAR PDU Messages</button>
      <div id="progress" style="display:none;align-items:center;gap:6px;font-size:11px;color:var(--muted)">
        <div class="spinner" style="width:12px;height:12px;border-width:2px;display:inline-block"></div>
        <span id="progress-text">Reading capture...</span>
      </div>
    </div>
    <div id="file-list" style="flex:1;overflow-y:auto;padding:8px;font-size:12px"></div>
  </div>

  <div class="main" id="main-content">
    <div class="empty-state" id="empty-state">
      <h2>No capture analyzed</h2>
      <p>Enter a .pcap/.pcapng path and click Analyze.</p>
    </div>
    <div class="pane" id="results" style="display:none"></div>
  </div>
</div>

<div id="toast-container"></div>

<script>
const params = new URLSearchParams(location.search);
document.querySelectorAll("#panel-nav .nav-link").forEach(a => {
  const port = a.dataset.port;
  a.href = `${location.protocol}//${location.hostname}:${port}/`;
});

function toast(msg, type="info") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  const duration = Math.min(8000, Math.max(3000, msg.length * 60));
  setTimeout(() => el.remove(), duration);
}

async function api(method, url, body) {
  const opts = { method, headers: {"Content-Type": "application/json"} };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { const j = await r.json(); msg = j.detail || msg; } catch(e) {}
    throw new Error(msg);
  }
  return r.json();
}

async function loadFileList() {
  try {
    const r = await api("GET", "/api/pcap/list");
    const div = document.getElementById("file-list");
    if (!r.files.length) { div.innerHTML = '<div style="color:var(--muted)">No .pcap files found</div>'; return; }
    div.innerHTML = r.files.map(f => `<div style="padding:4px 0;cursor:pointer;color:var(--muted);word-break:break-all" onclick="document.getElementById('file-path').value='${f.replace(/\\/g,"\\\\")}'; runAnalyze()">${f}</div>`).join("");
  } catch(e) {}
}
loadFileList();

function browseFile() {
  toast("Enter the full path in the text field, then click Analyze", "info");
}

let lastResult = null;
let pduResult = null;

async function runAnalyze() {
  const path = document.getElementById("file-path").value.trim();
  if (!path) { toast("Enter a .pcap/.pcapng path first", "error"); return; }
  document.getElementById("analyze-btn").disabled = true;
  document.getElementById("pdu-stage-btn").disabled = true;
  document.getElementById("progress-text").textContent = "Reading capture...";
  document.getElementById("progress").style.display = "flex";
  try {
    lastResult = await api("POST", "/api/pcap/analyze", {path});
    pduResult = null;
    renderResults();
    document.getElementById("empty-state").style.display = "none";
    document.getElementById("results").style.display = "block";
    toast(`Analyzed in ${lastResult.elapsed_s}s: ${lastResult.file_name} — ${lastResult.total_frames.toLocaleString()} frames, ${lastResult.duration_s}s span`, "success");
    (lastResult.warnings || []).forEach(w => toast(w, "info"));
    document.getElementById("pdu-stage-btn").disabled = false;
  } catch(e) {
    toast("Analysis failed: " + e.message, "error");
  } finally {
    document.getElementById("analyze-btn").disabled = false;
    document.getElementById("progress").style.display = "none";
  }
}

async function runPduStage() {
  if (!lastResult) { toast("Run Analyze first", "error"); return; }
  document.getElementById("pdu-stage-btn").disabled = true;
  document.getElementById("progress-text").textContent = "Scanning AUTOSAR PDU-multiplex traffic...";
  document.getElementById("progress").style.display = "flex";
  try {
    pduResult = await api("POST", "/api/pcap/stage/pdus", {path: lastResult.path});
    renderResults();
    toast(`PDU stage done in ${pduResult.elapsed_s}s: ${pduResult.pdu_count} PDU IDs (${pduResult.pdus_with_duplicates} with routed duplicates eliminated)`, "success");
  } catch(e) {
    toast("PDU stage failed: " + e.message, "error");
  } finally {
    document.getElementById("pdu-stage-btn").disabled = false;
    document.getElementById("progress").style.display = "none";
  }
}

function fmtVlans(vlans) {
  return vlans && vlans.length ? vlans.map(v => `<span class="badge" style="background:rgba(255,166,87,0.12);color:var(--orange)">${v}</span>`).join(" ") : "—";
}

function fmtRoleHints(hints) {
  if (!hints || !hints.length) return "";
  const colorFor = h => h.startsWith("DoIP Server") ? "var(--purple)" : h.startsWith("Likely Diagnostic") ? "var(--orange)" : h.startsWith("SOME/IP") ? "var(--blue)" : "var(--muted)";
  return hints.map(h => `<span class="badge" style="background:rgba(255,255,255,0.06);color:${colorFor(h)};margin-right:3px">${h}</span>`).join("");
}

// ── CSV export ────────────────────────────────────────────────────────────
// Every export is generated client-side from the full (untruncated) arrays
// already held in lastResult/pduResult -- the on-screen tables only render
// the first N rows for readability, but the underlying data behind every
// section is complete, so exporting needs no server round-trip.

function csvCell(v) {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) v = v.join("; ");
  else if (typeof v === "object") v = JSON.stringify(v);
  v = String(v);
  if (/[",\r\n]/.test(v)) v = '"' + v.replace(/"/g, '""') + '"';
  return v;
}

function downloadCSV(filename, rows, columns) {
  if (!rows || !rows.length) { toast("Nothing to export", "info"); return; }
  const header = columns.map(c => csvCell(c.label)).join(",");
  const lines = rows.map(row => columns.map(c => csvCell(c.get(row))).join(","));
  const csv = [header, ...lines].join("\r\n");
  const blob = new Blob(["﻿" + csv], {type: "text/csv;charset=utf-8;"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  toast(`Exported ${rows.length} rows to ${filename}`, "success");
}

function exportDoipServers() {
  downloadCSV("doip_servers.csv", (lastResult.doip_servers || []).map(ip => ({ip})), [
    {label: "IP", get: r => r.ip},
  ]);
}

function exportNodes() {
  downloadCSV("nodes.csv", lastResult.nodes, [
    {label: "Label", get: r => r.label},
    {label: "IP", get: r => r.ip},
    {label: "FramesSent", get: r => r.frames_sent},
    {label: "FramesReceived", get: r => r.frames_received},
    {label: "VLANs", get: r => r.vlan_ids},
    {label: "RoleHints", get: r => r.role_hints},
  ]);
}

function exportMulticastGroups() {
  downloadCSV("multicast_groups.csv", lastResult.multicast_groups, [
    {label: "Address", get: r => r.address},
    {label: "Port", get: r => r.port},
    {label: "VLANs", get: r => r.vlan_ids},
    {label: "Frames", get: r => r.frame_count},
    {label: "Bytes", get: r => r.byte_count},
    {label: "Senders", get: r => r.sender_labels},
    {label: "ConfirmedMembers", get: r => r.confirmed_member_labels},
  ]);
}

function exportUdpFlows() {
  downloadCSV("udp_flows.csv", lastResult.udp_flows, [
    {label: "SrcIP", get: r => r.src_ip},
    {label: "SrcPort", get: r => r.src_port},
    {label: "DstIP", get: r => r.dst_ip},
    {label: "DstPort", get: r => r.dst_port},
    {label: "Frames", get: r => r.frame_count},
    {label: "Bytes", get: r => r.byte_count},
    {label: "CycleTimeMs", get: r => r.cycle_time_ms},
    {label: "SendType", get: r => r.send_type},
    {label: "IsMulticastDst", get: r => r.is_multicast_dst},
    {label: "IsDoipPort", get: r => r.is_doip_port},
    {label: "IsSomeIpSD", get: r => r.is_someip_sd},
    {label: "IsSomeIp", get: r => r.is_someip_like},
    {label: "IsPduMultiplex", get: r => r.is_pdu_multiplex},
    {label: "PduIds", get: r => r.pdu_ids},
  ]);
}

function exportTcpSessions() {
  downloadCSV("tcp_sessions.csv", lastResult.tcp_sessions, [
    {label: "Client", get: r => r.client || r.endpoint_a},
    {label: "Server", get: r => r.server || r.endpoint_b},
    {label: "RoleConfidence", get: r => r.role_confidence},
    {label: "TotalFrames", get: r => r.total_frames},
    {label: "BytesClientToServer", get: r => r.bytes_a_to_b},
    {label: "BytesServerToClient", get: r => r.bytes_b_to_a},
    {label: "IsDoIP", get: r => r.is_doip},
    {label: "SawFinOrRst", get: r => r.saw_fin_or_rst},
    {label: "VLANs", get: r => r.vlan_ids},
  ]);
}

function exportSomeipCatalog() {
  downloadCSV("someip_catalog.csv", lastResult.someip_catalog, [
    {label: "ServiceID", get: r => r.service_id},
    {label: "MethodID", get: r => r.method_id},
    {label: "Count", get: r => r.count},
  ]);
}

function exportPduCatalog() {
  downloadCSV("autosar_pdu_catalog.csv", lastResult.autosar_pdu_catalog, [
    {label: "HeaderID", get: r => r.header_id},
    {label: "Count", get: r => r.count},
  ]);
}

function exportGptpPorts() {
  downloadCSV("gptp_ports.csv", lastResult.gptp.ports, [
    {label: "MAC", get: r => r.label},
    {label: "Port", get: r => r.port_number},
    {label: "Messages", get: r => Object.entries(r.message_counts).map(([k, v]) => `${k}:${v}`).join("; ")},
    {label: "SyncIntervalMs", get: r => r.sync_interval_ms},
    {label: "SyncJitterMs", get: r => r.sync_jitter_ms},
    {label: "MeanCorrectionNs", get: r => r.mean_correction_ns},
    {label: "SeqGaps", get: r => r.sequence_gap_count},
    {label: "SeqTotal", get: r => r.sequence_total_count},
    {label: "AssociatedIPCount", get: r => r.associated_ip_count},
    {label: "AssociatedIPsSample", get: r => r.associated_ips},
  ]);
}

function exportGptpLinks() {
  downloadCSV("gptp_links.csv", lastResult.gptp.links, [
    {label: "PortA", get: r => r.port_a},
    {label: "PortB", get: r => r.port_b},
    {label: "Exchanges", get: r => r.exchange_count},
    {label: "TurnaroundNsMean", get: r => r.turnaround_ns_mean},
    {label: "TurnaroundNsStdev", get: r => r.turnaround_ns_stdev},
    {label: "CaptureRttMsMean", get: r => r.capture_rtt_ms_mean},
    {label: "CaptureRttMsStdev", get: r => r.capture_rtt_ms_stdev},
  ]);
}

function exportPduMessages() {
  if (!pduResult) { toast("Run the PDU stage first", "error"); return; }
  downloadCSV("autosar_pdu_messages.csv", pduResult.pdus, [
    {label: "HeaderID", get: r => r.header_id},
    {label: "OriginalFlow", get: r => r.flow},
    {label: "Frames", get: r => r.count},
    {label: "Length", get: r => r.length_values},
    {label: "LengthStable", get: r => r.length_is_stable},
    {label: "CycleTimeMs", get: r => r.cycle_time_ms},
    {label: "SendType", get: r => r.send_type},
    {label: "DuplicateFlows", get: r => r.duplicate_flows.map(d => `${d.flow} (${d.count})`).join("; ")},
  ]);
}

function renderResults() {
  const r = lastResult;
  const el = document.getElementById("results");

  const nodeCount = r.nodes.length;
  const mcastCount = r.multicast_groups.length;
  const udpCount = r.udp_flows.length;
  const tcpCount = r.tcp_sessions.length;
  const doipCount = r.doip_servers.length;
  const someipCount = r.someip_catalog.length;
  const pduCount = r.autosar_pdu_catalog.length;
  const gptpLinkCount = r.gptp.links.length;

  let html = `
    <div class="stat-grid">
      <div class="stat-card"><div class="value">${r.total_frames.toLocaleString()}</div><div class="label">Total Frames</div></div>
      <div class="stat-card"><div class="value">${r.duration_s}s</div><div class="label">Capture Span</div></div>
      <div class="stat-card"><div class="value">${nodeCount}</div><div class="label">Nodes</div></div>
      <div class="stat-card"><div class="value">${mcastCount}</div><div class="label">Multicast Groups</div></div>
      <div class="stat-card"><div class="value">${udpCount}</div><div class="label">UDP Flows</div></div>
      <div class="stat-card"><div class="value">${tcpCount}</div><div class="label">TCP Sessions</div></div>
      <div class="stat-card"><div class="value">${doipCount}</div><div class="label">DoIP Servers</div></div>
      <div class="stat-card"><div class="value">${someipCount}</div><div class="label">SOME/IP Service+Method IDs</div></div>
      <div class="stat-card"><div class="value">${pduCount}</div><div class="label">AUTOSAR PDU IDs</div></div>
      <div class="stat-card"><div class="value">${gptpLinkCount}</div><div class="label">gPTP Links</div></div>
      <div class="stat-card"><div class="value">${(r.file_size/1024/1024).toFixed(1)} MB</div><div class="label">File Size</div></div>
    </div>
  `;

  html += `<div class="section"><h3>EtherType / VLAN breakdown</h3><div style="display:flex;gap:24px;flex-wrap:wrap">
    <table style="width:auto;min-width:260px"><thead><tr><th>EtherType</th><th>Name</th><th>Frames</th></tr></thead><tbody>
      ${r.ethertypes.map(e => `<tr><td>${e.ethertype}</td><td>${e.name}</td><td>${e.count.toLocaleString()}</td></tr>`).join("")}
    </tbody></table>
    <table style="width:auto;min-width:200px"><thead><tr><th>VLAN ID</th><th>Frames</th></tr></thead><tbody>
      ${r.vlans.length ? r.vlans.map(v => `<tr><td>${v.vlan_id}</td><td>${v.count.toLocaleString()}</td></tr>`).join("") : '<tr><td colspan="2" style="color:var(--muted)">no VLAN tags</td></tr>'}
    </tbody></table>
    <table style="width:auto;min-width:200px"><thead><tr><th>IP Protocol</th><th>Frames</th></tr></thead><tbody>
      ${r.ip_protocols.map(p => `<tr><td>${p.proto}</td><td>${p.count.toLocaleString()}</td></tr>`).join("")}
    </tbody></table>
  </div></div>`;

  html += `<div class="section"><h3>DoIP servers <button class="export-btn" onclick="exportDoipServers()">&#8681; CSV</button><span class="hint">confirmed via SYN-ACK on port 13400</span></h3>
    ${r.doip_servers.length ? `<table><thead><tr><th>IP</th></tr></thead><tbody>${r.doip_servers.map(ip => `<tr><td>${ip}</td></tr>`).join("")}</tbody></table>` : '<div style="color:var(--muted)">none found</div>'}
  </div>`;

  html += `<div class="section"><h3>Nodes <button class="export-btn" onclick="exportNodes()">&#8681; CSV</button><span class="hint">unicast IP addresses only -- sorted by total traffic; see Multicast Groups below for group addresses</span></h3>
    <table><thead><tr><th>Node</th><th>IP</th><th>Sent</th><th>Received</th><th>VLANs seen</th><th>Role hints</th></tr></thead><tbody>
    ${r.nodes.slice(0, 60).map(n => `<tr>
      <td><strong>${n.label}</strong></td>
      <td>${n.ip}</td>
      <td>${n.frames_sent.toLocaleString()}</td>
      <td>${n.frames_received.toLocaleString()}</td>
      <td>${fmtVlans(n.vlan_ids)}</td>
      <td>${fmtRoleHints(n.role_hints)}</td>
    </tr>`).join("")}
    </tbody></table>
    ${r.nodes.length > 60 ? `<div style="color:var(--muted);padding-top:6px">... and ${r.nodes.length-60} more</div>` : ''}
  </div>`;

  html += `<div class="section"><h3>Multicast groups <button class="export-btn" onclick="exportMulticastGroups()">&#8681; CSV</button><span class="hint">grouped by (address, port) -- one address can carry several distinct channels on different ports; "Senders" are directly observed, "Confirmed members" only come from an actual MLD Report${r.mld_observed ? '' : ' (none observed in this capture -- membership can\'t be confirmed for any channel here)'}</span></h3>
    ${r.multicast_groups.length ? `<table><thead><tr><th>Group Address</th><th>Port</th><th>VLANs</th><th>Frames</th><th>Senders</th><th>Confirmed members (MLD)</th></tr></thead><tbody>
      ${r.multicast_groups.slice(0, 60).map(g => `<tr>
        <td>${g.address}</td>
        <td>${g.port !== null ? g.port : "—"}</td>
        <td>${fmtVlans(g.vlan_ids)}</td>
        <td>${g.frame_count.toLocaleString()}</td>
        <td>${g.sender_labels.join(", ") || "—"}</td>
        <td>${g.confirmed_member_labels.length ? g.confirmed_member_labels.join(", ") : '<span style="color:var(--muted)">not observed</span>'}</td>
      </tr>`).join("")}
    </tbody></table>
    ${r.multicast_groups.length > 60 ? `<div style="color:var(--muted);padding-top:6px">... and ${r.multicast_groups.length-60} more</div>` : ''}` : '<div style="color:var(--muted)">none found</div>'}
  </div>`;

  html += `<div class="section"><h3>UDP flows <button class="export-btn" onclick="exportUdpFlows()">&#8681; CSV</button><span class="hint">Cyclic = consistent inter-frame timing (low jitter); Bursty = irregular/multiplexed</span></h3>
    <table><thead><tr><th>Src</th><th>Dst</th><th>Frames</th><th>Bytes</th><th>Cycle</th><th>Type</th><th>Tags</th></tr></thead><tbody>
    ${r.udp_flows.slice(0, 80).map(f => `<tr>
      <td>${f.src_ip}:${f.src_port}</td>
      <td>${f.dst_ip}:${f.dst_port}</td>
      <td>${f.frame_count.toLocaleString()}</td>
      <td>${(f.byte_count/1024).toFixed(1)} KB</td>
      <td>${f.cycle_time_ms ? f.cycle_time_ms.toFixed(3) + " ms" : "—"}</td>
      <td><span class="badge ${f.send_type === 'Cyclic' ? 'badge-cyclic' : 'badge-bursty'}">${f.send_type}</span></td>
      <td>
        ${f.is_multicast_dst ? '<span class="badge badge-mcast">multicast</span>' : ''}
        ${f.is_doip_port ? '<span class="badge badge-doip">DoIP</span>' : ''}
        ${f.is_someip_sd ? '<span class="badge badge-someip">SOME/IP-SD</span>' : ''}
        ${f.is_someip_like ? '<span class="badge badge-someip">SOME/IP</span>' : ''}
        ${f.is_pdu_multiplex ? `<span class="badge badge-someip" style="background:rgba(210,168,255,0.15);color:var(--purple)" title="AUTOSAR SoAd PDU-multiplex records: ${f.pdu_ids.join(', ')}">PDU-Mux (${f.pdu_ids.length} IDs)</span>` : ''}
      </td>
    </tr>`).join("")}
    </tbody></table>
    ${r.udp_flows.length > 80 ? `<div style="color:var(--muted);padding-top:6px">... and ${r.udp_flows.length-80} more</div>` : ''}
  </div>`;

  html += `<div class="section"><h3>TCP sessions <button class="export-btn" onclick="exportTcpSessions()">&#8681; CSV</button><span class="hint">client/server roles from observed SYN / SYN-ACK; "unknown" means the handshake wasn't captured</span></h3>
    <table><thead><tr><th>Client</th><th>Server</th><th>Role</th><th>Frames</th><th>Bytes c&rarr;s</th><th>Bytes s&rarr;c</th><th>Tags</th></tr></thead><tbody>
    ${r.tcp_sessions.slice(0, 80).map(s => `<tr>
      <td>${s.client || s.endpoint_a}</td>
      <td>${s.server || s.endpoint_b}</td>
      <td style="color:${s.role_confidence === 'confirmed' ? 'var(--green)' : 'var(--muted)'}">${s.role_confidence}</td>
      <td>${s.total_frames.toLocaleString()}</td>
      <td>${(s.bytes_a_to_b/1024).toFixed(1)} KB</td>
      <td>${(s.bytes_b_to_a/1024).toFixed(1)} KB</td>
      <td>${s.is_doip ? '<span class="badge badge-doip">DoIP</span>' : ''}</td>
    </tr>`).join("")}
    </tbody></table>
    ${r.tcp_sessions.length > 80 ? `<div style="color:var(--muted);padding-top:6px">... and ${r.tcp_sessions.length-80} more</div>` : ''}
  </div>`;

  html += `<div class="section"><h3>SOME/IP service catalog <button class="export-btn" onclick="exportSomeipCatalog()">&#8681; CSV</button><span class="hint">Service+Method ID pairs recognized by header shape, not semantic meaning</span></h3>
    ${r.someip_catalog.length ? `<table><thead><tr><th>Service ID</th><th>Method ID</th><th>Frames sampled matching</th></tr></thead><tbody>
      ${r.someip_catalog.slice(0, 60).map(c => `<tr><td>${c.service_id}</td><td>${c.method_id}</td><td>${c.count}</td></tr>`).join("")}
    </tbody></table>` : '<div style="color:var(--muted)">none found</div>'}
  </div>`;

  html += `<div class="section"><h3>AUTOSAR PDU catalog <button class="export-btn" onclick="exportPduCatalog()">&#8681; CSV</button><span class="hint">Header-IDs recognized from repeated (ID+Length+data) SoAd PDU-multiplex records; not signal-level decoded</span></h3>
    ${r.autosar_pdu_catalog.length ? `<table><thead><tr><th>Header-ID</th><th>Frames sampled matching</th></tr></thead><tbody>
      ${r.autosar_pdu_catalog.slice(0, 60).map(c => `<tr><td>${c.header_id}</td><td>${c.count}</td></tr>`).join("")}
    </tbody></table>
    ${r.autosar_pdu_catalog.length > 60 ? `<div style="color:var(--muted);padding-top:6px">... and ${r.autosar_pdu_catalog.length-60} more</div>` : ''}` : '<div style="color:var(--muted)">none found</div>'}
  </div>`;

  html += `<div class="section"><h3>gPTP (IEEE 802.1AS) <span class="hint">identified by MAC address only -- see hover text on a port row for why an IP label isn't used here</span></h3>
    <h4 style="font-size:12px;color:var(--muted);margin:4px 0 6px;display:flex;align-items:center;gap:8px">Ports <button class="export-btn" onclick="exportGptpPorts()">&#8681; CSV</button></h4>
    <table><thead><tr><th>MAC</th><th>Port</th><th>Messages</th><th>Sync interval</th><th>Sync jitter</th><th>Mean correction</th><th>Seq. gaps</th></tr></thead><tbody>
    ${r.gptp.ports.map(p => `<tr>
      <td title="${p.associated_ip_count ? 'Sourced IP traffic for ' + p.associated_ip_count + ' address(es) in this capture (may include traffic merely routed through this device, not necessarily its own): ' + p.associated_ips.join(', ') + (p.associated_ip_count > p.associated_ips.length ? ', ...' : '') : 'No IP traffic observed from this MAC in this capture'}">${p.label}</td>
      <td>${p.port_number}</td>
      <td>${Object.entries(p.message_counts).map(([k,v]) => `${k}: ${v}`).join(", ")}</td>
      <td>${p.sync_interval_ms ? p.sync_interval_ms.toFixed(3) + " ms" : "—"}</td>
      <td>${p.sync_jitter_ms ? p.sync_jitter_ms.toFixed(3) + " ms" : "—"}</td>
      <td>${p.mean_correction_ns ? p.mean_correction_ns.toFixed(0) + " ns" : "—"}</td>
      <td style="color:${p.sequence_gap_count ? 'var(--yellow)' : 'var(--muted)'}">${p.sequence_gap_count} / ${p.sequence_total_count}</td>
    </tr>`).join("")}
    </tbody></table>
    <h4 style="font-size:12px;color:var(--muted);margin:14px 0 6px;display:flex;align-items:center;gap:8px">Links <button class="export-btn" onclick="exportGptpLinks()">&#8681; CSV</button><span class="hint" style="font-weight:400">each resolved Pdelay_Req/Resp/Follow_Up exchange is direct evidence of a physical point-to-point link -- turnaround is exact (from the messages' own embedded timestamps); capture RTT is this capture's own frame-arrival-time approximation, not gPTP's own path-delay computation</span></h4>
    <table><thead><tr><th>Port A</th><th>Port B</th><th>Exchanges</th><th>Turnaround (mean / stdev)</th><th>Capture RTT (mean / stdev)</th></tr></thead><tbody>
    ${r.gptp.links.map(l => `<tr>
      <td>${l.port_a}</td>
      <td>${l.port_b}</td>
      <td>${l.exchange_count.toLocaleString()}</td>
      <td>${(l.turnaround_ns_mean/1000).toFixed(1)} &micro;s / ${(l.turnaround_ns_stdev/1000).toFixed(1)} &micro;s</td>
      <td>${l.capture_rtt_ms_mean.toFixed(4)} ms / ${l.capture_rtt_ms_stdev.toFixed(4)} ms</td>
    </tr>`).join("")}
    </tbody></table>
    ${!r.gptp.ports.length ? '<div style="color:var(--muted)">no gPTP traffic found</div>' : ''}
  </div>`;

  if (pduResult) {
    html += `<div class="section"><h3>AUTOSAR PDU messages (Stage 2) <button class="export-btn" onclick="exportPduMessages()">&#8681; CSV</button><span class="hint">routed/relayed duplicates eliminated -- same principle as CAN's multi-channel dedup; the flow whose copy of each payload value appeared earliest is kept as the original. No signal-level decoding -- Header-ID, length, and raw payload only.</span></h3>
      <table><thead><tr><th>Header-ID</th><th>Original flow</th><th>Frames</th><th>Length</th><th>Cycle</th><th>Type</th><th>Routed duplicates eliminated</th></tr></thead><tbody>
      ${pduResult.pdus.slice(0, 150).map(p => `<tr>
        <td>${p.header_id}</td>
        <td>${p.flow}</td>
        <td>${p.count.toLocaleString()}</td>
        <td>${p.length_is_stable ? p.length_values[0] : p.length_values.join(", ") + ' <span style="color:var(--yellow)">(varies)</span>'}</td>
        <td>${p.cycle_time_ms ? p.cycle_time_ms.toFixed(3) + " ms" : "—"}</td>
        <td><span class="badge ${p.send_type === 'Cyclic' ? 'badge-cyclic' : 'badge-bursty'}">${p.send_type}</span></td>
        <td>${p.duplicate_flows.length ? p.duplicate_flows.map(d => `${d.flow} (${d.count.toLocaleString()} frames)`).join("; ") : "—"}</td>
      </tr>`).join("")}
      </tbody></table>
      ${pduResult.pdus.length > 150 ? `<div style="color:var(--muted);padding-top:6px">... and ${pduResult.pdus.length-150} more</div>` : ''}
    </div>`;
  }

  el.innerHTML = html;
}

if (params.get("path")) {
  document.getElementById("file-path").value = params.get("path");
  runAnalyze();
}
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index(path: Optional[str] = Query(None)):
    return HTML

if __name__ == "__main__":
    print(f"BoAt Ethernet Trace Analyzer starting on http://0.0.0.0:{_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
