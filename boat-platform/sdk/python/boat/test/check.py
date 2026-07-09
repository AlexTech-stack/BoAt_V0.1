from __future__ import annotations

import os
import socket
from typing import Optional

from boat.test.config import EnvironmentConfig


def check_environment(config: EnvironmentConfig) -> list[str]:
    """Run pre-flight checks on an environment configuration.

    Returns a list of issue messages (empty = all clear).
    """
    issues: list[str] = []

    _check_gateway_binary(config, issues)
    _check_can_interfaces(config, issues)
    _check_eth_interfaces(config, issues)
    _check_pdu_database(config, issues)
    _check_dut_plugin(config, issues)
    _check_gateway_connectivity(config, issues)

    return issues


def _check_gateway_binary(config: EnvironmentConfig, issues: list[str]) -> None:
    binary = config.gateway.binary
    if binary and not os.path.isfile(binary):
        issues.append(f"Gateway binary not found: {binary}")


def _check_can_interfaces(config: EnvironmentConfig, issues: list[str]) -> None:
    for name, bus in config.buses.items():
        if bus.type not in ("virtual", "physical"):
            continue
        iface = bus.interface
        sys_path = f"/sys/class/net/{iface}"
        if not os.path.exists(sys_path):
            issues.append(f"CAN interface '{iface}' ({name}) not found in /sys/class/net")
            continue
        if bus.type == "virtual":
            operstate = _read_sysfs(f"{sys_path}/operstate") or "unknown"
            if operstate != "up":
                issues.append(f"Virtual CAN '{iface}' ({name}) state is '{operstate}', expected 'up'")
        if bus.type == "physical":
            operstate = _read_sysfs(f"{sys_path}/operstate") or "unknown"
            if operstate != "up":
                issues.append(f"Physical CAN '{iface}' ({name}) state is '{operstate}', expected 'up'")
            driver = _read_sysfs(f"{sys_path}/device/driver")
            if driver:
                driver = os.path.basename(driver)
            if not driver:
                issues.append(f"Physical CAN '{iface}' ({name}): no driver detected")


def _check_eth_interfaces(config: EnvironmentConfig, issues: list[str]) -> None:
    for name, bus in config.buses.items():
        if bus.type not in ("virtual_eth", "raw_eth"):
            continue
        iface = bus.interface
        sys_path = f"/sys/class/net/{iface}"
        if not os.path.exists(sys_path):
            issues.append(f"Ethernet interface '{iface}' ({name}) not found in /sys/class/net")


def _check_pdu_database(config: EnvironmentConfig, issues: list[str]) -> None:
    pdu_db = config.gateway.pdu_database
    if pdu_db and not os.path.isfile(pdu_db):
        issues.append(f"PDU database not found: {pdu_db}")


def _check_dut_plugin(config: EnvironmentConfig, issues: list[str]) -> None:
    if config.dut and config.dut.type == "plugin" and config.dut.so_path:
        if not os.path.isfile(config.dut.so_path):
            issues.append(f"DUT plugin not found: {config.dut.so_path}")


def _check_gateway_connectivity(config: EnvironmentConfig, issues: list[str]) -> None:
    addr = config.gateway.address
    if not addr:
        return
    try:
        host, port_str = addr.split(":", 1)
        port = int(port_str)
        sock = socket.create_connection((host, port), timeout=2)
        sock.close()
    except (ValueError, socket.error, OSError):
        issues.append(f"Gateway at {addr} is not reachable")


def _read_sysfs(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None
