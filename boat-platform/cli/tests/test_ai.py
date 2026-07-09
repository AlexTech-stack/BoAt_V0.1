"""Tests for `boat ai *` commands."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from boat_cli.main import app

runner = CliRunner()


PLUGIN_CODE = (
    '"""Example CAN node."""\n'
    "from boat.can_node import CanNode\n\n"
    "class MyPlugin(CanNode):\n"
    "    def on_frame(self, frame, iface):\n"
    "        pass\n\n"
    "if __name__ == '__main__':\n"
    "    MyPlugin().run()\n"
)


def test_ai_scenario() -> None:
    with patch("boat_cli.ai.ai_backend.complete", return_value='{"duration_ticks": 500}'):
        result = runner.invoke(app, [
            "ai", "scenario",
            "--desc", "A 5-second simulation with speed signal",
        ])
    assert result.exit_code == 0
    assert "duration_ticks" in result.stdout


def test_ai_bus_setup() -> None:
    with patch("boat_cli.ai.ai_backend.complete", return_value="sudo modprobe vcan"):
        result = runner.invoke(app, [
            "ai", "bus-setup",
            "--query", "Set up vcan0 and start the gateway",
        ])
    assert result.exit_code == 0
    assert "modprobe vcan" in result.stdout


def test_ai_cli() -> None:
    with patch("boat_cli.ai.ai_backend.complete", return_value="boat frame send --bus-type can --can-id 0x100 --iface vcan0"):
        result = runner.invoke(app, [
            "ai", "cli",
            "--query", "Send a CAN frame 0x100 on vcan0",
        ])
    assert result.exit_code == 0
    assert "boat frame send" in result.stdout


def test_ai_plugin(tmp_path: Path) -> None:
    out_file = tmp_path / "my_plugin.py"
    with patch("boat_cli.ai.ai_backend.complete", return_value=PLUGIN_CODE):
        result = runner.invoke(app, [
            "ai", "plugin",
            "--desc", "Listen on vcan0 for ID 0x100",
            "--out", str(out_file),
        ])
    assert result.exit_code == 0
    assert out_file.exists()
    content = out_file.read_text()
    assert "CanNode" in content


def test_ai_plugin_default_name() -> None:
    """When --out is not specified, derive filename from description."""
    with patch("boat_cli.ai.ai_backend.complete", return_value=PLUGIN_CODE), \
         patch("boat_cli.ai.Path.write_text"):
        result = runner.invoke(app, [
            "ai", "plugin",
            "--desc", "Listen on vcan0 for ID 0x100",
        ])
    assert result.exit_code == 0


def test_ai_config_show() -> None:
    """boat ai config show exits 0 (uses defaults when no config file)."""
    result = runner.invoke(app, ["ai", "config", "show"])
    assert result.exit_code == 0
    assert "endpoint" in result.stdout
    assert "api_key" in result.stdout


def test_ai_config_set() -> None:
    """boat ai config set updates the config file."""
    with patch("boat_cli.ai.ai_config.save") as mock_save:
        mock_save.return_value = Mock(
            config_path=Path("/tmp/ai.toml"),
            endpoint="http://test:11434/v1",
            model="test-model",
            timeout=30,
            api_key="sk-test123",
            masked_api_key="sk-t...t123",
        )
        result = runner.invoke(app, [
            "ai", "config", "set",
            "--endpoint", "http://test:11434/v1",
            "--model", "test-model",
        ])
    assert result.exit_code == 0
    assert "http://test:11434/v1" in result.stdout


def test_ai_config_set_with_api_key() -> None:
    """boat ai config set --api-key stores the key."""
    with patch("boat_cli.ai.ai_config.save") as mock_save:
        mock_save.return_value = Mock(
            config_path=Path("/tmp/ai.toml"),
            endpoint="http://localhost:11434/v1",
            model="qwen2.5-coder:3b",
            timeout=120,
            api_key="sk-test-key-12345",
            masked_api_key="sk-t...2345",
        )
        result = runner.invoke(app, [
            "ai", "config", "set",
            "--api-key", "sk-test-key-12345",
        ])
    assert result.exit_code == 0
    mock_save.assert_called_once_with(api_key="sk-test-key-12345", endpoint=None, model=None, timeout=None)
    assert "sk-t...2345" in result.stdout


def test_ai_help() -> None:
    result = runner.invoke(app, ["ai", "--help"])
    assert result.exit_code == 0
    assert "scenario" in result.stdout
    assert "bus-setup" in result.stdout
    assert "cli" in result.stdout
    assert "plugin" in result.stdout
