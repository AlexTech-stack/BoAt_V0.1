from boat.test.config import EnvironmentConfig, ManifestConfig


class TestEnvironmentConfig:
    def test_from_dict(self) -> None:
        raw = {
            "schema_version": "1.0",
            "name": "test-env",
            "description": "A test environment",
            "gateway": {"address": "localhost:50051", "tick_ms": 10},
            "buses": {
                "can1": {"type": "virtual", "interface": "vcan0"},
                "eth0": {"type": "virtual_eth", "interface": "veth0",
                         "multicast_group": "239.255.0.1", "port": 51000},
            },
            "dut": {"name": "sim-dut", "type": "plugin",
                    "so_path": "plugin.so", "config_json": "{}"},
            "plugins": [{"so_path": "extra.so", "config_json": '{"a":1}'}],
        }
        cfg = EnvironmentConfig.from_dict(raw)
        assert cfg.schema_version == "1.0"
        assert cfg.name == "test-env"
        assert cfg.description == "A test environment"
        assert cfg.gateway.address == "localhost:50051"
        assert cfg.gateway.tick_ms == 10
        assert "can1" in cfg.buses
        assert cfg.buses["can1"].type == "virtual"
        assert cfg.buses["can1"].interface == "vcan0"
        assert cfg.buses["eth0"].multicast_group == "239.255.0.1"
        assert cfg.buses["eth0"].port == 51000
        assert cfg.dut is not None
        assert cfg.dut.name == "sim-dut"
        assert cfg.dut.so_path == "plugin.so"
        assert len(cfg.plugins) == 1
        assert cfg.plugins[0].so_path == "extra.so"

    def test_round_trip(self) -> None:
        raw = {
            "schema_version": "1.0",
            "name": "roundtrip",
            "gateway": {"address": "127.0.0.1:50051", "tick_ms": 5},
            "buses": {
                "can1": {"type": "physical", "interface": "can0",
                         "bitrate": 500000, "fd": True},
            },
        }
        cfg = EnvironmentConfig.from_dict(raw)
        restored = EnvironmentConfig.from_dict(cfg.to_dict())
        assert restored.name == "roundtrip"
        assert restored.gateway.tick_ms == 5
        assert restored.buses["can1"].bitrate == 500000
        assert restored.buses["can1"].fd is True

    def test_validate_ok(self) -> None:
        cfg = EnvironmentConfig.from_dict({
            "schema_version": "1.0",
            "name": "valid",
            "gateway": {"address": "x:1"},
            "buses": {"can1": {"type": "virtual", "interface": "vcan0"}},
        })
        issues = cfg.validate()
        assert len(issues) == 0

    def test_validate_virtual_wrong_iface(self) -> None:
        cfg = EnvironmentConfig.from_dict({
            "schema_version": "1.0",
            "name": "bad",
            "gateway": {"address": "x:1"},
            "buses": {"can1": {"type": "virtual", "interface": "can0"}},
        })
        issues = cfg.validate()
        assert any("vcan*" in i for i in issues)

    def test_validate_plugin_no_so(self) -> None:
        cfg = EnvironmentConfig.from_dict({
            "schema_version": "1.0",
            "name": "bad",
            "gateway": {"address": "x:1"},
            "buses": {"can1": {"type": "virtual", "interface": "vcan0"}},
            "dut": {"name": "x", "type": "plugin"},
        })
        issues = cfg.validate()
        assert any("so_path" in i for i in issues)


class TestManifestConfig:
    def test_from_dict(self) -> None:
        raw = {
            "schema_version": "1.0",
            "name": "suite1",
            "version": "1.2.0",
            "description": "A test suite",
            "environment_config": "env_virtual.json",
            "setup": [{"action": "load_scenario", "params": {"id": "s1"}}],
            "teardown": [{"action": "cleanup", "params": {}}],
            "tests": [
                {"id": "TC-001", "name": "Test 1", "file": "test1",
                 "timeout_s": 30},
                {"id": "TC-002", "name": "Test 2", "file": "test2"},
            ],
        }
        m = ManifestConfig.from_dict(raw)
        assert m.name == "suite1"
        assert m.version == "1.2.0"
        assert m.environment_config == "env_virtual.json"
        assert len(m.setup) == 1
        assert m.setup[0].action == "load_scenario"
        assert len(m.tests) == 2
        assert m.tests[0].id == "TC-001"
        assert m.tests[0].timeout_s == 30
        assert m.tests[1].timeout_s == 60  # default

    def test_round_trip(self) -> None:
        raw = {
            "schema_version": "1.0",
            "name": "round",
            "tests": [{"id": "T1", "name": "T1", "file": "f1"}],
        }
        m = ManifestConfig.from_dict(raw)
        restored = ManifestConfig.from_dict(m.to_dict())
        assert restored.name == "round"
        assert restored.tests[0].id == "T1"
