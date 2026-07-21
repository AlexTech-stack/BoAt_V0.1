from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class BusConfig:
    logical_name: str
    type: str
    interface: str
    bitrate: Optional[int] = None
    fd: bool = False
    multicast_group: Optional[str] = None
    port: Optional[int] = None

    @classmethod
    def from_dict(cls, name: str, d: dict) -> BusConfig:
        return cls(
            logical_name=name,
            type=d["type"],
            interface=d["interface"],
            bitrate=d.get("bitrate"),
            fd=d.get("fd", False),
            multicast_group=d.get("multicast_group"),
            port=d.get("port"),
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "type": self.type,
            "interface": self.interface,
        }
        if self.bitrate is not None:
            d["bitrate"] = self.bitrate
        if self.fd:
            d["fd"] = True
        if self.multicast_group is not None:
            d["multicast_group"] = self.multicast_group
        if self.port is not None:
            d["port"] = self.port
        return d

    def summary(self) -> str:
        parts = [f"{self.type}:{self.interface}"]
        if self.bitrate:
            parts.append(f"{self.bitrate}bps")
        if self.fd:
            parts.append("FD")
        return " ".join(parts)


@dataclass
class DutConfig:
    name: str
    type: str
    so_path: Optional[str] = None
    config_json: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> DutConfig:
        return cls(
            name=d["name"],
            type=d["type"],
            so_path=d.get("so_path"),
            config_json=d.get("config_json"),
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.so_path is not None:
            d["so_path"] = self.so_path
        if self.config_json is not None:
            d["config_json"] = self.config_json
        return d

    def summary(self) -> str:
        if self.type == "plugin":
            return f"plugin:{self.so_path or '?'}"
        return self.type


@dataclass
class PluginRef:
    so_path: str
    config_json: str = "{}"

    @classmethod
    def from_dict(cls, d: dict) -> PluginRef:
        return cls(so_path=d["so_path"], config_json=d.get("config_json", "{}"))

    def to_dict(self) -> dict:
        return {"so_path": self.so_path, "config_json": self.config_json}


@dataclass
class DeviceConfig:
    """An electrical device declared in an environment config's `devices:` block.

    Translates to a BoAt device plugin loaded via BOAT_NODE_PLUGINS. `virtual`
    devices use the deterministic model plugins; `scpi`/`gpio`/`modbus` use the
    live-hardware backends. The device_id (the dict key, e.g. "psu.main") sets
    the signal prefix; the plugin `id` is the part after the kind (e.g. "main").
    """

    device_id: str
    type: str   # virtual | scpi | gpio | modbus
    kind: str   # power_supply | relay | generator | generic_io
    host: Optional[str] = None
    port: Optional[int] = None
    resource: Optional[str] = None
    poll_ms: Optional[int] = None
    config_json: str = "{}"

    @classmethod
    def from_dict(cls, device_id: str, d: dict) -> DeviceConfig:
        return cls(
            device_id=device_id,
            type=d["type"],
            kind=d["kind"],
            host=d.get("host"),
            port=d.get("port"),
            resource=d.get("resource"),
            poll_ms=d.get("poll_ms"),
            config_json=d.get("config_json", "{}"),
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"type": self.type, "kind": self.kind}
        for k in ("host", "port", "resource", "poll_ms"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        if self.config_json and self.config_json != "{}":
            d["config_json"] = self.config_json
        return d

    def plugin_id(self) -> str:
        return self.device_id.split(".", 1)[1] if "." in self.device_id else self.device_id

    def plugin_name(self) -> Optional[str]:
        if self.type == "virtual":
            return {
                "power_supply": "virtual_psu",
                "relay": "virtual_relay",
                "generator": "virtual_generator",
            }.get(self.kind)
        if self.type == "scpi":
            return "scpi_device"
        if self.type == "gpio":
            return "gpio_relay"
        if self.type == "modbus":
            return "modbus_device"
        return None

    def plugin_spec(self, plugin_dir: str) -> str:
        """Return the BOAT_NODE_PLUGINS entry (`path.so?{json}`) for this device."""
        name = self.plugin_name()
        if name is None:
            raise ValueError(f"no plugin for device type={self.type} kind={self.kind}")
        cfg: dict[str, Any] = {"id": self.plugin_id()}
        for k in ("host", "port", "resource", "poll_ms"):
            v = getattr(self, k)
            if v is not None:
                cfg[k] = v
        if self.config_json and self.config_json != "{}":
            try:
                cfg.update(json.loads(self.config_json))
            except (ValueError, TypeError):
                pass
        blob = json.dumps(cfg, separators=(",", ":"))
        return f"{plugin_dir}/{name}/{name}.so?{blob}"


@dataclass
class GatewayConfig:
    binary: Optional[str] = None
    tick_ms: int = 10
    address: str = "localhost:50051"
    pdu_database: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> GatewayConfig:
        return cls(
            binary=d.get("binary"),
            tick_ms=d.get("tick_ms", 10),
            address=d.get("address", "localhost:50051"),
            pdu_database=d.get("pdu_database"),
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "address": self.address,
            "tick_ms": self.tick_ms,
        }
        if self.binary is not None:
            d["binary"] = self.binary
        if self.pdu_database is not None:
            d["pdu_database"] = self.pdu_database
        return d

    def summary(self) -> str:
        s = f"{self.address}"
        if self.binary:
            s += f" ({os.path.basename(self.binary)})"
        s += f" tick={self.tick_ms}ms"
        return s


@dataclass
class EnvironmentConfig:
    schema_version: str
    name: str
    description: Optional[str]
    gateway: GatewayConfig
    buses: dict[str, BusConfig]
    dut: Optional[DutConfig] = None
    plugins: list[PluginRef] = field(default_factory=list)
    devices: dict[str, DeviceConfig] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str) -> EnvironmentConfig:
        with open(path) as f:
            d = json.load(f)
        return cls.from_dict(d)

    @classmethod
    def from_dict(cls, d: dict) -> EnvironmentConfig:
        buses = {}
        for name, bus_dict in d.get("buses", {}).items():
            buses[name] = BusConfig.from_dict(name, bus_dict)

        plugins_list = [PluginRef.from_dict(p) for p in d.get("plugins", [])]

        devices = {}
        for dev_id, dev_dict in d.get("devices", {}).items():
            devices[dev_id] = DeviceConfig.from_dict(dev_id, dev_dict)

        return cls(
            schema_version=d["schema_version"],
            name=d["name"],
            description=d.get("description"),
            gateway=GatewayConfig.from_dict(d["gateway"]),
            buses=buses,
            dut=DutConfig.from_dict(d["dut"]) if d.get("dut") else None,
            plugins=plugins_list,
            devices=devices,
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "name": self.name,
            "gateway": self.gateway.to_dict(),
            "buses": {name: bus.to_dict() for name, bus in self.buses.items()},
        }
        if self.description is not None:
            d["description"] = self.description
        if self.dut is not None:
            d["dut"] = self.dut.to_dict()
        if self.plugins:
            d["plugins"] = [p.to_dict() for p in self.plugins]
        if self.devices:
            d["devices"] = {k: v.to_dict() for k, v in self.devices.items()}
        return d

    def plugin_dir(self) -> str:
        """Best-effort path to the built plugins, derived from the gateway binary."""
        b = self.gateway.binary or ""
        marker = os.path.join("src", "gateway")
        idx = b.find(marker)
        if idx > 0:
            return os.path.join(b[:idx], "src", "plugins")
        return os.path.join("build", "debug", "src", "plugins")

    def node_plugin_specs(self) -> list[str]:
        """BOAT_NODE_PLUGINS entries for the declared devices (empty if none).

        The device_manager is prepended so DeviceService/`boat device` work.
        """
        if not self.devices:
            return []
        pdir = self.plugin_dir()
        specs = [f"{pdir}/device_manager/device_manager.so"]
        specs += [dev.plugin_spec(pdir) for dev in self.devices.values()]
        return specs

    def snapshot(self) -> dict:
        """Return a frozen dict for embedding in test reports."""
        return self.to_dict()

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not self.buses:
            issues.append("At least one bus must be defined")
        for name, bus in self.buses.items():
            if bus.type in ("virtual",) and not bus.interface.startswith("vcan"):
                issues.append(f"Bus {name}: virtual type expects a vcan* interface, got {bus.interface}")
            if bus.type == "virtual_eth" and not bus.interface.startswith("veth"):
                issues.append(f"Bus {name}: virtual_eth type expects a veth* interface, got {bus.interface}")
        if self.dut and self.dut.type == "plugin" and not self.dut.so_path:
            issues.append("DUT type 'plugin' requires so_path")
        return issues


@dataclass
class ManifestAction:
    action: str
    params: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> ManifestAction:
        return cls(action=d["action"], params=d.get("params", {}))

    def to_dict(self) -> dict:
        return {"action": self.action, "params": self.params}


@dataclass
class ManifestTestEntry:
    id: str
    name: str
    file: str
    description: Optional[str] = None
    timeout_s: int = 60
    steps: Optional[list[str]] = None

    @classmethod
    def from_dict(cls, d: dict) -> ManifestTestEntry:
        return cls(
            id=d["id"],
            name=d["name"],
            file=d["file"],
            description=d.get("description"),
            timeout_s=d.get("timeout_s", 60),
            steps=d.get("steps"),
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id, "name": self.name, "file": self.file}
        if self.description is not None:
            d["description"] = self.description
        if self.timeout_s != 60:
            d["timeout_s"] = self.timeout_s
        if self.steps is not None:
            d["steps"] = self.steps
        return d


@dataclass
class ManifestConfig:
    schema_version: str
    name: str
    version: Optional[str] = None
    description: Optional[str] = None
    environment_config: Optional[str] = None
    setup: list[ManifestAction] = field(default_factory=list)
    teardown: list[ManifestAction] = field(default_factory=list)
    tests: list[ManifestTestEntry] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str) -> ManifestConfig:
        with open(path) as f:
            d = json.load(f)
        return cls.from_dict(d)

    @classmethod
    def from_dict(cls, d: dict) -> ManifestConfig:
        return cls(
            schema_version=d["schema_version"],
            name=d["name"],
            version=d.get("version"),
            description=d.get("description"),
            environment_config=d.get("environment_config"),
            setup=[ManifestAction.from_dict(a) for a in d.get("setup", [])],
            teardown=[ManifestAction.from_dict(a) for a in d.get("teardown", [])],
            tests=[ManifestTestEntry.from_dict(t) for t in d.get("tests", [])],
        )

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "name": self.name,
            "tests": [t.to_dict() for t in self.tests],
        }
        if self.version is not None:
            d["version"] = self.version
        if self.description is not None:
            d["description"] = self.description
        if self.environment_config is not None:
            d["environment_config"] = self.environment_config
        if self.setup:
            d["setup"] = [a.to_dict() for a in self.setup]
        if self.teardown:
            d["teardown"] = [a.to_dict() for a in self.teardown]
        return d
