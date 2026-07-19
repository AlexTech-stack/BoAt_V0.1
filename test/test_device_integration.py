#!/usr/bin/env python3
"""Integration tests for electrical-device integration (Phases 1–3).

Launches the real gateway binary with the device plugins and exercises the
DeviceService, the device_manager discovery, the record→replay loop, and the
SCPI physical-device path (against an in-process mock instrument — no hardware).

Usage:
    python3 test/test_device_integration.py

Requires a built debug tree (cmake --build --preset debug) and the boat SDK on
the path (pip install -e boat-platform/sdk/python). If the gateway binary or
plugins are missing, the affected tests report SKIP rather than fail.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BP = REPO / "boat-platform"
BUILD = BP / "build" / "debug"
GATEWAY = BUILD / "src" / "gateway" / "grpc_gateway" / "boat_gateway"
PLUGINS = BUILD / "src" / "plugins"

DM = PLUGINS / "device_manager" / "device_manager.so"
PSU = PLUGINS / "virtual_psu" / "virtual_psu.so"
RELAY = PLUGINS / "virtual_relay" / "virtual_relay.so"
GEN = PLUGINS / "virtual_generator" / "virtual_generator.so"
SCPI = PLUGINS / "scpi_device" / "scpi_device.so"
MODBUS = PLUGINS / "modbus_device" / "modbus_device.so"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_gateway(address: str, timeout: float = 8.0) -> bool:
    # Use the always-available BusService (independent of which plugins loaded)
    # so readiness detection works even without device_manager.
    from boat.bus_node import BusNode
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if BusNode(address).publish("__probe__", 0.0):
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


class Gateway:
    """Launch the gateway binary with a plugin set; tear it down on exit."""

    def __init__(self, plugins: str, extra_env: dict | None = None,
                 cwd: Path | None = None):
        self.address = "127.0.0.1:50051"  # gateway binds 0.0.0.0:50051
        env = dict(os.environ)
        env["BOAT_CAN_INTERFACES"] = ""
        env["BOAT_NODE_PLUGINS"] = plugins
        if extra_env:
            env.update(extra_env)
        self._env = env
        self._cwd = str(cwd) if cwd else None
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [str(GATEWAY)], env=self._env, cwd=self._cwd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not _wait_gateway(self.address):
            self.__exit__(None, None, None)
            raise RuntimeError("gateway did not come up")
        return self

    def __exit__(self, *exc):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        time.sleep(0.5)  # let the listening port fully release before the next run


class MockScpiServer:
    """Minimal SCPI power-supply simulator over TCP (3 ohm load)."""

    def __init__(self):
        self.port = _free_port()
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", self.port))
        self._srv.listen(1)
        self._run = True
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        while self._run:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        volt, buf = 0.0, b""
        with conn:
            while self._run:
                try:
                    data = conn.recv(256)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    cmd = line.decode().strip()
                    reply = None
                    if cmd == "*IDN?":
                        reply = "BoAt,MockPSU,0,1"
                    elif cmd == "MEAS:VOLT?":
                        reply = f"{volt:g}"
                    elif cmd == "MEAS:CURR?":
                        reply = f"{volt / 3.0:g}"
                    elif cmd.startswith("VOLT "):
                        volt = float(cmd[5:])
                    if reply is not None:
                        conn.sendall((reply + "\n").encode())

    def close(self):
        self._run = False
        try:
            self._srv.close()
        except OSError:
            pass


# ── tests ────────────────────────────────────────────────────────────────

def _chan(dev, name):
    for c in dev.channels:
        if c.name == name:
            return c
    return None


def test_virtual_devices() -> bool:
    """TC_Devices_001/002/004/005 — virtual PSU + relay via DeviceService."""
    from boat.device_node import DeviceNode
    plugins = f"{DM},{PSU}?{{\"id\":\"main\",\"r_load\":3.0,\"ramp_v_per_tick\":0.5}},{RELAY}?{{\"id\":\"kl15\"}}"
    with Gateway(plugins):
        d = DeviceNode()
        time.sleep(1.0)
        ids = {x.device_id for x in d.list_devices()}
        assert {"psu.main", "relay.kl15"} <= ids, f"discovery: {ids}"
        assert d.set_control("psu.main", "voltage", 24.0)
        assert d.set_control("relay.kl15", "state", 1)
        assert not d.set_control("psu.main", "nonsense", 1.0), "bad channel accepted"
        time.sleep(0.8)
        psu = d.read_state("psu.main")
        relay = d.read_state("relay.kl15")
        v, i = _chan(psu, "voltage"), _chan(psu, "current")
        st = _chan(relay, "state")
        assert v and abs(v.value - 24.0) < 0.01, f"voltage {v and v.value}"
        assert i and abs(i.value - 8.0) < 0.01, f"current {i and i.value}"
        assert st and st.value == 1.0, f"relay {st and st.value}"
        d.close()
    return True


def test_record_replay() -> bool:
    """TC_Devices_007 — record a device curve, replay it onto the bus."""
    from boat.bus_node import BusNode
    from boat.client import BoAtClient
    from boat.v1 import replay_pb2
    db = BP / "boat_events.db"
    if db.exists():
        db.unlink()

    # Phase A: record a ramp (cwd=BP so boat_events.db lands where Phase B reads it).
    rec_env = {"BOAT_RECORD_BUS_SIGNALS": "devrec", "BOAT_RECORD_BUS_PREFIXES": "psu."}
    with Gateway(f"{PSU}?{{\"id\":\"main\",\"v_set\":0,\"r_load\":3.0,\"ramp_v_per_tick\":0.2}}",
                 rec_env, cwd=BP):
        time.sleep(1.0)
        BusNode().publish("psu.main.voltage.set", 20.0)
        time.sleep(1.5)

    # Phase B: replay the recorded curve; a subscriber observes it.
    got: list[float] = []

    class Sub(BusNode):
        def on_signal(self, s):
            got.append(round(s.number_value, 4))

    with Gateway("", None, cwd=BP):
        sub = Sub(node_id="rr-sub")
        sub.run_background(names=["psu.main.voltage.meas"])
        time.sleep(0.4)
        c = BoAtClient()
        c.replay.StartReplayFromEvents(replay_pb2.StartReplayFromEventsRequest(
            simulation_id="devrec", signal_id="psu.main.voltage.meas",
            speed=replay_pb2.REPLAY_SPEED_ACCELERATED, speed_multiplier=20.0))
        time.sleep(2.0)
        sub.stop()
    assert len(got) >= 10, f"replayed {len(got)} values"
    assert abs(got[-1] - 20.0) < 0.6 and got[0] < got[-1], f"curve {got[:3]}..{got[-3:]}"
    return True


def test_virtual_generator() -> bool:
    """TC_Devices_013 — virtual alternator: rpm setpoint → regulated voltage."""
    from boat.device_node import DeviceNode
    plugins = f"{DM},{GEN}?{{\"id\":\"alt\",\"r_load\":2.0,\"rpm_per_tick\":100}}"
    with Gateway(plugins):
        d = DeviceNode()
        time.sleep(1.0)
        assert "gen.alt" in {x.device_id for x in d.list_devices()}
        assert d.set_control("gen.alt", "rpm", 6000)   # at/above rated → regulated
        time.sleep(1.2)
        gen = d.read_state("gen.alt")
        rpm = _chan(gen, "rpm")
        v = _chan(gen, "output_voltage")
        i = _chan(gen, "output_current")
        assert rpm and abs(rpm.value - 6000) < 1, f"rpm {rpm and rpm.value}"
        assert v and abs(v.value - 14.2) < 0.05, f"voltage {v and v.value}"
        assert i and abs(i.value - 7.1) < 0.05, f"current {i and i.value}"  # 14.2 / 2 Ω
        d.close()
    return True


def test_stream_state() -> bool:
    """TC_Devices_010 — DeviceService.StreamState pushes measurement updates."""
    from boat.device_node import DeviceNode
    plugins = f"{DM},{PSU}?{{\"id\":\"main\",\"v_set\":0,\"r_load\":3.0,\"ramp_v_per_tick\":0.3}}"
    with Gateway(plugins):
        stream = DeviceNode()
        updates = []
        t = threading.Thread(
            target=lambda: stream.stream_state(updates.append, ["psu.main"]),
            daemon=True)
        t.start()
        time.sleep(0.6)
        drv = DeviceNode()
        drv.set_control("psu.main", "voltage", 24.0)
        time.sleep(1.2)
        stream.close()  # cancels the stream
        drv.close()
        assert updates, "no StreamState updates received"
        assert any(u.device_id == "psu.main" and u.channel in ("voltage", "current")
                   and u.value > 0 for u in updates), "no meaningful update"
    return True


def test_kl15_restbus_gate() -> bool:
    """TC_Devices_006 — ignition relay gates the restbus (gate logic; no CAN needed)."""
    import importlib.util
    from boat.bus_node import BusNode

    # Load _Kl15Gate from the demo without running its main().
    demo = BP / "demo" / "restbus_simulator.py"
    spec = importlib.util.spec_from_file_location("restbus_simulator", demo)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class FakeSim:  # stands in for the CAN/PDU side (which needs hardware)
        def __init__(self):
            self.started = 0
            self.stopped = 0

        def start(self):
            self.started += 1
            return 3

        def stop(self):
            self.stopped += 1

    with Gateway(f"{RELAY}?{{\"id\":\"kl15\"}}"):
        fake = FakeSim()
        gate = mod._Kl15Gate(fake, "kl15", "127.0.0.1:50051")
        threading.Thread(target=lambda: gate.run(names=gate.names), daemon=True).start()
        time.sleep(0.5)
        pub = BusNode()
        pub.publish("relay.kl15.set", 1)   # ignition ON  → restbus starts
        time.sleep(0.6)
        pub.publish("relay.kl15.set", 0)   # ignition OFF → restbus stops
        time.sleep(0.6)
        gate.stop()
        assert fake.started >= 1, f"restbus not started on ignition-on ({fake.started})"
        assert fake.stopped >= 1, f"restbus not stopped on ignition-off ({fake.stopped})"
    return True


def test_scpi_device() -> bool:
    """TC_Devices_008 — physical PSU over SCPI (mock instrument, no hardware)."""
    from boat.device_node import DeviceNode
    mock = MockScpiServer()
    try:
        plugins = f"{DM},{SCPI}?{{\"id\":\"main\",\"host\":\"127.0.0.1\",\"port\":{mock.port},\"poll_ms\":100}}"
        with Gateway(plugins):
            d = DeviceNode()
            time.sleep(2.0)
            assert "psu.main" in {x.device_id for x in d.list_devices()}
            assert d.set_control("psu.main", "voltage", 24.0)
            time.sleep(0.8)
            psu = d.read_state("psu.main")
            v, i = _chan(psu, "voltage"), _chan(psu, "current")
            assert v and abs(v.value - 24.0) < 0.01, f"scpi voltage {v and v.value}"
            assert i and abs(i.value - 8.0) < 0.01, f"scpi current {i and i.value}"
            d.close()
    finally:
        mock.close()
    return True


def main() -> int:
    if not GATEWAY.exists():
        print(f"SKIP: gateway not built ({GATEWAY}). Run cmake --build --preset debug.")
        return 0
    for p in (DM, PSU, RELAY, GEN, SCPI):
        if not p.exists():
            print(f"SKIP: plugin missing ({p}).")
            return 0

    tests = [
        ("virtual devices (PSU + relay via DeviceService)", test_virtual_devices),
        ("virtual generator (rpm → regulated voltage)", test_virtual_generator),
        ("DeviceService StreamState updates", test_stream_state),
        ("KL15 relay gates the restbus", test_kl15_restbus_gate),
        ("record → replay device curve", test_record_replay),
        ("physical PSU over SCPI (mock instrument)", test_scpi_device),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
