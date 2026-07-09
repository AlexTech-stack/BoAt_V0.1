from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from boat.test.config import BusConfig, DutConfig, EnvironmentConfig, GatewayConfig
from boat.test.dut import DutProxy, PluginDutBackend, PhysicalDutBackend, MockDutBackend
from boat.test.harness import TestHarness, StepContext
from boat.test.report import TestStepRecord, AssertionRecord


def _make_env_config() -> EnvironmentConfig:
    return EnvironmentConfig(
        schema_version="1.0",
        name="test-env",
        description="Test environment",
        gateway=GatewayConfig(binary=None, tick_ms=10, address="localhost:50051"),
        buses={
            "can1": BusConfig(logical_name="can1", type="virtual", interface="vcan0"),
            "can2": BusConfig(logical_name="can2", type="virtual", interface="vcan1"),
        },
        dut=DutConfig(name="test-dut", type="plugin", so_path="test.so", config_json="{}"),
    )


class TestDutProxy:
    def test_plugin_backend_created(self) -> None:
        mock_client = MagicMock()
        resp = MagicMock()
        resp.plugin.plugin_id = "p-1"
        mock_client.plugin.RegisterPlugin.return_value = resp

        proxy = DutProxy(mock_client, _make_env_config().dut)
        proxy.configure({})
        mock_client.plugin.RegisterPlugin.assert_called_once()

    def test_physical_backend_no_rpc(self) -> None:
        mock_client = MagicMock()
        dut_cfg = DutConfig(name="ecu-42", type="physical")
        proxy = DutProxy(mock_client, dut_cfg)
        proxy.configure({})
        proxy.reset()
        assert proxy.version is None

    def test_mock_backend(self) -> None:
        proxy = DutProxy(MagicMock(), None)
        assert proxy.version is None
        proxy.configure({})
        proxy.reset()


class TestStepContext:
    def test_assert_true_pass(self) -> None:
        step = TestStepRecord(id=1, name="test")
        ctx = StepContext(step)
        ctx.assert_true(True)
        assert step.assertions[0].result == "PASS"

    def test_assert_true_fail(self) -> None:
        step = TestStepRecord(id=1, name="test")
        ctx = StepContext(step)
        ctx.assert_true(False)
        assert step.assertions[0].result == "FAIL"

    def test_assert_equal(self) -> None:
        step = TestStepRecord(id=1, name="test")
        ctx = StepContext(step)
        ctx.assert_equal(42, 42)
        assert step.assertions[0].result == "PASS"
        ctx.assert_equal(42, 0)
        assert step.assertions[1].result == "FAIL"

    def test_record_stimulus(self) -> None:
        step = TestStepRecord(id=1, name="test")
        ctx = StepContext(step)
        ctx.record_stimulus(type="can", bus="can1", can_id=0x100, data="01F4")
        assert len(step.stimuli) == 1
        assert step.stimuli[0].can_id == 0x100


class TestHarnessConfig:
    def test_from_path(self, tmp_path) -> None:
        cfg_path = tmp_path / "env.json"
        cfg_path.write_text('{"schema_version":"1.0","name":"t","gateway":{"address":"x:1"},"buses":{"can1":{"type":"virtual","interface":"vcan0"}}}')
        h = TestHarness(str(cfg_path))
        assert h.config.name == "t"
        assert "can1" in h.config.buses

    def test_from_object(self) -> None:
        cfg = _make_env_config()
        h = TestHarness(cfg)
        assert h.config.name == "test-env"

    def test_bus_access(self) -> None:
        cfg = _make_env_config()
        h = TestHarness(cfg)
        h._client = MagicMock()
        can1 = h.can_bus("can1")
        assert can1.name == "can1"

        same = h.can_bus("can1")
        assert same is can1  # cached

    def test_bus_access_unknown(self) -> None:
        h = TestHarness(_make_env_config())
        with pytest.raises(KeyError):
            h.can_bus("nonexistent")

    def test_dut_access(self) -> None:
        h = TestHarness(_make_env_config())
        h._client = MagicMock()
        assert h.dut is not None

    def test_step_yields_context(self) -> None:
        from boat.test.report import TestReport
        h = TestHarness(_make_env_config())
        h._report = TestReport()
        with h.step(1, "My Step") as ctx:
            ctx.assert_true(True)
        assert len(h.report.steps) == 1
        assert h.report.steps[0].name == "My Step"

    def test_trace_manager_created_on_start(self) -> None:
        h = TestHarness(_make_env_config())
        h._client = MagicMock()
        h._trace = None
        # Simulate start — trace manager should be created and started
        from boat.test.harness import _TraceManager
        h._trace = _TraceManager(h._client)
        tid = h._trace.start("test-trace")
        assert tid is not None
        assert h._trace._trace_id == tid

    def test_step_calls_trace_marker(self) -> None:
        from boat.test.report import TestReport
        h = TestHarness(_make_env_config())
        h._trace = MagicMock()
        h._report = TestReport()
        with h.step(1, "Traced Step") as ctx:
            ctx.assert_true(True)
        h._trace.marker.assert_called_once_with(1, "Traced Step")


# We skip gateway start/stop tests since they require a real binary or subprocess mocking.
