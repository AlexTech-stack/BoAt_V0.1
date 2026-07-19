from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

import grpc

from boat.client import BoAtClient
from boat.test.bus import TestCanBus, TestEthBus
from boat.test.config import EnvironmentConfig, BusConfig
from boat.test.dut import DutProxy
from boat.test.exceptions import TestGatewayError
from boat.test.pdu import PduHelper
from boat.test.report import (
    TestReport,
    TestStepRecord,
    AssertionRecord,
    StimulusRecord,
    TraceRef,
)

_REPORT_SCHEMA_VERSION = "1.0"


class _GatewayManager:
    """Manages gateway subprocess lifecycle."""

    def __init__(self, config: EnvironmentConfig) -> None:
        self._config = config.gateway
        self._env_cfg = config
        self._process: Optional[subprocess.Popen] = None

    def start(self) -> str:
        binary = self._config.binary
        if binary and os.path.isfile(binary):
            env = os.environ.copy()
            env["BOAT_CAN_INTERFACES"] = self._build_can_ifaces()
            env["BOAT_ETH_INTERFACES"] = self._build_eth_ifaces()
            node_plugins = self._env_cfg.node_plugin_specs()
            if node_plugins:
                env["BOAT_NODE_PLUGINS"] = ",".join(node_plugins)

            self._process = subprocess.Popen(
                [binary],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self._wait_for_ready(timeout=15)
        return self._config.address

    def _build_can_ifaces(self) -> str:
        ifaces = [
            b.interface for b in self._env_cfg.buses.values()
            if b.type in ("virtual", "physical")
        ]
        return ",".join(ifaces) if ifaces else "vcan0"

    def _build_eth_ifaces(self) -> str:
        ifaces = [
            b.interface for b in self._env_cfg.buses.values()
            if b.type in ("virtual_eth", "raw_eth")
        ]
        return ",".join(ifaces) if ifaces else ""

    def _wait_for_ready(self, timeout: int = 15) -> None:
        deadline = time.monotonic() + timeout
        last_err = ""
        while time.monotonic() < deadline:
            try:
                channel = grpc.insecure_channel(self._config.address)
                grpc.channel_ready_future(channel).result(timeout=3)
                channel.close()
                return
            except Exception as exc:
                last_err = str(exc)
                time.sleep(0.5)
        raise TestGatewayError(
            f"Gateway at {self._config.address} not ready within {timeout}s: {last_err}"
        )

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None


class _SimControl:
    """Wraps gRPC SimulationService for test control."""

    def __init__(self, client: BoAtClient) -> None:
        self._client = client
        self._sim_id: Optional[str] = None

    def create(self, scenario_id: str) -> str:
        from boat.v1 import simulation_pb2

        resp = self._client.simulation.CreateSimulation(
            simulation_pb2.CreateSimulationRequest(scenario_id=scenario_id)
        )
        self._sim_id = resp.simulation.simulation_id
        return self._sim_id

    def start(self) -> None:
        from boat.v1 import simulation_pb2

        if not self._sim_id:
            raise RuntimeError("No simulation created")
        self._client.simulation.StartSimulation(
            simulation_pb2.StartSimulationRequest(simulation_id=self._sim_id)
        )

    def pause(self) -> None:
        from boat.v1 import simulation_pb2

        if not self._sim_id:
            raise RuntimeError("No simulation created")
        self._client.simulation.PauseSimulation(
            simulation_pb2.PauseSimulationRequest(simulation_id=self._sim_id)
        )

    def step(self, ticks: int = 1) -> None:
        from boat.v1 import simulation_pb2

        if not self._sim_id:
            raise RuntimeError("No simulation created")
        self._client.simulation.StepSimulation(
            simulation_pb2.StepSimulationRequest(simulation_id=self._sim_id, ticks=ticks)
        )

    def stop(self) -> None:
        from boat.v1 import simulation_pb2

        if self._sim_id:
            self._client.simulation.StopSimulation(
                simulation_pb2.StopSimulationRequest(simulation_id=self._sim_id)
            )
            self._sim_id = None

    @property
    def simulation_id(self) -> Optional[str]:
        return self._sim_id


class _TraceManager:
    """Manages trace recording lifecycle via MarkStep RPC and recorder daemon."""

    def __init__(self, client: BoAtClient,
                 recorder_url: Optional[str] = None,
                 trace_format: str = "blf",
                 report: Optional["TestReport"] = None) -> None:
        self._client = client
        self._trace_id: Optional[str] = None
        self._session_id: Optional[str] = None
        self._recorder_url = recorder_url
        self._trace_format = trace_format
        self._report = report
        self._rec = None

    def start(self, trace_id: str, buses: Optional[list[str]] = None) -> str:
        self._trace_id = trace_id
        if self._recorder_url:
            try:
                from boat.trace_recorder import TraceRecorder
                self._rec = TraceRecorder(
                    recorder_url=self._recorder_url,
                    gateway=self._client.address if hasattr(self._client, 'address') else "localhost:50051",
                )
                result = self._rec.start(
                    buses=buses,
                    fmt=self._trace_format,
                    name=trace_id,
                )
                self._session_id = result.get("session_id")
            except Exception:
                self._rec = None
        return trace_id

    def marker(self, step_id: int, step_name: str, sim_tick: int = 0,
               metadata: Optional[dict[str, str]] = None) -> None:
        if not self._trace_id:
            return
        from boat.v1 import trace_pb2

        try:
            req = trace_pb2.MarkStepRequest(
                trace_id=self._trace_id,
                step_id=step_id,
                step_name=step_name,
                sim_tick=sim_tick,
                metadata=metadata or {},
            )
            self._client.trace.MarkStep(req)
        except Exception:
            pass

    def stop(self, copy_to_dir: Optional[str] = None) -> list["TraceRef"]:
        tid = self._trace_id
        self._trace_id = None
        traces: list[TraceRef] = []

        if self._rec and self._session_id:
            try:
                result = self._rec.stop(self._session_id)
                session_id = result.get("session_id", "")
                for file_info in result.get("files", []):
                    fname = file_info.get("name", "")
                    fsize = file_info.get("size", 0)
                    ext = fname.split(".")[-1] if "." in fname else "binary"
                    fmt_map = {"asc": "asc", "blf": "blf", "pcap": "pcap", "jsonl": "binary"}
                    fmt = fmt_map.get(ext, "binary")

                    tr = TraceRef(
                        id=f"{tid}/{fname}",
                        path=fname,
                        format=fmt,
                        size_bytes=fsize,
                    )
                    traces.append(tr)

                    if copy_to_dir and result.get("output_dir"):
                        src = os.path.join(result["output_dir"], fname)
                        dst = os.path.join(copy_to_dir, fname)
                        try:
                            import shutil
                            os.makedirs(os.path.dirname(dst), exist_ok=True)
                            if os.path.isfile(src):
                                shutil.copy2(src, dst)
                                tr.path = dst
                        except Exception:
                            pass
            except Exception:
                pass

        self._session_id = None
        self._rec = None
        return traces


class TestHarness:
    __test__ = False
    """Main test orchestrator for BoAt HIL tests.

    Manages gateway lifecycle, provides bus/DUT/simulation access, coordinates
    test steps and reporting.

    Usage::

        harness = TestHarness("config/tests/env_virtual.json")
        harness.start()
        try:
            can1 = harness.can_bus("can1")
            with harness.step(1, "Send RPM") as step:
                can1.send(0x100, b'\\x01\\xF4')
                harness.advance(100)
                frame = can1.expect(can_id=0x300, timeout_ms=200)
                step.assert_true(frame is not None)
        finally:
            report = harness.stop()
            report.save("report.json")
    """

    def __init__(self, config: str | EnvironmentConfig,
                 recorder_url: Optional[str] = None,
                 trace_format: str = "blf") -> None:
        if isinstance(config, EnvironmentConfig):
            self.config = config
        else:
            self.config = EnvironmentConfig.from_file(config)
        self._recorder_url = recorder_url
        self._trace_format = trace_format
        self._client: Optional[BoAtClient] = None
        self._gateway: Optional[_GatewayManager] = None
        self._sim: Optional[_SimControl] = None
        self._trace: Optional[_TraceManager] = None
        self._dut: Optional[DutProxy] = None
        self._buses: dict[str, Any] = {}
        self._pdu: Optional[PduHelper] = None
        self._report: Optional[TestReport] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        self._gateway = _GatewayManager(self.config)
        address = self._gateway.start()
        self._client = BoAtClient(address)
        self._sim = _SimControl(self._client)
        self._report = TestReport()
        self._report.meta.generator = "boat-test"
        self._report.environment = self.config.snapshot()

        self._trace = _TraceManager(
            self._client,
            recorder_url=self._recorder_url,
            trace_format=self._trace_format,
            report=self._report,
        )
        trace_id = f"test_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        can_ifaces = [b.interface for b in self.config.buses.values()
                      if b.type in ("virtual", "physical")]
        self._trace.start(trace_id, buses=can_ifaces)

        if self.config.dut:
            self._dut = DutProxy(self._client, self.config)

    def stop(self, report_dir: Optional[str] = None) -> TestReport:
        if self._trace:
            trace_refs = self._trace.stop(copy_to_dir=report_dir)
            for tr in trace_refs:
                self._report.traces.append(tr)
        if self._client:
            self._client.close()
            self._client = None
        if self._gateway:
            self._gateway.stop()
            self._gateway = None
        report = self._report
        self._report = None
        return report

    # ── Bus access ─────────────────────────────────────────────────────────

    def can_bus(self, name: str) -> TestCanBus:
        if name not in self._buses:
            cfg = self.config.buses.get(name)
            if not cfg:
                raise KeyError(f"Bus '{name}' not found in environment config")
            bus = TestCanBus(self._client, cfg)
            if self._pdu is not None:
                bus.pdu = self._pdu
            self._buses[name] = bus
        return self._buses[name]

    def eth_bus(self, name: str) -> TestEthBus:
        if name not in self._buses:
            cfg = self.config.buses.get(name)
            if not cfg:
                raise KeyError(f"Bus '{name}' not found in environment config")
            self._buses[name] = TestEthBus(self._client, cfg)
        return self._buses[name]

    def close_bus(self, name: str) -> None:
        bus = self._buses.pop(name, None)
        if bus and hasattr(bus, "close"):
            bus.close()

    @property
    def dut(self) -> DutProxy:
        if self._dut is None:
            self._dut = DutProxy(self._client, self.config.dut if self.config else None)
        return self._dut

    @property
    def sim(self) -> _SimControl:
        if self._sim is None:
            self._sim = _SimControl(self._client)
        return self._sim

    @property
    def client(self) -> BoAtClient:
        if self._client is None:
            raise RuntimeError("Harness not started. Call start() first.")
        return self._client

    @property
    def report(self) -> TestReport:
        if self._report is None:
            self._report = TestReport()
        return self._report

    # ── PDU Database ───────────────────────────────────────────────────────

    @property
    def pdu(self) -> Optional[PduHelper]:
        return self._pdu

    def load_pdu_database(self, path: str) -> PduHelper:
        """Load a PDU database and attach it to all existing bus instances.

        After calling this, bus instances have ``.pdu`` set and can use
        ``send_signal()`` / ``expect_signal()``.

        Args:
            path: Path to the PDU database JSON file.

        Returns:
            The ``PduHelper`` instance.
        """
        self._pdu = PduHelper(path)
        for bus in self._buses.values():
            if hasattr(bus, "pdu"):
                bus.pdu = self._pdu
        return self._pdu

    # ── Time ───────────────────────────────────────────────────────────────

    def advance(self, ms: int) -> None:
        if self._sim is not None:
            tick_ms = self.config.gateway.tick_ms
            ticks = max(1, ms // tick_ms) if tick_ms else ms
            self._sim.step(ticks=ticks)

    # ── Steps ──────────────────────────────────────────────────────────────

    @contextmanager
    def step(self, step_id: int, name: str) -> Iterator["StepContext"]:
        step = TestStepRecord(id=step_id, name=name)
        started = datetime.now(timezone.utc).isoformat()
        step.started_at = started

        if self._trace:
            self._trace.marker(step_id, name)

        ctx = StepContext(step)
        yield ctx

        if step.verdict == "SKIPPED":
            has_fail = any(a.result == "FAIL" for a in step.assertions)
            has_err = any(a.result == "ERROR" for a in step.assertions)
            step.verdict = "ERROR" if has_err else ("FAIL" if has_fail else "PASS")

        finished = datetime.now(timezone.utc)
        start_dt = datetime.fromisoformat(started)
        step.duration_ms = int((finished - start_dt).total_seconds() * 1000)

        self.report.add_step(step)


class StepContext:
    """Context object yielded by ``TestHarness.step()``.

    Collects assertions, stimuli, and observations for the active step.
    """

    def __init__(self, step: TestStepRecord) -> None:
        self._step = step

    @property
    def record(self) -> TestStepRecord:
        return self._step

    def assert_true(self, condition: bool, expr: str = "") -> None:
        record = AssertionRecord.pass_(expr or "assert_true") if condition else AssertionRecord.fail(
            expr or "assert_true", "true", "false"
        )
        self._step.add_assertion(record)

    def assert_equal(self, actual: Any, expected: Any, expr: str = "") -> None:
        record = AssertionRecord.pass_(expr or f"{actual} == {expected}") if actual == expected else \
            AssertionRecord.fail(expr or "assert_equal", str(expected), str(actual))
        self._step.add_assertion(record)

    def assert_frame_matches(self, frame, can_id: int = None, data: bytes = None) -> None:
        from boat.test.bus import TestCanBus
        matches = TestCanBus._matches(frame, can_id, data, None)
        parts = []
        if can_id is not None:
            parts.append(f"can_id=0x{can_id:X}")
        if data is not None:
            parts.append(f"data={data.hex()}")
        expr = " and ".join(parts) or "frame is not None"
        record = AssertionRecord.pass_(expr) if matches else AssertionRecord.fail(
            expr,
            f"can_id=0x{frame.can_id:X}, data={bytes(frame.data).hex()}",
            "no match"
        )
        self._step.add_assertion(record)

    def record_stimulus(self, **kwargs: Any) -> None:
        self._step.add_stimulus(**kwargs)

    def record_observation(self, **kwargs: Any) -> None:
        self._step.add_observation(**kwargs)
