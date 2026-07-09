from unittest.mock import patch, MagicMock

from boat.test.check import check_environment
from boat.test.config import EnvironmentConfig, GatewayConfig, BusConfig


def _make_config(bus_type="virtual", iface="vcan0", dut_type=None, gateway_binary=None) -> EnvironmentConfig:
    return EnvironmentConfig(
        schema_version="1.0",
        name="test-env",
        description="test",
        gateway=GatewayConfig(binary=gateway_binary, tick_ms=10, address=""),
        buses={"can1": BusConfig(logical_name="can1", type=bus_type, interface=iface)},
        dut=None,
    )


class TestCheckEnvironment:
    def test_clean_virtual(self) -> None:
        cfg = _make_config()
        with patch("os.path.isfile", return_value=True), \
             patch("os.path.exists", return_value=True), \
             patch("boat.test.check._read_sysfs", return_value="up"):
            issues = check_environment(cfg)
            assert len(issues) == 0, issues

    def test_gateway_binary_missing(self) -> None:
        cfg = _make_config(gateway_binary="/nonexistent/gateway")
        with patch("os.path.isfile", return_value=False), \
             patch("os.path.exists", return_value=True), \
             patch("boat.test.check._read_sysfs", return_value="up"):
            issues = check_environment(cfg)
            assert any("Gateway binary" in i for i in issues)

    def test_can_interface_missing(self) -> None:
        cfg = _make_config()
        with patch("os.path.exists", return_value=False), \
             patch("os.path.isfile", return_value=True):
            issues = check_environment(cfg)
            assert any("not found" in i for i in issues)

    def test_empty_config_no_issues(self) -> None:
        cfg = EnvironmentConfig(
            schema_version="1.0", name="minimal", description="",
            gateway=GatewayConfig(address=""),
            buses={},
        )
        issues = check_environment(cfg)
        assert len(issues) == 0
