"""
BoAt Platform — Gateway Launcher
Network interface setup, gateway lifecycle management, and live debug output.

Usage:
    python3 demo/launcher.py
    # Open http://localhost:8086

Environment:
    BOAT_LAUNCHER_PORT  — HTTP port (default 8086)
    BOAT_GATEWAY_BIN    — path to boat_gateway binary
    BOAT_GATEWAY        — gRPC address for state queries (default localhost:50051)

Requires passwordless sudo for:
    modprobe vcan
    ip link add/del/set (vcan and veth creation)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "boat-platform" / "sdk" / "python"))

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

# ── Configuration ─────────────────────────────────────────────────────────────

_DEMO_DIR       = Path(__file__).parent
_PROJECT_ROOT   = _DEMO_DIR.parent / "boat-platform"
_DEFAULT_GW_BIN = str(_PROJECT_ROOT / "build" / "debug" / "src" / "gateway" / "grpc_gateway" / "boat_gateway")
_GW_BIN         = os.environ.get("BOAT_GATEWAY_BIN", _DEFAULT_GW_BIN)
_GW_ADDR        = os.environ.get("BOAT_GATEWAY", "localhost:50051")
_LOG_LINES      = 500
_PORT           = int(os.environ.get("BOAT_LAUNCHER_PORT", "8086"))

_SIM_STATE_NAMES = {0: "UNSPECIFIED", 1: "IDLE", 2: "RUNNING", 3: "PAUSED", 4: "STOPPED", 5: "ERROR"}

# ── Gateway subprocess management ────────────────────────────────────────────

@dataclass
class GatewayProcess:
    process: Optional[subprocess.Popen] = None
    exit_code: Optional[int] = None
    started_at: Optional[float] = None
    can_interfaces: str = ""
    eth_interfaces: str = ""
    _log: deque = field(default_factory=lambda: deque(maxlen=_LOG_LINES))
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _log_thread: Optional[threading.Thread] = None

    def append_log(self, line: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        with self._lock:
            self._log.append({"ts": ts, "text": line.rstrip()})

    def get_log(self) -> List[dict]:
        with self._lock:
            return list(self._log)

    @property
    def running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    @property
    def status(self) -> str:
        if self.process is None:
            return "stopped"
        code = self.process.poll()
        if code is None:
            return "running"
        return "stopped" if code == 0 else f"exited:{code}"

    def pid(self) -> Optional[int]:
        if self.running and self.process is not None:
            return self.process.pid
        return None

    def uptime(self) -> Optional[float]:
        if self.started_at is None or not self.running:
            return None
        return time.time() - self.started_at

    def start(self, can: str, eth: str) -> None:
        with self._lock:
            # Prevent duplicate gateway instances
            if _PID_FILE.exists():
                try:
                    existing_pid = int(_PID_FILE.read_text().strip())
                    if _is_pid_alive(existing_pid):
                        self.append_log(f"gateway PID {existing_pid} already running — refusing to start another")
                        raise RuntimeError(f"Gateway PID {existing_pid} is already running")
                except (ValueError, OSError):
                    pass
                _cleanup_pid_file()
            if self.running:
                return
            env = os.environ.copy()
            if can:
                env["BOAT_CAN_INTERFACES"] = can
            if eth:
                env["BOAT_ETH_INTERFACES"] = eth
            self.can_interfaces = can
            self.eth_interfaces = eth
            self.exit_code = None
            self.started_at = time.time()
            self.process = subprocess.Popen(
                [_GW_BIN],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                text=True,
                bufsize=1,
            )
            self.append_log(f"[launcher] started PID {self.process.pid}")
            try:
                _PID_FILE.write_text(str(self.process.pid))
            except OSError:
                pass
            self._log_thread = threading.Thread(target=self._drain_output, daemon=True, name="gw-log")
            self._log_thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self.running or self.process is None:
                return
            self.append_log("[launcher] sending SIGTERM…")
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.append_log("[launcher] timeout — sending SIGKILL")
                self.process.kill()
                self.process.wait()
            self.exit_code = self.process.returncode
            self.append_log(f"[launcher] exited with code {self.exit_code}")
            _cleanup_pid_file()
            self.process = None

    def _drain_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.append_log(line)
        except ValueError:
            pass
        with self._lock:
            if self.process:
                self.exit_code = self.process.wait()
                _cleanup_pid_file()
                self.process = None


_gw = GatewayProcess()

_PID_FILE = Path("/tmp/boat_gateway.pid")

def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False

def _cleanup_pid_file() -> None:
    try:
        if _PID_FILE.exists():
            _PID_FILE.unlink()
    except OSError:
        pass

# User-registered Ethernet interfaces (not backed by system devices)
_virtual_eth_ifaces: set = set()
_raw_eth_ifaces: set = set()

# ── Interface helpers ─────────────────────────────────────────────────────────

def _list_interfaces() -> List[dict]:
    """Return all system interfaces with type, state, flags via ip -j link show."""
    try:
        raw = subprocess.run(["ip", "-j", "link", "show"], capture_output=True, text=True, check=True)
        all_ifaces: list = json.loads(raw.stdout)

        vcan_names: set = set()
        try:
            vraw = subprocess.run(["ip", "-j", "link", "show", "type", "vcan"], capture_output=True, text=True)
            if vraw.returncode == 0:
                for e in json.loads(vraw.stdout):
                    vcan_names.add(e["ifname"])
        except Exception:
            pass

        out = []
        for iface in all_ifaces:
            name = iface["ifname"]
            flags = iface.get("flags", [])
            link_type = iface.get("link_type", "")

            if name in vcan_names:
                iface_type = "vcan"
            elif link_type == "ether" and "veth" in name:
                iface_type = "veth"
            elif link_type == "ether":
                iface_type = "ether"
            elif link_type == "loopback":
                iface_type = "loopback"
            else:
                iface_type = link_type or "other"

            out.append({
                "name": name,
                "type": iface_type,
                "up": "UP" in flags,
                "lower_up": "LOWER_UP" in flags,
                "operstate": iface.get("operstate", "UNKNOWN"),
                "mac": iface.get("address", ""),
            })
        for name in sorted(_virtual_eth_ifaces):
            if not any(i["name"] == name for i in out):
                out.append({"name": name, "type": "eth-virtual", "up": True, "lower_up": True, "operstate": "UNKNOWN", "mac": ""})
        for name in sorted(_raw_eth_ifaces):
            if not any(i["name"] == name for i in out):
                out.append({"name": name, "type": "eth-raw", "up": True, "lower_up": True, "operstate": "UNKNOWN", "mac": ""})
        return out
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to list interfaces: {e}")


def _sudo_ip(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["sudo", "-n", "ip"] + args, capture_output=True, text=True, check=check)

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI()


@app.get("/api/interfaces")
def api_list_interfaces():
    return {"interfaces": _list_interfaces()}


@app.post("/api/interfaces/vcan")
def api_create_vcan(name: str = "vcan0"):
    try:
        subprocess.run(["sudo", "-n", "modprobe", "vcan"], capture_output=True, check=False)
        _sudo_ip(["link", "add", name, "type", "vcan"])
        _sudo_ip(["link", "set", name, "up"])
        return {"ok": True, "name": name}
    except subprocess.CalledProcessError as e:
        detail = e.stderr.strip() or str(e)
        raise HTTPException(status_code=400, detail=detail)


@app.delete("/api/interfaces/vcan/{name}")
def api_delete_vcan(name: str):
    try:
        _sudo_ip(["link", "delete", name])
        return {"ok": True, "name": name}
    except subprocess.CalledProcessError as e:
        detail = e.stderr.strip() or str(e)
        raise HTTPException(status_code=400, detail=detail)


@app.post("/api/interfaces/veth")
def api_create_veth(name: str = "veth0"):
    peer = f"{name}_peer"
    try:
        _sudo_ip(["link", "add", name, "type", "veth", "peer", "name", peer])
        _sudo_ip(["link", "set", name, "up"])
        _sudo_ip(["link", "set", peer, "up"])
        return {"ok": True, "interfaces": [name, peer]}
    except subprocess.CalledProcessError as e:
        detail = e.stderr.strip() or str(e)
        raise HTTPException(status_code=400, detail=detail)


@app.delete("/api/interfaces/veth/{name}")
def api_delete_veth(name: str):
    try:
        _sudo_ip(["link", "delete", name])
        return {"ok": True, "name": name}
    except subprocess.CalledProcessError as e:
        detail = e.stderr.strip() or str(e)
        raise HTTPException(status_code=400, detail=detail)


@app.post("/api/interfaces/eth/virtual")
def api_create_eth_virtual(name: str = "eth0"):
    _virtual_eth_ifaces.add(name)
    return {"ok": True, "name": name}


@app.delete("/api/interfaces/eth/virtual/{name}")
def api_delete_eth_virtual(name: str):
    _virtual_eth_ifaces.discard(name)
    return {"ok": True, "name": name}


@app.post("/api/interfaces/eth/raw")
def api_create_eth_raw(name: str = "eth0"):
    _raw_eth_ifaces.add(name)
    return {"ok": True, "name": name, "env_value": f"raw:{name}"}


@app.delete("/api/interfaces/eth/raw/{name}")
def api_delete_eth_raw(name: str):
    _raw_eth_ifaces.discard(name)
    return {"ok": True, "name": name}


@app.post("/api/gateway/start")
def api_gateway_start(can: str = "", eth: str = ""):
    if not os.path.isfile(_GW_BIN):
        raise HTTPException(status_code=400, detail=f"Gateway binary not found: {_GW_BIN}")
    try:
        _gw.start(can.strip(), eth.strip())
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, "pid": _gw.pid()}


@app.post("/api/gateway/stop")
def api_gateway_stop():
    _gw.stop()
    return {"ok": True}


@app.get("/api/gateway/status")
def api_gateway_status():
    return {
        "running": _gw.running,
        "status": _gw.status,
        "pid": _gw.pid(),
        "uptime_sec": _gw.uptime(),
        "exit_code": _gw.exit_code,
        "can_interfaces": _gw.can_interfaces,
        "eth_interfaces": _gw.eth_interfaces,
    }


@app.get("/api/gateway/log")
def api_gateway_log():
    return {"log": _gw.get_log()}


@app.get("/api/simulation/state")
def api_simulation_state():
    if not _gw.running:
        return {"connected": False, "error": "gateway not running"}
    try:
        from boat.client import BoAtClient
        from boat.v1 import simulation_pb2
        client = BoAtClient(_GW_ADDR)
        req = simulation_pb2.GetSimulationStateRequest(simulation_id="")
        resp = client.simulation.GetSimulationState(req)
        sim = resp.simulation
        return {
            "connected": True,
            "state": _SIM_STATE_NAMES.get(sim.state, "UNKNOWN"),
            "state_code": sim.state,
            "tick": sim.tick,
            "simulation_id": sim.simulation_id,
            "scenario_id": sim.scenario_id,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}


@app.get("/api/health")
def api_health():
    can_sudo = True
    try:
        subprocess.run(["sudo", "-n", "true"], capture_output=True, check=True)
    except Exception:
        can_sudo = False
    return {
        "ok": True,
        "gateway_bin": _GW_BIN,
        "gateway_bin_exists": os.path.isfile(_GW_BIN),
        "can_sudo": can_sudo,
        "gateway_running": _gw.running,
    }


# ── PDU database import ─────────────────────────────────────────────────────

_CONFIG_DIR = _PROJECT_ROOT / "config"
_LAST_IMPORT_FILE = _CONFIG_DIR / ".last_import"


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
    path = _CONFIG_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    try:
        sys.path.insert(0, str(_PROJECT_ROOT / "sdk" / "python"))
        from boat.pdu_db import PduDatabase
        db = PduDatabase(str(path))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse PDU DB: {e}")

    can_buses = set()
    eth_buses = set()
    for msg in db.messages():
        bt = msg.get("BusType", "")
        bus = msg.get("Bus", "")
        if bt in ("CAN", "CANFD"):
            can_buses.add(bus)
        elif bt in ("ETH", "ETH_PDU"):
            eth_buses.add(bus)

    created_can = []
    all_can_ifaces = []
    for bus_name in sorted(can_buses):
        iface_name = bus_name.lower().replace(" ", "_")
        if len(iface_name) > 15:
            iface_name = iface_name[:15]
        all_can_ifaces.append(iface_name)
        try:
            subprocess.run(["sudo", "-n", "modprobe", "vcan"], capture_output=True, check=False)
            _sudo_ip(["link", "add", iface_name, "type", "vcan"])
            _sudo_ip(["link", "set", iface_name, "up"])
            created_can.append(iface_name)
        except Exception:
            try:
                _sudo_ip(["link", "set", iface_name, "up"], check=False)
            except Exception:
                pass

    created_eth = []
    all_eth_ifaces = []
    for bus_name in sorted(eth_buses):
        iface_name = bus_name.lower().replace(" ", "_")
        if len(iface_name) > 15:
            iface_name = iface_name[:15]
        all_eth_ifaces.append(iface_name)
        if iface_name not in _virtual_eth_ifaces:
            created_eth.append(iface_name)
        _virtual_eth_ifaces.add(iface_name)

    _LAST_IMPORT_FILE.write_text(filename)

    return {
        "ok": True,
        "filename": filename,
        "can_buses": sorted(can_buses),
        "eth_buses": sorted(eth_buses),
        "created_can": created_can,
        "created_eth": created_eth,
        "selected_can": all_can_ifaces,
        "selected_eth": all_eth_ifaces,
    }


@app.get("/api/pdu/select")
def api_pdu_select(filename: str):
    """Just parse and return bus info without creating interfaces."""
    path = _CONFIG_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    try:
        sys.path.insert(0, str(_PROJECT_ROOT / "sdk" / "python"))
        from boat.pdu_db import PduDatabase
        db = PduDatabase(str(path))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse PDU DB: {e}")

    can_buses = set()
    eth_buses = set()
    can_ifaces = set()
    eth_ifaces = set()
    for msg in db.messages():
        bt = msg.get("BusType", "")
        bus = msg.get("Bus", "")
        raw = bus.lower().replace(" ", "_")
        if len(raw) > 15:
            raw = raw[:15]
        if bt in ("CAN", "CANFD"):
            can_buses.add(bus)
            can_ifaces.add(raw)
        elif bt in ("ETH", "ETH_PDU"):
            eth_buses.add(bus)
            eth_ifaces.add(raw)

    # Check if interfaces already exist
    existing_ifaces = {i["name"] for i in _list_interfaces()}
    existing_can = [n for n in can_ifaces if n in existing_ifaces]
    existing_eth = [n for n in eth_ifaces if n in _virtual_eth_ifaces]

    return {
        "ok": True,
        "filename": filename,
        "can_buses": sorted(can_buses),
        "eth_buses": sorted(eth_buses),
        "needed_can": sorted(can_ifaces - existing_ifaces),
        "needed_eth": sorted(eth_ifaces - _virtual_eth_ifaces),
        "existing_can": existing_can,
        "existing_eth": existing_eth,
    }

# ── HTML ──────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>BoAt — Launcher</title>
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
  .spacer     { flex: 1; }
  .gw-badge {
    font-size: 11px; padding: 2px 10px; border-radius: 12px;
    background: #1f3a1f; color: var(--green); border: 1px solid #2ea043;
    font-family: var(--mono);
  }

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
    cursor: pointer;
  }
  #panel-nav a:hover { background: #21262d; color: var(--text); }
  #panel-nav a.active { color: var(--blue); background: rgba(88,166,255,.10); font-weight: 600; }

  /* ── three-col layout ── */
  .layout {
    display: flex;
    gap: 0;
    height: calc(100vh - 78px);
    overflow: hidden;
  }
  .col {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    border-right: 1px solid var(--border);
    overflow: hidden;
  }
  .col:last-child { border-right: none; }
  .col-left  { flex: 0 0 35%; }
  .col-mid   { flex: 0 0 35%; }
  .col-right { flex: 1; }

  /* ── pane ── */
  .pane {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
  }
  .pane-header {
    height: 36px; padding: 0 12px;
    display: flex; align-items: center; gap: 8px;
    border-bottom: 1px solid var(--border);
    background: var(--panel); flex-shrink: 0;
  }
  .pane-title {
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: .8px; color: var(--muted);
  }
  .pane-body {
    flex: 1;
    overflow-y: auto;
    padding: 10px 12px;
  }

  /* ── interface list ── */
  .iface-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border-bottom: 1px solid rgba(48,54,61,.35);
    font-size: 12px;
    font-family: var(--mono);
    transition: background .1s;
  }
  .iface-item:hover { background: #1c2128; }

  .iface-pill {
    display: inline-block; padding: 1px 7px; border-radius: 10px;
    font-size: 10px; font-weight: 600;
  }
  .iface-name    { flex: 1; overflow: hidden; text-overflow: ellipsis; }
  .iface-state   { font-size: 10px; padding: 1px 5px; border-radius: 3px; }
  .state-up      { color: var(--green); }
  .state-down    { color: var(--muted); }

  .btn-remove {
    background: none; border: none; color: var(--muted);
    cursor: pointer; font-size: 13px; padding: 0 2px;
    line-height: 1;
  }
  .btn-remove:hover { color: var(--red); }

  .iface-check {
    accent-color: var(--blue);
    width: 14px; height: 14px; cursor: pointer;
  }

  /* ── forms ── */
  .form-row {
    display: flex;
    gap: 6px;
    margin-top: 8px;
  }
  .form-row input {
    flex: 1;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 11px;
    font-family: var(--mono);
    outline: none;
  }
  .form-row input:focus { border-color: var(--blue); }

  .btn {
    font-size: 11px; padding: 4px 12px;
    border-radius: 5px; cursor: pointer;
    border: 1px solid var(--border);
    font-weight: 600;
    transition: background .15s, border-color .15s;
    white-space: nowrap;
  }
  .btn:disabled { opacity: .4; cursor: not-allowed; }
  .btn-primary {
    background: #1c3a5c; color: var(--blue); border-color: var(--blue);
  }
  .btn-primary:hover:not(:disabled) { background: #24497a; }
  .btn-start {
    background: #1a3a1a; color: var(--green); border-color: #2ea043;
  }
  .btn-start:hover:not(:disabled) { background: #1f4a1f; border-color: var(--green); }
  .btn-stop {
    background: #3a1a1a; color: var(--red); border-color: #8b2020;
  }
  .btn-stop:hover:not(:disabled) { background: #4a2020; border-color: var(--red); }
  .btn-danger {
    background: #3a1a1a; color: var(--red); border-color: #8b2020;
  }
  .btn-danger:hover:not(:disabled) { background: #4a2020; }
  .btn-sm {
    font-size: 10px; padding: 2px 8px;
  }

  .assign-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
    border-bottom: 1px solid rgba(48,54,61,.2);
    font-size: 12px;
    font-family: var(--mono);
  }
  .assign-label {
    color: var(--muted);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: .6px;
    flex-shrink: 0;
    width: 40px;
  }
  .assign-value {
    flex: 1;
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* ── status card ── */
  .status-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }
  .stat-item {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .stat-label {
    font-size: 10px; font-weight: 600; text-transform: uppercase;
    letter-spacing: .6px; color: var(--muted);
  }
  .stat-value {
    font-family: var(--mono);
    font-size: 13px;
  }

  .status-dot {
    width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
    display: inline-block;
    background: var(--muted);
    transition: background .3s;
  }
  .status-dot.running  { background: var(--green); animation: pulse 2s infinite; }
  .status-dot.exited   { background: var(--red); }
  .status-dot.stopped  { background: var(--muted); }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

  .state-badge {
    display: inline-block;
    font-size: 10px; padding: 2px 8px; border-radius: 10px;
    font-weight: 600;
    font-family: var(--mono);
  }
  .state-RUNNING    { background: #1f3a1f; color: var(--green); }
  .state-PAUSED     { background: #2d2208; color: var(--yellow); }
  .state-STOPPED    { background: #3d0b0b; color: var(--red); }
  .state-IDLE       { background: #1c2128; color: var(--muted); }
  .state-ERROR      { background: #3d0b0b; color: var(--red); }
  .state-UNSPECIFIED{ background: #1c2128; color: var(--muted); }
  .state-disconnected { background: #1c2128; color: var(--muted); }

  .actions {
    display: flex;
    gap: 8px;
    margin-top: 12px;
  }
  .actions .btn { flex: 1; text-align: center; }

  /* ── log panel ── */
  .log-scroll {
    flex: 1;
    overflow-y: auto;
    padding: 8px 12px;
    font-family: var(--mono);
    font-size: 11px;
    line-height: 1.6;
    background: #0a0d12;
    min-height: 120px;
  }
  .log-line {
    display: flex;
    gap: 10px;
  }
  .log-ts {
    color: #555;
    flex-shrink: 0;
    width: 78px;
  }
  .log-text {
    color: #b0c4d8;
    word-break: break-all;
  }
  .log-text.launcher { color: var(--muted); font-style: italic; }

  .log-hint {
    color: var(--muted);
    font-size: 11px;
    text-align: center;
    padding: 24px;
  }

  /* ── error toast ── */
  .toast {
    position: fixed;
    bottom: 20px;
    left: 50%;
    transform: translateX(-50%);
    background: #3d0b0b;
    color: var(--red);
    border: 1px solid #8b2020;
    padding: 8px 20px;
    border-radius: 6px;
    font-size: 12px;
    font-family: var(--mono);
    z-index: 100;
    display: none;
    max-width: 80vw;
  }

  /* ── empty ── */
  .empty {
    text-align: center;
    padding: 24px;
    color: var(--muted);
    font-size: 12px;
  }

  /* ── scrollbar ── */
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  /* ── section subtitle ── */
  .section-sub {
    font-size: 10px; font-weight: 600; text-transform: uppercase;
    letter-spacing: .6px; color: var(--muted);
    margin-top: 10px;
    margin-bottom: 4px;
  }

  .sudo-warn {
    font-size: 11px;
    color: var(--yellow);
    padding: 6px 8px;
    background: #2d2208;
    border: 1px solid #6e4c00;
    border-radius: 4px;
    margin-bottom: 8px;
  }
</style>
</head>
<body>

<header>
  <span class="logo">⛵ BoAt</span>
  <span class="subtitle">Launcher</span>
  <span class="spacer"></span>
  <span class="gw-badge" id="gw-badge">● :50051</span>
</header>

<nav id="panel-nav">
  <a class="nav-link" data-port="8086">Launcher</a>
  <a class="nav-link" data-port="8080">Dashboard</a>
  <a class="nav-link" data-port="8081">Nodes</a>
  <a class="nav-link" data-port="8082">Commander</a>
  <a class="nav-link" data-port="8083">Recorder</a>
</nav>

<div id="sudo-warn" class="sudo-warn" style="display:none">
  ⚠ Passwordless sudo not available. Interface creation/deletion requires it.
  Run: <code>echo 'ALL ALL=(ALL) NOPASSWD: /sbin/ip' | sudo tee /etc/sudoers.d/ip</code>
</div>

<div class="layout">
  <!-- ── LEFT: CAN ── -->
  <div class="col col-left">
    <div class="pane">
      <div class="pane-header">
        <span class="pane-title">CAN Interfaces</span>
      </div>
      <div class="pane-body" id="can-list">
        <div class="empty">Loading…</div>
      </div>
      <div style="padding:8px 12px; border-top:1px solid var(--border); flex-shrink:0">
        <div class="section-sub">Add Virtual CAN</div>
        <div class="form-row">
          <input id="vcan-name" value="vcan0" placeholder="name"/>
          <button class="btn btn-primary btn-sm" onclick="createVcan()">+ Add</button>
        </div>
      </div>
    </div>
  </div>

  <!-- ── MID: ETHERNET + CONFIG ── -->
  <div class="col col-mid">
    <div class="pane">
      <div class="pane-header">
        <span class="pane-title">Ethernet Interfaces</span>
      </div>
      <div class="pane-body" id="eth-list">
        <div class="empty">Loading…</div>
      </div>
      <div style="padding:8px 12px; border-top:1px solid var(--border); flex-shrink:0">
        <div class="section-sub">Add Virtual Ethernet</div>
        <div class="form-row">
          <input id="virt-eth-name" value="eth0" placeholder="name"/>
          <button class="btn btn-primary btn-sm" onclick="createEthVirtual()">+ Add</button>
        </div>
        <div class="section-sub" style="margin-top:6px">Add VETH Pair</div>
        <div class="form-row">
          <input id="veth-name" value="veth0" placeholder="base name"/>
          <button class="btn btn-primary btn-sm" onclick="createVeth()">+ Add</button>
        </div>
        <div class="section-sub" style="margin-top:6px">Add Raw Physical</div>
        <div class="form-row">
          <input id="raw-eth-name" value="eth0" placeholder="interface name"/>
          <button class="btn btn-primary btn-sm" onclick="createEthRaw()">+ Add</button>
        </div>
      </div>
    </div>
    <div style="border-top:1px solid var(--border); padding:10px 12px; flex-shrink:0">
      <div class="pane-title" style="margin-bottom:8px">Interface Assignment</div>
      <div class="assign-row">
        <span class="assign-label">CAN</span>
        <span class="assign-value" id="assigned-can">—</span>
      </div>
      <div class="assign-row">
        <span class="assign-label">ETH</span>
        <span class="assign-value" id="assigned-eth">—</span>
      </div>
    </div>
    <div style="border-top:1px solid var(--border); padding:10px 12px; flex-shrink:0">
      <div class="pane-title" style="margin-bottom:8px">Import PDU DB</div>
      <div class="form-row">
        <select id="pdu-file-select" style="flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:4px;padding:3px 7px;font-size:11px;font-family:var(--mono);outline:none">
          <option value="" disabled selected>Select a file…</option>
        </select>
        <button class="btn btn-primary btn-sm" id="btn-import-pdu" onclick="importPduDb()">Import</button>
      </div>
      <div id="pdu-import-result" style="margin-top:6px;font-size:11px;font-family:var(--mono);color:var(--muted);display:none"></div>
    </div>
  </div>

  <!-- ── RIGHT: GATEWAY ── -->
  <div class="col col-right">
    <div class="pane">
      <div class="pane-header">
        <span class="pane-title">Gateway</span>
      </div>
      <div class="pane-body" id="gateway-panel">
        <div class="status-grid">
          <div class="stat-item">
            <span class="stat-label">Status</span>
            <span class="stat-value" id="gw-status">
              <span class="status-dot stopped"></span>
              <span id="gw-status-text">stopped</span>
            </span>
          </div>
          <div class="stat-item">
            <span class="stat-label">PID</span>
            <span class="stat-value" id="gw-pid">—</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Uptime</span>
            <span class="stat-value" id="gw-uptime">—</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">Sim State</span>
            <span class="stat-value" id="sim-state">
              <span class="state-badge state-disconnected">disconnected</span>
            </span>
          </div>
        </div>
        <div class="actions">
          <button class="btn btn-start" id="btn-start" onclick="startGateway()">▶ Start</button>
          <button class="btn btn-stop"  id="btn-stop"  onclick="stopGateway()" disabled>■ Stop</button>
        </div>
        <div style="margin-top:10px;border-top:1px solid var(--border);padding-top:8px">
          <div class="assign-row">
            <span class="assign-label">CAN</span>
            <span class="assign-value" id="gw-can-ifaces" style="color:var(--muted)">—</span>
          </div>
          <div class="assign-row">
            <span class="assign-label">ETH</span>
            <span class="assign-value" id="gw-eth-ifaces" style="color:var(--muted)">—</span>
          </div>
        </div>
      </div>
    </div>
    <div class="pane" style="border-top:1px solid var(--border)">
      <div class="pane-header">
        <span class="pane-title">Debug Output</span>
        <span id="log-dot" class="status-dot stopped" style="width:6px;height:6px"></span>
        <span style="flex:1"></span>
        <span id="log-count" style="font-size:10px;color:var(--muted)">0 lines</span>
        <button class="btn btn-sm" onclick="clearLog()" style="padding:2px 6px">Clear</button>
      </div>
      <div class="log-scroll" id="log-scroll">
        <div class="log-hint">Gateway output will appear here after starting.</div>
      </div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
// ── State ─────────────────────────────────────────────────────────────────────
const IFACE_COLORS = [
  { bg: "#1c3a5c", fg: "#58a6ff" },
  { bg: "#1f3a1f", fg: "#3fb950" },
  { bg: "#3a1c3a", fg: "#d2a8ff" },
  { bg: "#3a2a00", fg: "#ffa657" },
  { bg: "#3a1c1c", fg: "#f78166" },
];
const SIM_STATE_CLASS = {
  "RUNNING":    "state-RUNNING",
  "PAUSED":     "state-PAUSED",
  "STOPPED":    "state-STOPPED",
  "IDLE":       "state-IDLE",
  "ERROR":      "state-ERROR",
  "UNSPECIFIED":"state-UNSPECIFIED",
};

let ifaces = [];
let canSelected = [];
let ethSelected = [];
let logData = [];
let logOpen = true;
let logAutoScroll = true;
let health = { can_sudo: false };

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  await checkHealth();
  await Promise.all([fetchInterfaces(), fetchGatewayStatus()]);
  setInterval(fetchInterfaces, 3000);
  setInterval(fetchGatewayStatus, 1000);
  setInterval(fetchSimState, 2000);
  pollLog();
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
  setupNav();
}

// ── Nav ───────────────────────────────────────────────────────────────────────
function setupNav() {
  const h = window.location.hostname;
  const p = window.location.port;
  document.querySelectorAll('.nav-link').forEach(a => {
    a.href = 'http://' + h + ':' + a.dataset.port + '/';
    if (a.dataset.port === p) a.classList.add('active');
  });
}

// ── Health ────────────────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    const r = await fetch('/api/health');
    health = await r.json();
    const warn = document.getElementById('sudo-warn');
    warn.style.display = health.can_sudo ? 'none' : 'block';
  } catch {}
}

// ── Toast ─────────────────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.style.display = 'none'; }, 4000);
}

// ── Interfaces ────────────────────────────────────────────────────────────────
async function fetchInterfaces() {
  let data;
  try {
    const r = await fetch('/api/interfaces');
    data = await r.json();
  } catch { return; }
  ifaces = data.interfaces;
  renderCanList();
  renderEthList();
}

function renderCanList() {
  const el = document.getElementById('can-list');
  const cans = ifaces.filter(i => i.type === 'vcan');
  if (!cans.length) {
    el.innerHTML = '<div class="empty">No CAN interfaces. Create one below.</div>';
    return;
  }
  el.innerHTML = cans.map((iface, i) => {
    const c = IFACE_COLORS[i % IFACE_COLORS.length];
    const up = iface.up ? 'UP' : 'DOWN';
    const upCls = iface.up ? 'state-up' : 'state-down';
    const checked = canSelected.includes(iface.name);
    return `<div class="iface-item">
      <input type="checkbox" class="iface-check" ${checked ? 'checked' : ''}
             onchange="toggleCan('${escHtml(iface.name)}', this.checked)"/>
      <span class="iface-pill" style="background:${c.bg};color:${c.fg}">vcan</span>
      <span class="iface-name">${escHtml(iface.name)}</span>
      <span class="iface-state ${upCls}">${up}</span>
      <button class="btn-remove" onclick="deleteVcan('${escHtml(iface.name)}')" title="Delete">✕</button>
    </div>`;
  }).join('');
  updateAssignDisplay();
}

function toggleCan(name, checked) {
  if (checked) {
    if (!canSelected.includes(name)) canSelected.push(name);
  } else {
    canSelected = canSelected.filter(n => n !== name);
  }
  updateAssignDisplay();
}

function renderEthList() {
  const el = document.getElementById('eth-list');
  const eths = ifaces.filter(i => i.type === 'veth' || i.type === 'ether' || i.type === 'eth-virtual' || i.type === 'eth-raw');
  if (!eths.length) {
    el.innerHTML = '<div class="empty">No Ethernet interfaces.</div>';
    return;
  }
  el.innerHTML = eths.map((iface, i) => {
    const c = IFACE_COLORS[i % IFACE_COLORS.length];
    const up = iface.up ? 'UP' : 'DOWN';
    const upCls = iface.up ? 'state-up' : 'state-down';
    const checked = ethSelected.includes(iface.name);
    const canRemove = iface.type === 'veth' || iface.type === 'eth-virtual' || iface.type === 'eth-raw';
    const pillLabel = iface.type === 'eth-virtual' ? 'virt' : iface.type === 'eth-raw' ? 'raw' : iface.type;
    const delFn = iface.type === 'veth' ? 'deleteVeth' :
                  iface.type === 'eth-virtual' ? 'deleteEthVirtual' :
                  iface.type === 'eth-raw' ? 'deleteEthRaw' : '';
    return `<div class="iface-item">
      <input type="checkbox" class="iface-check" ${checked ? 'checked' : ''}
             onchange="toggleEth('${escHtml(iface.name)}', this.checked)"/>
      <span class="iface-pill" style="background:${c.bg};color:${c.fg}">${pillLabel}</span>
      <span class="iface-name">${escHtml(iface.name)}</span>
      <span class="iface-state ${upCls}">${up}</span>
      ${canRemove ? `<button class="btn-remove" onclick="${delFn}('${escHtml(iface.name)}')" title="Delete">✕</button>` : ''}
    </div>`;
  }).join('');
}

function toggleEth(name, checked) {
  if (checked) {
    if (!ethSelected.includes(name)) ethSelected.push(name);
  } else {
    ethSelected = ethSelected.filter(n => n !== name);
  }
  updateAssignDisplay();
}

function updateAssignDisplay() {
  document.getElementById('assigned-can').textContent = canSelected.length ? canSelected.join(', ') : '—';
  document.getElementById('assigned-eth').textContent = ethSelected.length ? ethSelected.join(', ') : '—';
}

async function createVcan() {
  const name = document.getElementById('vcan-name').value.trim() || 'vcan0';
  try {
    const r = await fetch(`/api/interfaces/vcan?name=${encodeURIComponent(name)}`, { method: 'POST' });
    if (!r.ok) { const e = await r.json(); showToast('Failed: ' + (e.detail || r.status)); return; }
    if (!canSelected.includes(name)) canSelected.push(name);
    await fetchInterfaces();
    updateAssignDisplay();
  } catch (e) { showToast('Error: ' + e); }
}

async function deleteVcan(name) {
  try {
    const r = await fetch(`/api/interfaces/vcan/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!r.ok) { const e = await r.json(); showToast('Failed: ' + (e.detail || r.status)); return; }
    canSelected = canSelected.filter(n => n !== name);
    await fetchInterfaces();
    updateAssignDisplay();
  } catch (e) { showToast('Error: ' + e); }
}

async function createVeth() {
  const name = document.getElementById('veth-name').value.trim() || 'veth0';
  try {
    const r = await fetch(`/api/interfaces/veth?name=${encodeURIComponent(name)}`, { method: 'POST' });
    if (!r.ok) { const e = await r.json(); showToast('Failed: ' + (e.detail || r.status)); return; }
    await fetchInterfaces();
  } catch (e) { showToast('Error: ' + e); }
}

async function deleteVeth(name) {
  try {
    const r = await fetch(`/api/interfaces/veth/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!r.ok) { const e = await r.json(); showToast('Failed: ' + (e.detail || r.status)); return; }
    ethSelected = ethSelected.filter(n => n !== name);
    await fetchInterfaces();
    updateAssignDisplay();
  } catch (e) { showToast('Error: ' + e); }
}

async function createEthVirtual() {
  const name = document.getElementById('virt-eth-name').value.trim() || 'eth0';
  try {
    const r = await fetch(`/api/interfaces/eth/virtual?name=${encodeURIComponent(name)}`, { method: 'POST' });
    if (!r.ok) { const e = await r.json(); showToast('Failed: ' + (e.detail || r.status)); return; }
    if (!ethSelected.includes(name)) ethSelected.push(name);
    await fetchInterfaces();
    updateAssignDisplay();
  } catch (e) { showToast('Error: ' + e); }
}

async function deleteEthVirtual(name) {
  try {
    const r = await fetch(`/api/interfaces/eth/virtual/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!r.ok) { const e = await r.json(); showToast('Failed: ' + (e.detail || r.status)); return; }
    ethSelected = ethSelected.filter(n => n !== name);
    await fetchInterfaces();
    updateAssignDisplay();
  } catch (e) { showToast('Error: ' + e); }
}

async function createEthRaw() {
  const name = document.getElementById('raw-eth-name').value.trim() || 'eth0';
  try {
    const r = await fetch(`/api/interfaces/eth/raw?name=${encodeURIComponent(name)}`, { method: 'POST' });
    if (!r.ok) { const e = await r.json(); showToast('Failed: ' + (e.detail || r.status)); return; }
    if (!ethSelected.includes(name)) ethSelected.push(name);
    await fetchInterfaces();
    updateAssignDisplay();
  } catch (e) { showToast('Error: ' + e); }
}

async function deleteEthRaw(name) {
  try {
    const r = await fetch(`/api/interfaces/eth/raw/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!r.ok) { const e = await r.json(); showToast('Failed: ' + (e.detail || r.status)); return; }
    ethSelected = ethSelected.filter(n => n !== name);
    await fetchInterfaces();
    updateAssignDisplay();
  } catch (e) { showToast('Error: ' + e); }
}

// ── PDU DB import ──────────────────────────────────────────────────────────

async function fetchPduFiles() {
  try {
    const r = await fetch('/api/pdu/list');
    const d = await r.json();
    const sel = document.getElementById('pdu-file-select');
    if (!d.files.length) return;
    sel.innerHTML = '<option value="" disabled selected>Select a file…</option>';
    for (const f of d.files) {
      const opt = document.createElement('option');
      opt.value = f;
      opt.textContent = f;
      sel.appendChild(opt);
    }
  } catch {}
}

async function importPduDb() {
  const sel = document.getElementById('pdu-file-select');
  const filename = sel.value;
  if (!filename) { showToast('Select a PDU DB file first'); return; }

  const btn = document.getElementById('btn-import-pdu');
  btn.disabled = true;
  btn.textContent = 'Importing…';
  const resultEl = document.getElementById('pdu-import-result');

  try {
    const r = await fetch(`/api/pdu/import?filename=${encodeURIComponent(filename)}`, { method: 'POST' });
    const d = await r.json();
    if (!r.ok) { showToast('Failed: ' + (d.detail || r.status)); btn.disabled = false; btn.textContent = 'Import'; return; }

    // Select the created interfaces
    canSelected = d.selected_can || [];
    ethSelected = d.selected_eth || [];
    await fetchInterfaces();
    updateAssignDisplay();

    const canStr = d.created_can.length ? d.created_can.join(', ') : 'none needed';
    const ethStr = d.created_eth.length ? d.created_eth.join(', ') : 'none needed';
    resultEl.innerHTML =
      `<span style="color:var(--green)">✓ Imported ${filename}</span><br>` +
      `CAN: ${canStr}<br>ETH: ${ethStr}`;
    resultEl.style.display = 'block';
    showToast(`Imported ${filename}`);
  } catch (e) {
    showToast('Error: ' + e);
  }
  btn.disabled = false;
  btn.textContent = 'Import';
}

// ── Gateway ───────────────────────────────────────────────────────────────────
async function fetchGatewayStatus() {
  let data;
  try {
    const r = await fetch('/api/gateway/status');
    data = await r.json();
  } catch { return; }

  const dot = document.getElementById('gw-status').querySelector('.status-dot');
  const text = document.getElementById('gw-status-text');
  const pidEl = document.getElementById('gw-pid');
  const upEl = document.getElementById('gw-uptime');
  const startBtn = document.getElementById('btn-start');
  const stopBtn = document.getElementById('btn-stop');
  const logDot = document.getElementById('log-dot');

  const isRun = data.running;

  dot.className = 'status-dot ' + (isRun ? 'running' : (data.status.startsWith('exited:') ? 'exited' : 'stopped'));
  text.textContent = data.status;
  pidEl.textContent = data.pid ?? '—';

  if (isRun && data.uptime_sec != null) {
    const m = Math.floor(data.uptime_sec / 60);
    const s = Math.floor(data.uptime_sec % 60);
    upEl.textContent = `${m}m ${s}s`;
  } else {
    upEl.textContent = '—';
  }

  document.getElementById('gw-can-ifaces').textContent = data.can_interfaces || '—';
  document.getElementById('gw-eth-ifaces').textContent = data.eth_interfaces || '—';

  startBtn.disabled = isRun;
  stopBtn.disabled = !isRun;
  logDot.className = 'status-dot ' + (isRun ? 'running' : 'stopped');
  logDot.style.width = '6px';
  logDot.style.height = '6px';
}

async function fetchSimState() {
  let data;
  try {
    const r = await fetch('/api/simulation/state');
    data = await r.json();
  } catch { return; }

  const el = document.getElementById('sim-state');
  if (!data.connected) {
    el.innerHTML = '<span class="state-badge state-disconnected">disconnected</span>';
    return;
  }
  const cls = SIM_STATE_CLASS[data.state] || 'state-disconnected';
  const label = data.state || 'UNKNOWN';
  el.innerHTML =
    `<span class="state-badge ${cls}">${label}</span>` +
    (data.tick != null ? ` <span style="font-size:11px;color:var(--muted)">tick ${data.tick}</span>` : '');
}

async function startGateway() {
  const can = canSelected.join(',');
  const eth = ethSelected.map(name => {
    const iface = ifaces.find(i => i.name === name);
    return iface && iface.type === 'eth-raw' ? 'raw:' + name : name;
  }).join(',');
  try {
    const r = await fetch('/api/gateway/start?' + new URLSearchParams({ can, eth }), { method: 'POST' });
    if (!r.ok) { const e = await r.json(); showToast('Failed: ' + (e.detail || r.status)); return; }
    await fetchGatewayStatus();
  } catch (e) { showToast('Error: ' + e); }
}

async function stopGateway() {
  try {
    await fetch('/api/gateway/stop', { method: 'POST' });
    await fetchGatewayStatus();
  } catch (e) { showToast('Error: ' + e); }
}

// ── Log ───────────────────────────────────────────────────────────────────────
async function pollLog() {
  while (true) {
    if (!logOpen) { await sleep(500); continue; }
    let data;
    try {
      const r = await fetch('/api/gateway/log');
      data = await r.json();
    } catch { await sleep(1000); continue; }

    const prevLen = logData.length;
    if (data.log.length === prevLen) { await sleep(500); continue; }

    logData = data.log;
    renderLog(prevLen);
    await sleep(500);
  }
}

function renderLog(prevLen) {
  const el = document.getElementById('log-scroll');
  const count = document.getElementById('log-count');
  count.textContent = logData.length + ' lines';

  if (logData.length === 0) {
    el.innerHTML = '<div class="log-hint">Gateway output will appear here after starting.</div>';
    return;
  }

  const atBottom = el.scrollHeight - el.clientHeight - el.scrollTop < 40;

  // Only append new lines
  const newLines = logData.slice(prevLen || 0);
  for (const line of newLines) {
    const row = document.createElement('div');
    row.className = 'log-line';
    const isLauncher = line.text.startsWith('[launcher]');
    row.innerHTML =
      `<span class="log-ts">${line.ts}</span>` +
      `<span class="log-text${isLauncher ? ' launcher' : ''}">${escHtml(line.text)}</span>`;
    el.appendChild(row);
  }

  if (atBottom || (prevLen != null && logData.length - prevLen > 0)) {
    el.scrollTop = el.scrollHeight;
  }
}

function clearLog() {
  logData = [];
  const el = document.getElementById('log-scroll');
  el.innerHTML = '<div class="log-hint">Gateway output will appear here after starting.</div>';
  document.getElementById('log-count').textContent = '0 lines';
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/\\/g,'&#92;');
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Start ─────────────────────────────────────────────────────────────────────
init();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(HTML)


if __name__ == "__main__":
    print(f"BoAt Launcher → http://localhost:{_PORT}")
    print(f"Gateway binary: {_GW_BIN}")
    print(f"gRPC address : {_GW_ADDR}")
    can_sudo = True
    try:
        subprocess.run(["sudo", "-n", "true"], capture_output=True, check=True)
    except Exception:
        can_sudo = False
        print("WARNING: passwordless sudo not available — interface creation will fail.")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")
