"""
BoAt Platform — PDU Database Editor
Create, modify, validate, and export PDU database JSON files.
Run:  python3 tools/pdu_editor.py
Open: http://localhost:8087
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "boat-platform" / "sdk" / "python"))

import jsonschema
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from boat.pdu_db import PduDatabase

_PORT = int(os.environ.get("BOAT_PDU_EDITOR_PORT", "8087"))
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "boat-platform" / "config"
_SCHEMA_PATH = _CONFIG_DIR / "pdu_db.schema.json"

_current_db: dict[str, Any] = {"schema_version": "1.0", "messages": [], "signal_routes": []}
_current_path: Optional[str] = None
_db_lock = threading.Lock()

_schema: Optional[dict] = None
if _SCHEMA_PATH.exists():
    try:
        _schema = json.loads(_SCHEMA_PATH.read_text())
    except Exception:
        pass

app = FastAPI()

# ── Schema validation ─────────────────────────────────────────────────────────

def _validate_db(db: dict) -> list[str]:
    """Validate db dict against schema, return list of error strings."""
    if _schema is None:
        return ["Schema file not found"]
    errors: list[str] = []
    try:
        jsonschema.validate(db, _schema)
    except jsonschema.ValidationError as e:
        errors.append(f"{e.message} (path: {' → '.join(str(p) for p in e.absolute_path)})")
    except Exception as e:
        errors.append(str(e))

    seen_ids: set[int] = set()
    for i, msg in enumerate(db.get("messages", [])):
        dbid = msg.get("DbId")
        if dbid is not None:
            if dbid in seen_ids:
                errors.append(f"Duplicate DbId {dbid} at message index {i}")
            seen_ids.add(dbid)
        if not msg.get("MessageName", "").strip():
            errors.append(f"Message at index {i} has empty MessageName")
        bt = msg.get("BusType")
        if bt == "ETH":
            if not msg.get("IpduMEntries"):
                errors.append(f"ETH message '{msg.get('MessageName')}' has no IpduMEntries")
        elif bt == "ETH_PDU":
            if msg.get("ContainerDbId") is None:
                errors.append(f"ETH_PDU message '{msg.get('MessageName')}' has no ContainerDbId")
        elif bt in ("CAN", "CANFD"):
            if "Identifier" not in msg:
                errors.append(f"{bt} message '{msg.get('MessageName')}' has no Identifier")

    for sr in db.get("signal_routes", []):
        if sr["SrcDbId"] not in seen_ids:
            errors.append(f"signal_route SrcDbId {sr['SrcDbId']} not found in messages")
        if sr["DstDbId"] not in seen_ids:
            errors.append(f"signal_route DstDbId {sr['DstDbId']} not found in messages")

    return errors

# ── Helpers ──────────────────────────────────────────────────────────────────

def _default_msg(bus_type: str = "CAN", db_id: int = 1) -> dict:
    base: dict[str, Any] = {
        "DbId": db_id,
        "MessageName": f"Message_{db_id}",
        "Bus": "CAN_Bus",
        "BusType": bus_type,
        "MessageType": 0,
        "Direction": 0,
        "RoutingType": 0,
        "TargetDbIds": None,
        "SourceDbId": None,
        "isE2E": 0,
        "SendType": "Cyclic",
        "CycleTime": 100,
        "CycleTimeFast": 0,
        "NrOfRepetitions": 0,
        "signalcount": 0,
        "signals": [],
    }
    if bus_type in ("CAN", "CANFD"):
        base.update({
            "Identifier": 0,
            "FrameType": 0,
            "Length": 8,
            "BRS": bus_type == "CANFD",
        })
    elif bus_type == "ETH":
        base.update({
            "EtherType": 2048,
            "VlanId": 0,
            "SrcMAC": "",
            "DstMAC": "",
            "SrcIP": "",
            "DstIP": "",
            "SrcPort": 0,
            "DstPort": 0,
            "TTL": 64,
            "IpduMEntries": [],
            "signalcount": 0,
            "signals": [],
        })
    elif bus_type == "ETH_PDU":
        base.update({
            "PduId": 0,
            "ContainerDbId": None,
            "Length": 8,
        })
    return base

def _next_db_id(db: dict) -> int:
    ids = [m.get("DbId", 0) for m in db.get("messages", [])]
    return (max(ids) + 1) if ids else 1

def _next_sig_id(msg: dict) -> int:
    ids = [s.get("id", 0) for s in msg.get("signals", [])]
    return (max(ids) + 1) if ids else 1

# ── API routes ───────────────────────────────────────────────────────────────

@app.get("/api/db/list")
def api_db_list():
    files = sorted(f.name for f in _CONFIG_DIR.glob("*.json") if f.is_file())
    return {"files": files, "config_dir": str(_CONFIG_DIR)}

@app.post("/api/db/new")
def api_db_new():
    global _current_db, _current_path
    with _db_lock:
        _current_db = {"schema_version": "1.0", "messages": [], "signal_routes": []}
        _current_path = None
    return {"status": "ok"}

@app.get("/api/db/load")
def api_db_load(path: str = Query(...)):
    global _current_db, _current_path
    fp = Path(path)
    if not fp.is_absolute():
        fp = _CONFIG_DIR / fp.name
    if not fp.exists():
        raise HTTPException(404, f"File not found: {fp}")
    try:
        data = json.loads(fp.read_text())
    except Exception as e:
        raise HTTPException(400, f"Invalid JSON: {e}")
    with _db_lock:
        _current_db = data
        _current_path = str(fp)
    return _current_db

@app.post("/api/db/save")
def api_db_save(body: dict):
    global _current_db, _current_path
    path_str = body.get("path")
    if path_str:
        fp = Path(path_str)
        if not fp.is_absolute():
            fp = _CONFIG_DIR / fp.name
    else:
        if _current_path:
            fp = Path(_current_path)
        else:
            fp = _CONFIG_DIR / "pdu_db_custom.json"

    errors = _validate_db(_current_db)
    if errors and not body.get("force"):
        return {"status": "error", "errors": errors}

    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(_current_db, indent=2))
    with _db_lock:
        _current_path = str(fp)
    return {"status": "ok", "path": str(fp)}

@app.get("/api/db/validate")
def api_db_validate():
    with _db_lock:
        errors = _validate_db(_current_db)
    return {"valid": len(errors) == 0, "errors": errors}

@app.get("/api/db/current")
def api_db_current():
    with _db_lock:
        return _current_db

@app.get("/api/messages")
def api_messages():
    with _db_lock:
        return {"messages": _current_db.get("messages", [])}

@app.post("/api/messages")
def api_message_create(body: dict):
    with _db_lock:
        db = _current_db
        msg = _default_msg(body.get("bus_type", "CAN"), _next_db_id(db))
        if body.get("name"):
            msg["MessageName"] = body["name"]
        if body.get("bus"):
            msg["Bus"] = body["bus"]
        db.setdefault("messages", []).append(msg)
        return msg

@app.get("/api/messages/{db_id}")
def api_message_get(db_id: int):
    with _db_lock:
        for msg in _current_db.get("messages", []):
            if msg.get("DbId") == db_id:
                return msg
    raise HTTPException(404, f"Message DbId {db_id} not found")

@app.put("/api/messages/{db_id}")
def api_message_update(db_id: int, body: dict):
    with _db_lock:
        for i, msg in enumerate(_current_db.get("messages", [])):
            if msg.get("DbId") == db_id:
                _current_db["messages"][i] = body
                return {"status": "ok"}
    raise HTTPException(404, f"Message DbId {db_id} not found")

@app.delete("/api/messages/{db_id}")
def api_message_delete(db_id: int):
    with _db_lock:
        msgs = _current_db.get("messages", [])
        _current_db["messages"] = [m for m in msgs if m.get("DbId") != db_id]
        _current_db["signal_routes"] = [
            r for r in _current_db.get("signal_routes", [])
            if r.get("SrcDbId") != db_id and r.get("DstDbId") != db_id
        ]
    return {"status": "ok"}

@app.post("/api/messages/{db_id}/signals")
def api_signal_create(db_id: int, body: dict):
    with _db_lock:
        for msg in _current_db.get("messages", []):
            if msg.get("DbId") == db_id:
                sig = {
                    "id": _next_sig_id(msg),
                    "SignalName": body.get("name", f"Signal_{_next_sig_id(msg)}"),
                    "Length": int(body.get("length", 1)),
                    "StartPos": int(body.get("start_pos", 0)),
                    "ByteOrder": int(body.get("byte_order", 0)),
                    "ValueType": body.get("value_type", "Unsigned"),
                    "SigSendType": bool(body.get("sig_send_type", False)),
                    "Repetitions": int(body.get("repetitions", 0)),
                    "InitValue": float(body.get("init_value", 0)),
                    "Factor": float(body.get("factor", 1.0)),
                    "Offset": float(body.get("offset", 0.0)),
                    "Min": float(body.get("min", 0.0)),
                    "Max": float(body.get("max", 1.0)),
                    "Unit": body.get("unit", ""),
                    "EnumValues": body.get("enum_values"),
                    "IsMuxor": bool(body.get("is_muxor", False)),
                    "MuxValue": body.get("mux_value"),
                    "Comment": body.get("comment", ""),
                }
                msg.setdefault("signals", []).append(sig)
                msg["signalcount"] = len(msg["signals"])
                return sig
    raise HTTPException(404, f"Message DbId {db_id} not found")

@app.put("/api/messages/{db_id}/signals/{sig_id}")
def api_signal_update(db_id: int, sig_id: int, body: dict):
    with _db_lock:
        for msg in _current_db.get("messages", []):
            if msg.get("DbId") == db_id:
                for si, sig in enumerate(msg.get("signals", [])):
                    if sig.get("id") == sig_id:
                        msg["signals"][si] = body
                        return {"status": "ok"}
    raise HTTPException(404, f"Signal DbId={db_id} id={sig_id} not found")

@app.delete("/api/messages/{db_id}/signals/{sig_id}")
def api_signal_delete(db_id: int, sig_id: int):
    with _db_lock:
        for msg in _current_db.get("messages", []):
            if msg.get("DbId") == db_id:
                msg["signals"] = [s for s in msg.get("signals", []) if s.get("id") != sig_id]
                msg["signalcount"] = len(msg["signals"])
                return {"status": "ok"}
    raise HTTPException(404, f"Message DbId {db_id} not found")

@app.get("/api/routes")
def api_routes():
    with _db_lock:
        return {"routes": _current_db.get("signal_routes", [])}

@app.post("/api/routes")
def api_route_create(body: dict):
    with _db_lock:
        route = {
            "SrcDbId": int(body["src_db_id"]),
            "SrcSignalId": int(body["src_sig_id"]),
            "DstDbId": int(body["dst_db_id"]),
            "DstSignalId": int(body["dst_sig_id"]),
        }
        _current_db.setdefault("signal_routes", []).append(route)
        return route

@app.delete("/api/routes")
def api_route_delete(body: dict):
    with _db_lock:
        _current_db["signal_routes"] = [
            r for r in _current_db.get("signal_routes", [])
            if not (
                r.get("SrcDbId") == int(body["src_db_id"])
                and r.get("SrcSignalId") == int(body["src_sig_id"])
                and r.get("DstDbId") == int(body["dst_db_id"])
                and r.get("DstSignalId") == int(body["dst_sig_id"])
            )
        ]
    return {"status": "ok"}

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — PDU Database Editor</title>
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
  width:300px; min-width:300px; background:var(--panel); border-right:1px solid var(--border);
  display:flex; flex-direction:column; overflow:hidden;
}
.sidebar-toolbar { padding:8px; display:flex; gap:4px; border-bottom:1px solid var(--border); flex-wrap:wrap; }
.sidebar-toolbar button { font-size:11px; padding:4px 8px; border:1px solid var(--border); border-radius:4px; background:var(--bg); color:var(--text); cursor:pointer; }
.sidebar-toolbar button:hover { background:var(--panel); border-color:var(--blue); }
.sidebar-toolbar button.primary { background:var(--blue); color:#fff; border-color:var(--blue); }
.sidebar-search { padding:6px 8px; border-bottom:1px solid var(--border); }
.sidebar-search input { width:100%; padding:4px 8px; background:var(--bg); border:1px solid var(--border); border-radius:4px; color:var(--text); font-size:12px; }
.sidebar-list { flex:1; overflow-y:auto; }
.msg-item {
  padding:8px 12px; border-bottom:1px solid var(--border); cursor:pointer; display:flex; align-items:center; gap:8px;
  transition:background 0.1s;
}
.msg-item:hover { background:rgba(88,166,255,0.05); }
.msg-item.active { background:rgba(88,166,255,0.12); border-left:3px solid var(--blue); }
.msg-item .msg-name { font-size:13px; font-weight:500; }
.msg-item .msg-id { font-size:11px; color:var(--muted); font-family:var(--mono); }
.msg-item .msg-bus { font-size:10px; color:var(--muted); font-family:var(--mono); padding:1px 4px; border:1px solid var(--border); border-radius:3px; }
.msg-item .msg-type { font-size:10px; color:var(--orange); font-family:var(--mono); }
.main { flex:1; overflow-y:auto; padding:16px; }
.editor-pane { max-width:960px; margin:0 auto; }
.tabs { display:flex; gap:0; border-bottom:1px solid var(--border); margin-bottom:12px; }
.tab-btn { padding:8px 16px; font-size:12px; border:none; background:none; color:var(--muted); cursor:pointer; border-bottom:2px solid transparent; }
.tab-btn:hover { color:var(--text); }
.tab-btn.active { color:var(--blue); border-bottom-color:var(--blue); }
.tab-content { display:none; }
.tab-content.active { display:block; }
.field { margin-bottom:8px; }
.field label { display:block; font-size:11px; color:var(--muted); margin-bottom:2px; }
.field input, .field select, .field textarea {
  width:100%; padding:5px 8px; background:var(--bg); border:1px solid var(--border); border-radius:4px; color:var(--text); font-size:13px; font-family:var(--mono);
}
.field input:focus, .field select:focus { border-color:var(--blue); outline:none; }
.field-row { display:flex; gap:8px; }
.field-row .field { flex:1; }
h3 { font-size:14px; font-weight:600; margin:16px 0 8px; color:var(--text); }
.actions { display:flex; gap:4px; }
.actions button, button.btn { padding:4px 10px; border:1px solid var(--border); border-radius:4px; background:var(--bg); color:var(--text); cursor:pointer; font-size:12px; }
.actions button:hover { background:var(--panel); }
.btn-danger { color:var(--red) !important; border-color:var(--red) !important; }
.btn-danger:hover { background:rgba(248,81,73,0.1) !important; }
.btn-add { color:var(--green) !important; border-color:var(--green) !important; }
.btn-add:hover { background:rgba(63,185,80,0.1) !important; }
.btn-primary { color:var(--blue) !important; border-color:var(--blue) !important; }
.btn-primary:hover { background:rgba(88,166,255,0.1) !important; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th { text-align:left; padding:6px 8px; border-bottom:2px solid var(--border); color:var(--muted); font-weight:600; font-size:11px; position:sticky; top:0; background:var(--bg); }
td { padding:5px 8px; border-bottom:1px solid var(--border); font-family:var(--mono); font-size:11px; }
tr:hover td { background:rgba(88,166,255,0.03); }
tr.selected td { background:rgba(88,166,255,0.1); }
.signal-table td input, .signal-table td select { width:100%; padding:2px 4px; background:transparent; border:1px solid transparent; color:var(--text); font-family:var(--mono); font-size:11px; }
.signal-table td input:focus, .signal-table td select:focus { border-color:var(--blue); background:var(--bg); outline:none; }
.empty-state { text-align:center; padding:40px; color:var(--muted); }
.empty-state h2 { font-size:18px; margin-bottom:8px; }
.empty-state p { font-size:13px; }
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
::-webkit-scrollbar { width:4px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--border); border-radius:2px; }
</style>
</head>
<body>

<header>
  <span class="logo">⛵ BoAt</span>
  <span class="subtitle">PDU Database Editor</span>
  <span class="spacer"></span>
</header>

<nav id="panel-nav">
  <a class="nav-link" data-port="8089">Trace Editor</a>
  <a class="nav-link" data-port="8088">Trace Analyzer</a>
  <a class="nav-link" data-port="8090">Eth Analyzer</a>
  <a class="nav-link" data-port="8087" style="color:var(--blue)">PDU Editor</a>
</nav>

<div class="layout">
  <div class="sidebar">
    <div class="sidebar-toolbar">
      <button class="primary" onclick="newDb()">New</button>
      <button onclick="saveFile()">Save</button>
      <button onclick="saveAs()">Save As</button>
      <button onclick="validateDb()">Validate</button>
    </div>
    <div class="sidebar-search" style="display:flex;gap:4px;padding:6px 8px;border-bottom:1px solid var(--border)">
      <select id="file-select" style="flex:1;padding:3px 4px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:11px;font-family:var(--mono)">
        <option value="">— select file —</option>
      </select>
      <button onclick="loadSelected()" style="padding:3px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--text);cursor:pointer;font-size:11px">Load</button>
      <button onclick="loadFile()" style="padding:3px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--text);cursor:pointer;font-size:11px">Browse...</button>
    </div>
    <div class="sidebar-search"><input id="msg-search" placeholder="Search messages..." oninput="renderMsgList()"/></div>
    <div style="padding:4px 8px;display:flex;gap:4px;border-bottom:1px solid var(--border)">
      <button class="btn-add" onclick="newMessage()" style="flex:1;padding:4px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--text);cursor:pointer;font-size:11px">+ New Message</button>
      <button class="btn-danger" onclick="deleteMessage()" style="padding:4px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);cursor:pointer;font-size:11px">× Delete</button>
    </div>
    <div class="sidebar-list" id="msg-list"></div>
  </div>

  <div class="main" id="editor-main">
    <div class="empty-state" id="empty-state">
      <h2>No database loaded</h2>
      <p>Create a new database or open an existing one.</p>
      <br/>
      <button class="btn btn-add" onclick="newDb()" style="font-size:14px;padding:8px 20px">Create New Database</button>
      &nbsp;
      <button class="btn btn-primary" onclick="loadFile()" style="font-size:14px;padding:8px 20px">Open Existing</button>
    </div>
    <div class="editor-pane" id="editor-pane" style="display:none">
      <div class="tabs" id="editor-tabs">
        <button class="tab-btn active" data-tab="general" onclick="switchTab('general')">General</button>
        <button class="tab-btn" data-tab="bus" onclick="switchTab('bus')">Bus Config</button>
        <button class="tab-btn" data-tab="timing" onclick="switchTab('timing')">Timing</button>
        <button class="tab-btn" data-tab="routing" onclick="switchTab('routing')">Routing</button>
        <button class="tab-btn" data-tab="signals" onclick="switchTab('signals')">Signals</button>
        <button class="tab-btn" data-tab="routes" onclick="switchTab('routes')">Routes</button>
      </div>

      <div class="tab-content active" id="tab-general">
        <div class="field-row">
          <div class="field"><label>DbId</label><input id="f-id" type="number" min="0" onchange="markDirty()"/></div>
          <div class="field"><label>MessageName</label><input id="f-name" onchange="markDirty()"/></div>
        </div>
        <div class="field-row">
          <div class="field"><label>Bus</label><input id="f-bus" onchange="markDirty()"/></div>
          <div class="field"><label>BusType</label>
            <select id="f-bus-type" onchange="onBusTypeChange()">
              <option value="CAN">CAN</option>
              <option value="CANFD">CANFD</option>
              <option value="ETH">ETH</option>
              <option value="ETH_PDU">ETH_PDU</option>
            </select>
          </div>
        </div>
        <div class="field"><label>MessageType</label><input id="f-msg-type" type="number" min="0" value="0" onchange="markDirty()"/></div>
      </div>

      <div class="tab-content" id="tab-bus">
        <div id="bus-can">
          <div class="field-row">
            <div class="field"><label>Identifier (CAN ID)</label><input id="f-can-id" type="number" min="0" onchange="markDirty()"/></div>
            <div class="field"><label>FrameType</label>
              <select id="f-frame-type" onchange="markDirty()">
                <option value="0">0 = Standard (11-bit)</option>
                <option value="1">1 = Extended (29-bit)</option>
              </select>
            </div>
          </div>
          <div class="field-row">
            <div class="field"><label>Length (bytes)</label><input id="f-length" type="number" min="0" onchange="markDirty()"/></div>
            <div class="field"><label>BRS (CANFD)</label>
              <select id="f-brs" onchange="markDirty()">
                <option value="false">False</option><option value="true">True</option>
              </select>
            </div>
          </div>
        </div>
        <div id="bus-eth" style="display:none">
          <div class="field-row">
            <div class="field"><label>EtherType</label><input id="f-ether-type" type="number" min="0" max="65535" onchange="markDirty()"/></div>
            <div class="field"><label>VlanId</label><input id="f-vlan" type="number" min="0" max="4095" onchange="markDirty()"/></div>
          </div>
          <div class="field-row">
            <div class="field"><label>SrcMAC</label><input id="f-src-mac" onchange="markDirty()"/></div>
            <div class="field"><label>DstMAC</label><input id="f-dst-mac" onchange="markDirty()"/></div>
          </div>
          <div class="field-row">
            <div class="field"><label>SrcIP</label><input id="f-src-ip" onchange="markDirty()"/></div>
            <div class="field"><label>DstIP</label><input id="f-dst-ip" onchange="markDirty()"/></div>
          </div>
          <div class="field-row">
            <div class="field"><label>SrcPort</label><input id="f-src-port" type="number" min="0" max="65535" onchange="markDirty()"/></div>
            <div class="field"><label>DstPort</label><input id="f-dst-port" type="number" min="0" max="65535" onchange="markDirty()"/></div>
          </div>
          <div class="field-row">
            <div class="field"><label>TTL</label><input id="f-ttl" type="number" min="0" max="255" onchange="markDirty()"/></div>
            <div class="field">
              <label>IpduMEntries (contained PDUs)</label>
              <div style="margin-bottom:6px;display:flex;gap:4px">
                <select id="f-add-pdu" style="flex:1;padding:3px 4px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:11px;font-family:var(--mono)">
                  <option value="">— add ETH_PDU —</option>
                </select>
                <button onclick="addPduEntry()" style="padding:3px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--text);cursor:pointer;font-size:11px">+</button>
              </div>
              <table style="width:100%;font-size:11px;border-collapse:collapse">
                <thead><tr><th style="text-align:left;padding:4px 6px;border-bottom:1px solid var(--border);color:var(--muted);font-weight:600">DbId</th><th style="text-align:left;padding:4px 6px;border-bottom:1px solid var(--border);color:var(--muted);font-weight:600">Name</th><th style="text-align:left;padding:4px 6px;border-bottom:1px solid var(--border);color:var(--muted);font-weight:600">PduId</th><th style="text-align:left;padding:4px 6px;border-bottom:1px solid var(--border);color:var(--muted);font-weight:600">Cycle</th><th style="padding:4px 6px;border-bottom:1px solid var(--border)"></th></tr></thead>
                <tbody id="pdu-entries-tbody"></tbody>
              </table>
            </div>
          </div>
        </div>
        <div id="bus-eth-pdu" style="display:none">
          <div class="field-row">
            <div class="field"><label>PduId</label><input id="f-pdu-id" type="number" min="0" onchange="markDirty()"/></div>
            <div class="field"><label>ContainerDbId</label><input id="f-container-db-id" type="number" min="0" onchange="markDirty()"/></div>
          </div>
          <div class="field"><label>Length (bytes)</label><input id="f-eth-pdu-length" type="number" min="0" onchange="markDirty()"/></div>
        </div>
      </div>

      <div class="tab-content" id="tab-timing">
        <div class="field-row">
          <div class="field"><label>SendType</label>
            <select id="f-send-type" onchange="markDirty()">
              <option value="Cyclic">Cyclic</option>
              <option value="OnChange">OnChange</option>
              <option value="Spontaneous">Spontaneous</option>
            </select>
          </div>
          <div class="field"><label>isE2E</label><input id="f-e2e" type="number" min="0" value="0" onchange="markDirty()"/></div>
        </div>
        <div class="field-row">
          <div class="field"><label>CycleTime (ms)</label><input id="f-cycle" type="number" min="0" onchange="markDirty()"/></div>
          <div class="field"><label>CycleTimeFast (ms)</label><input id="f-cycle-fast" type="number" min="0" onchange="markDirty()"/></div>
        </div>
        <div class="field"><label>NrOfRepetitions</label><input id="f-repetitions" type="number" min="0" onchange="markDirty()"/></div>
      </div>

      <div class="tab-content" id="tab-routing">
        <div class="field-row">
          <div class="field"><label>Direction</label>
            <select id="f-direction" onchange="markDirty()">
              <option value="0">0 = Source</option><option value="1">1 = Routed</option>
            </select>
          </div>
          <div class="field"><label>RoutingType</label>
            <select id="f-routing-type" onchange="markDirty()">
              <option value="0">0 = Source</option><option value="1">1 = MessageRouting</option><option value="2">2 = SignalRouting</option>
            </select>
          </div>
        </div>
        <div class="field-row">
          <div class="field"><label>TargetDbIds (comma-separated)</label><input id="f-target-dbids" placeholder="e.g. 20, 31" onchange="markDirty()"/></div>
          <div class="field"><label>SourceDbId</label><input id="f-source-dbid" type="number" min="0" onchange="markDirty()"/></div>
        </div>
      </div>

      <div class="tab-content" id="tab-signals">
        <div style="margin-bottom:8px">
          <button class="btn btn-add" onclick="addSignal()">+ Add Signal</button>
          <button class="btn btn-danger" onclick="deleteSelectedSignal()" id="delete-sig-btn" disabled>Delete Selected</button>
        </div>
        <div style="overflow-x:auto">
          <table class="signal-table" id="signal-table">
            <thead><tr>
              <th style="width:30px"><input type="checkbox" onchange="toggleAllSignals(this)"/></th>
              <th>id</th><th>Name</th><th>Bits</th><th>Start</th><th>Order</th><th>Type</th>
              <th>Factor</th><th>Offset</th><th>Min</th><th>Max</th><th>Unit</th><th>Enum</th>
              <th>Mux</th><th>MuxVal</th><th>Comment</th>
            </tr></thead>
            <tbody id="signal-tbody"></tbody>
          </table>
        </div>
      </div>

      <div class="tab-content" id="tab-routes">
        <div style="margin-bottom:8px">
          <button class="btn btn-add" onclick="showAddRouteModal()">+ Add Route</button>
        </div>
        <table id="route-table">
          <thead><tr><th>SrcDbId</th><th>SrcSignalId</th><th>DstDbId</th><th>DstSignalId</th><th></th></tr></thead>
          <tbody id="route-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<div id="toast-container"></div>

<script>
// ── State ──────────────────────────────────────────────────────────────────
let db = {schema_version:"1.0", messages:[], signal_routes:[]};
let currentPath = null;
let selectedMsgId = null;
let selectedSigId = null;
let dirty = false;
let _currentIpduEntries = [];

// ── API helpers ────────────────────────────────────────────────────────────
async function api(method, url, body) {
  const opts = {method, headers:{"Accept":"application/json"}};
  if (body !== undefined) {opts.headers["Content-Type"]="application/json"; opts.body=JSON.stringify(body);}
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function toast(msg, type="info") {
  const el = document.createElement("div");
  el.className = "toast " + type; el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  // Longer messages get more time to read; multiple toasts stack in the
  // container (column-reverse) instead of overlapping at the same spot.
  const duration = Math.min(8000, Math.max(3000, msg.length * 60));
  setTimeout(() => el.remove(), duration);
}

// ── DB operations ─────────────────────────────────────────────────────────
async function newDb() {
  await api("POST","/api/db/new");
  db = await api("GET","/api/db/current");
  currentPath = null; dirty = false; selectedMsgId = null;
  renderMsgList(); showEmptyState();
  refreshFileList();
  toast("New database created","success");
}

async function loadSelected() {
  const sel = document.getElementById("file-select");
  const path = sel.value;
  if (!path) { toast("Select a file from the dropdown first","error"); return; }
  await _loadPath(path);
}

async function loadFile() {
  const fp = prompt("Enter full path to a .json file:");
  if (!fp) return;
  await _loadPath(fp);
}

async function _loadPath(path) {
  try {
    db = await api("GET","/api/db/load?path=" + encodeURIComponent(path));
    currentPath = path; dirty = false; selectedMsgId = null;
    renderMsgList();
    if (db.messages && db.messages.length) selectMessage(db.messages[0].DbId);
    else showEmptyState();
    toast("Loaded " + path.split("/").pop(),"success");
  } catch(e) { toast("Load failed: " + e.message,"error"); }
}

async function refreshFileList() {
  try {
    const r = await api("GET","/api/db/list");
    const sel = document.getElementById("file-select");
    const current = sel.value;
    sel.innerHTML = '<option value="">— select file —</option>' +
      r.files.map(f => `<option value="${r.config_dir}/${f}">${f}</option>`).join("");
    if (current && [...sel.options].some(o => o.value === current)) sel.value = current;
  } catch(e) {}
}

async function saveFile() {
  if (currentPath) {
    const r = await api("POST","/api/db/save", {path:currentPath, force:true});
    if (r.status === "error") { toast(r.errors.join("; "),"error"); return; }
    dirty = false; refreshFileList();
    toast("Saved","success");
  } else {
    saveAs();
  }
}

async function saveAs() {
  const name = prompt("Filename:", "pdu_db_custom.json");
  if (!name) return;
  const fullPath = (await api("GET","/api/db/list")).config_dir + "/" + name;
  const r = await api("POST","/api/db/save", {path:fullPath, force:true});
  if (r.status === "error") { toast(r.errors.join("; "),"error"); return; }
  currentPath = fullPath; dirty = false; refreshFileList();
  toast("Saved as " + name,"success");
}

async function validateDb() {
  const r = await api("GET","/api/db/validate");
  if (r.valid) toast("Database is valid!","success");
  else toast(r.errors.join(" | "),"error");
}

// ── Message list ───────────────────────────────────────────────────────────
function renderMsgList() {
  const list = document.getElementById("msg-list");
  const q = (document.getElementById("msg-search").value || "").toLowerCase();
  const msgs = (db.messages || []).filter(m => (m.MessageName||"").toLowerCase().includes(q) || String(m.DbId).includes(q));
  list.innerHTML = msgs.map(m => `
    <div class="msg-item ${m.DbId === selectedMsgId ? 'active' : ''}" onclick="selectMessage(${m.DbId})">
      <div><div class="msg-name">${esc(m.MessageName)}</div><div class="msg-id">DbId ${m.DbId} · ID 0x${(m.Identifier||0).toString(16).toUpperCase()}</div></div>
      <div style="margin-left:auto;text-align:right">
        <span class="msg-type">${m.BusType||''}</span>
        <span class="msg-bus">${esc(m.Bus||'')}</span>
      </div>
    </div>
  `).join("");
}

function showEmptyState() {
  document.getElementById("empty-state").style.display = "block";
  document.getElementById("editor-pane").style.display = "none";
  selectedMsgId = null;
}

// ── Message selection ──────────────────────────────────────────────────────
async function selectMessage(dbId) {
  selectedMsgId = dbId;
  const msg = db.messages.find(m => m.DbId === dbId);
  if (!msg) return;
  document.getElementById("empty-state").style.display = "none";
  document.getElementById("editor-pane").style.display = "block";
  renderMsgList();
  fillEditor(msg);
  renderSignals(msg);
  renderRoutes();
}

function fillEditor(msg) {
  document.getElementById("f-id").value = msg.DbId || 0;
  document.getElementById("f-name").value = msg.MessageName || "";
  document.getElementById("f-bus").value = msg.Bus || "";
  document.getElementById("f-bus-type").value = msg.BusType || "CAN";
  document.getElementById("f-msg-type").value = msg.MessageType || 0;
  document.getElementById("f-send-type").value = msg.SendType || "Cyclic";
  document.getElementById("f-cycle").value = msg.CycleTime || 0;
  document.getElementById("f-cycle-fast").value = msg.CycleTimeFast || 0;
  document.getElementById("f-repetitions").value = msg.NrOfRepetitions || 0;
  document.getElementById("f-direction").value = msg.Direction || 0;
  document.getElementById("f-routing-type").value = msg.RoutingType || 0;
  document.getElementById("f-target-dbids").value = msg.TargetDbIds ? msg.TargetDbIds.join(", ") : "";
  document.getElementById("f-source-dbid").value = msg.SourceDbId ?? "";
  document.getElementById("f-e2e").value = msg.isE2E || 0;
  document.getElementById("f-can-id").value = msg.Identifier ?? "";
  document.getElementById("f-frame-type").value = msg.FrameType || 0;
  document.getElementById("f-length").value = msg.Length || 8;
  document.getElementById("f-brs").value = msg.BRS ? "true" : "false";
  document.getElementById("f-ether-type").value = msg.EtherType || 2048;
  document.getElementById("f-vlan").value = msg.VlanId || 0;
  document.getElementById("f-src-mac").value = msg.SrcMAC || "";
  document.getElementById("f-dst-mac").value = msg.DstMAC || "";
  document.getElementById("f-src-ip").value = msg.SrcIP || "";
  document.getElementById("f-dst-ip").value = msg.DstIP || "";
  document.getElementById("f-src-port").value = msg.SrcPort ?? "";
  document.getElementById("f-dst-port").value = msg.DstPort ?? "";
  document.getElementById("f-ttl").value = msg.TTL ?? 64;
  _currentIpduEntries = msg.IpduMEntries ? [...msg.IpduMEntries] : [];
  renderPduEntries();
  updatePduDropdown();
  document.getElementById("f-pdu-id").value = msg.PduId ?? "";
  document.getElementById("f-container-db-id").value = msg.ContainerDbId ?? "";
  document.getElementById("f-eth-pdu-length").value = msg.Length ?? 8;
  onBusTypeChange();
}

function onBusTypeChange() {
  const bt = document.getElementById("f-bus-type").value;
  document.getElementById("bus-can").style.display = (bt==="CAN"||bt==="CANFD") ? "block" : "none";
  document.getElementById("bus-eth").style.display = bt==="ETH" ? "block" : "none";
  document.getElementById("bus-eth-pdu").style.display = bt==="ETH_PDU" ? "block" : "none";
  if (bt === "ETH") { renderPduEntries(); updatePduDropdown(); }
}

function markDirty() { dirty = true; }

async function saveCurrentMessage() {
  if (!selectedMsgId) return;
  const msg = collectForm();
  const r = await api("PUT","/api/messages/"+selectedMsgId, msg);
  db = await api("GET","/api/db/current");
  renderMsgList();
  renderSignals(msg);
  dirty = false;
}

function collectForm() {
  const bt = document.getElementById("f-bus-type").value;
  const msg = {
    DbId: parseInt(document.getElementById("f-id").value) || 0,
    MessageName: document.getElementById("f-name").value || "",
    Bus: document.getElementById("f-bus").value || "",
    BusType: bt,
    MessageType: parseInt(document.getElementById("f-msg-type").value) || 0,
    Direction: parseInt(document.getElementById("f-direction").value) || 0,
    RoutingType: parseInt(document.getElementById("f-routing-type").value) || 0,
    TargetDbIds: parseCsvIds(document.getElementById("f-target-dbids").value),
    SourceDbId: parseInt(document.getElementById("f-source-dbid").value) || null,
    isE2E: parseInt(document.getElementById("f-e2e").value) || 0,
    SendType: document.getElementById("f-send-type").value || "Cyclic",
    CycleTime: parseInt(document.getElementById("f-cycle").value) || 0,
    CycleTimeFast: parseInt(document.getElementById("f-cycle-fast").value) || 0,
    NrOfRepetitions: parseInt(document.getElementById("f-repetitions").value) || 0,
    signalcount: 0,
    signals: [],
  };
  if (bt === "CAN" || bt === "CANFD") {
    msg.Identifier = parseInt(document.getElementById("f-can-id").value) || 0;
    msg.FrameType = parseInt(document.getElementById("f-frame-type").value) || 0;
    msg.Length = parseInt(document.getElementById("f-length").value) || 8;
    msg.BRS = document.getElementById("f-brs").value === "true";
  }
  if (bt === "ETH") {
    msg.EtherType = parseInt(document.getElementById("f-ether-type").value) || 2048;
    msg.VlanId = parseInt(document.getElementById("f-vlan").value) || 0;
    msg.SrcMAC = document.getElementById("f-src-mac").value || "";
    msg.DstMAC = document.getElementById("f-dst-mac").value || "";
    msg.SrcIP = document.getElementById("f-src-ip").value || "";
    msg.DstIP = document.getElementById("f-dst-ip").value || "";
    msg.SrcPort = parseInt(document.getElementById("f-src-port").value) || 0;
    msg.DstPort = parseInt(document.getElementById("f-dst-port").value) || 0;
    msg.TTL = parseInt(document.getElementById("f-ttl").value) || 64;
    msg.IpduMEntries = _currentIpduEntries.length ? _currentIpduEntries : null;
    msg.signalcount = 0;
    msg.signals = [];
  }
  if (bt === "ETH_PDU") {
    msg.PduId = parseInt(document.getElementById("f-pdu-id").value) || 0;
    msg.ContainerDbId = parseInt(document.getElementById("f-container-db-id").value) || null;
    msg.Length = parseInt(document.getElementById("f-eth-pdu-length").value) || 8;
  }
  return msg;
}

function parseCsvIds(val) {
  if (!val || !val.trim()) return null;
  const ids = val.split(",").map(s => parseInt(s.trim())).filter(n => !isNaN(n));
  return ids.length ? ids : null;
}

async function newMessage() {
  const r = await api("POST","/api/messages", {bus_type:"CAN"});
  db = await api("GET","/api/db/current");
  renderMsgList();
  selectMessage(r.DbId);
  saveCurrentMessage();
  toast("Created message DbId " + r.DbId,"success");
}

async function deleteMessage() {
  if (!selectedMsgId || !confirm("Delete this message?")) return;
  await api("DELETE","/api/messages/"+selectedMsgId);
  db = await api("GET","/api/db/current");
  selectedMsgId = null;
  renderMsgList();
  showEmptyState();
  toast("Message deleted","info");
}

// ── Signal editing ─────────────────────────────────────────────────────────
function renderSignals(msg) {
  const tb = document.getElementById("signal-tbody");
  if (!msg || !msg.signals) { tb.innerHTML = ""; return; }
  const msgId = msg.DbId;

  function _inp(sig, field, attrs, extra) {
    const val = sig[field];
    const e = extra || "";
    if (field === "EnumValues") {
      return `<input value="${val ? esc(JSON.stringify(val)) : ''}" placeholder='{"0":"Off"}' onchange="updateSignalField(${msgId},${sig.id},'EnumValues',parseEnum(this.value))" onclick="event.stopPropagation()" ${attrs||""}/>`;
    }
    return `<input value="${esc(String(val??''))}" onchange="updateSignalField(${msgId},${sig.id},'${field}',this.value)" onclick="event.stopPropagation()" ${attrs||""}/>`;
  }
  function _num(sig, field, attrs) {
    const val = sig[field];
    return `<input value="${val??''}" onchange="updateSignalField(${msgId},${sig.id},'${field}',parseFloat(this.value)||0)" onclick="event.stopPropagation()" ${attrs||""}/>`;
  }
  function _sel(sig, field, opts) {
    return `<select onchange="updateSignalField(${msgId},${sig.id},'${field}',this.value)" onclick="event.stopPropagation()">${opts.map(o => `<option value="${o[0]}" ${String(sig[field])===String(o[0])?'selected':''}>${o[1]||o[0]}</option>`).join("")}</select>`;
  }

  tb.innerHTML = msg.signals.map(s => {
    const checked = _checkedSigs.has(s.id);
    const isMux = s.IsMuxor || false;
    const muxVal = s.MuxValue !== undefined && s.MuxValue !== null ? s.MuxValue : '';
    const muxCheck = `<input type="checkbox" ${isMux?'checked':''} onchange="updateSignalField(${msgId},${s.id},'IsMuxor',this.checked)" onclick="event.stopPropagation()"/>`;
    const muxValInp = `<input value="${muxVal}" placeholder="null" onchange="updateSignalField(${msgId},${s.id},'MuxValue',this.value===''?null:parseInt(this.value))" onclick="event.stopPropagation()" type="number" min="0"/>`;
    return `<tr class="${s.id===selectedSigId?'selected':''}" onclick="selectSig(${s.id})">
      <td><input type="checkbox" ${checked?'checked':''} onclick="event.stopPropagation()" onchange="onSigCheck(${s.id},this.checked)"/></td>
      <td>${s.id}</td>
      <td>${_inp(s,'SignalName')}</td>
      <td>${_num(s,'Length','type="number" min="1"')}</td>
      <td>${_num(s,'StartPos','type="number" min="0"')}</td>
      <td>${_sel(s,'ByteOrder',[[0,'Intel'],[1,'Motorola']])}</td>
      <td>${_sel(s,'ValueType',[['Unsigned'],['Signed'],['Float'],['Bool']])}</td>
      <td>${_num(s,'Factor','type="number" step="any"')}</td>
      <td>${_num(s,'Offset','type="number" step="any"')}</td>
      <td>${_num(s,'Min','type="number" step="any"')}</td>
      <td>${_num(s,'Max','type="number" step="any"')}</td>
      <td>${_inp(s,'Unit')}</td>
      <td>${_inp(s,'EnumValues')}</td>
      <td>${muxCheck}</td>
      <td>${muxValInp}</td>
      <td>${_inp(s,'Comment')}</td>
    </tr>`;
  }).join("");
}

let _checkedSigs = new Set();

function onSigCheck(sigId, checked) {
  if (checked) _checkedSigs.add(sigId);
  else _checkedSigs.delete(sigId);
  updateDeleteSigBtn();
}

function selectSig(id) {
  selectedSigId = selectedSigId === id ? null : id;
  renderSignals(db.messages.find(m => m.DbId === selectedMsgId));
}

function toggleAllSignals(cb) {
  if (cb.checked) {
    const msg = db.messages.find(m => m.DbId === selectedMsgId);
    if (msg) msg.signals.forEach(s => _checkedSigs.add(s.id));
  } else {
    _checkedSigs.clear();
  }
  renderSignals(db.messages.find(m => m.DbId === selectedMsgId));
  updateDeleteSigBtn();
}

function updateDeleteSigBtn() {
  document.getElementById("delete-sig-btn").disabled = _checkedSigs.size === 0;
}

// ── PDU entries for ETH containers ─────────────────────────────────────────
function renderPduEntries() {
  const tb = document.getElementById("pdu-entries-tbody");
  if (!_currentIpduEntries || !_currentIpduEntries.length) {
    tb.innerHTML = '<tr><td colspan="5" style="padding:8px;color:var(--muted);text-align:center;font-family:sans-serif">No PDUs attached</td></tr>';
    return;
  }
  tb.innerHTML = _currentIpduEntries.map(dbid => {
    const pdu = db.messages.find(m => m.DbId === dbid);
    const name = pdu ? pdu.MessageName : `? (DbId ${dbid})`;
    const pduId = pdu ? (pdu.PduId ?? "—") : "—";
    const cycle = pdu ? (pdu.CycleTime || "—") : "—";
    return `<tr>
      <td style="padding:4px 6px;border-bottom:1px solid var(--border);font-family:var(--mono);font-size:11px">${dbid}</td>
      <td style="padding:4px 6px;border-bottom:1px solid var(--border);font-family:var(--mono);font-size:11px">${esc(name)}</td>
      <td style="padding:4px 6px;border-bottom:1px solid var(--border);font-family:var(--mono);font-size:11px">${pduId}</td>
      <td style="padding:4px 6px;border-bottom:1px solid var(--border);font-family:var(--mono);font-size:11px">${cycle}</td>
      <td style="padding:4px 6px;border-bottom:1px solid var(--border)"><button onclick="removePduEntry(${dbid})" style="padding:1px 6px;border:1px solid var(--red);border-radius:3px;background:transparent;color:var(--red);cursor:pointer;font-size:10px">×</button></td>
    </tr>`;
  }).join("");
}

function updatePduDropdown() {
  const sel = document.getElementById("f-add-pdu");
  const currentVal = sel.value;
  sel.innerHTML = '<option value="">— add ETH_PDU —</option>';
  const used = new Set(_currentIpduEntries || []);
  db.messages.forEach(m => {
    if (m.BusType === "ETH_PDU" && !used.has(m.DbId)) {
      const opt = document.createElement("option");
      opt.value = m.DbId;
      opt.textContent = `DbId ${m.DbId} — ${m.MessageName}`;
      sel.appendChild(opt);
    }
  });
  if (currentVal && [...sel.options].some(o => o.value === currentVal)) sel.value = currentVal;
}

async function addPduEntry() {
  const sel = document.getElementById("f-add-pdu");
  const dbid = parseInt(sel.value);
  if (!dbid) { toast("Select an ETH_PDU from the dropdown","error"); return; }
  if (!_currentIpduEntries.includes(dbid)) {
    _currentIpduEntries.push(dbid);
    renderPduEntries();
    updatePduDropdown();
    await saveCurrentMessage();
    toast("Added PDU DbId " + dbid,"success");
  }
}

async function removePduEntry(dbid) {
  _currentIpduEntries = _currentIpduEntries.filter(id => id !== dbid);
  renderPduEntries();
  updatePduDropdown();
  await saveCurrentMessage();
  toast("Removed PDU DbId " + dbid,"info");
}

async function updateSignalField(dbId, sigId, field, value) {
  const msg = db.messages.find(m => m.DbId === dbId);
  if (!msg) return;
  const sig = msg.signals.find(s => s.id === sigId);
  if (!sig) return;
  sig[field] = value;
  await api("PUT","/api/messages/"+dbId, msg);
  db = await api("GET","/api/db/current");
}

function parseEnum(val) {
  if (!val || !val.trim()) return null;
  try { return JSON.parse(val); } catch(e) { return null; }
}

async function addSignal() {
  if (!selectedMsgId) return;
  const msg = db.messages.find(m => m.DbId === selectedMsgId);
  if (!msg) return;
  const existingIds = (msg.signals||[]).map(s => s.id);
  const nextId = existingIds.length ? Math.max(...existingIds) + 1 : 1;
  const startPos = (msg.signals||[]).reduce((max, s) => Math.max(max, s.StartPos + s.Length), 0);
  const sig = {
    id: nextId,
    SignalName: "Signal_" + nextId,
    Length: 1,
    StartPos: startPos,
    ByteOrder: 0,
    ValueType: "Unsigned",
    SigSendType: false,
    Repetitions: 0,
    InitValue: 0,
    Factor: 1.0,
    Offset: 0.0,
    Min: 0.0,
    Max: 1.0,
    Unit: "",
    EnumValues: null,
    IsMuxor: false,
    MuxValue: null,
    Comment: "",
  };
  msg.signals.push(sig);
  msg.signalcount = msg.signals.length;
  await api("PUT","/api/messages/"+selectedMsgId, msg);
  db = await api("GET","/api/db/current");
  renderSignals(db.messages.find(m => m.DbId === selectedMsgId));
}

async function deleteSelectedSignal() {
  if (!selectedMsgId || _checkedSigs.size === 0) return;
  const ids = [..._checkedSigs];
  if (!confirm(`Delete ${ids.length} selected signal(s)?`)) return;
  const msg = db.messages.find(m => m.DbId === selectedMsgId);
  if (!msg) return;
  msg.signals = msg.signals.filter(s => !ids.includes(s.id));
  msg.signalcount = msg.signals.length;
  _checkedSigs.clear();
  await api("PUT","/api/messages/"+selectedMsgId, msg);
  db = await api("GET","/api/db/current");
  renderSignals(msg);
  updateDeleteSigBtn();
}

// ── Routes ─────────────────────────────────────────────────────────────────
function renderRoutes() {
  const tb = document.getElementById("route-tbody");
  const routes = db.signal_routes || [];
  tb.innerHTML = routes.map(r => `
    <tr>
      <td>${r.SrcDbId} (${nameForId(r.SrcDbId)})</td>
      <td>${r.SrcSignalId}</td>
      <td>${r.DstDbId} (${nameForId(r.DstDbId)})</td>
      <td>${r.DstSignalId}</td>
      <td><button class="btn-danger" onclick="deleteRoute(${r.SrcDbId},${r.SrcSignalId},${r.DstDbId},${r.DstSignalId})">×</button></td>
    </tr>
  `).join("");
}

function nameForId(dbId) {
  const m = (db.messages||[]).find(x => x.DbId === dbId);
  return m ? m.MessageName : "?";
}

function showAddRouteModal() {
  const src = prompt("Source DbId:");
  if (!src) return;
  const srcSig = prompt("Source Signal ID:");
  if (!srcSig) return;
  const dst = prompt("Destination DbId:");
  if (!dst) return;
  const dstSig = prompt("Destination Signal ID:");
  if (!dstSig) return;
  addRoute(parseInt(src), parseInt(srcSig), parseInt(dst), parseInt(dstSig));
}

async function addRoute(src, srcSig, dst, dstSig) {
  await api("POST","/api/routes", {src_db_id:src, src_sig_id:srcSig, dst_db_id:dst, dst_sig_id:dstSig});
  db = await api("GET","/api/db/current");
  renderRoutes();
}

async function deleteRoute(src, srcSig, dst, dstSig) {
  await api("DELETE","/api/routes", {src_db_id:src, src_sig_id:srcSig, dst_db_id:dst, dst_sig_id:dstSig});
  db = await api("GET","/api/db/current");
  renderRoutes();
}

// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.toggle("active", c.id === "tab-" + name));
  if (selectedMsgId) {
    const msg = db.messages.find(m => m.DbId === selectedMsgId);
    if (name === "signals" && msg) renderSignals(msg);
    if (name === "bus" && msg && msg.BusType === "ETH") { renderPduEntries(); updatePduDropdown(); }
    if (name === "routes") renderRoutes();
  }
}

// ── Auto-save on blur ──────────────────────────────────────────────────────
document.addEventListener("change", function(e) {
  if (!selectedMsgId) return;
  if (e.target.closest("#editor-pane") && !e.target.closest("#signal-table") && !e.target.closest("#route-table")) {
    const msg = collectForm();
    const idx = db.messages.findIndex(m => m.DbId === selectedMsgId);
    if (idx >= 0) {
      db.messages[idx] = msg;
      api("PUT","/api/messages/"+selectedMsgId, msg).then(() => {
        db = db; renderMsgList();
      });
    }
  }
});

// ── Nav links ──────────────────────────────────────────────────────────────
(function() {
  const h = window.location.hostname, p = window.location.port;
  document.querySelectorAll('.nav-link').forEach(a => {
    a.href = 'http://' + h + ':' + a.dataset.port + '/';
    if (a.dataset.port === p) a.classList.add('active');
  });
})();

// ── Esc helper ─────────────────────────────────────────────────────────────
function esc(s) {
  if (s == null) return "";
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;").replace(/'/g,"&#39;").replace(/\\/g,"&#92;");
}

// ── Load initial data ──────────────────────────────────────────────────────
(async function init() {
  await refreshFileList();
  try {
    db = await api("GET","/api/db/current");
    if (db.messages && db.messages.length) {
      renderMsgList();
      selectMessage(db.messages[0].DbId);
    } else {
      showEmptyState();
    }
  } catch(e) {
    showEmptyState();
  }
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
    print(f"BoAt PDU Database Editor → http://localhost:{_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
