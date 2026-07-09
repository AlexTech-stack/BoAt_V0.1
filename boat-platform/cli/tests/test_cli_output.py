from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from boat_cli.main import app

runner = CliRunner()


def test_print_table_json_mode_with_list_command() -> None:
    fake_client = SimpleNamespace(
        simulation=SimpleNamespace(
            ListSimulations=lambda _req: SimpleNamespace(
                simulations=[SimpleNamespace(simulation_id="sim-1", state=1)]
            )
        ),
        close=lambda: None,
    )
    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        result = runner.invoke(app, ["--json", "sim", "list"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout.strip() or "[]")
    assert isinstance(parsed, list)
    assert parsed[0]["simulation_id"] == "sim-1"


def test_print_table_rich_mode_with_list_command() -> None:
    fake_client = SimpleNamespace(
        simulation=SimpleNamespace(
            ListSimulations=lambda _req: SimpleNamespace(
                simulations=[SimpleNamespace(simulation_id="sim-1", state=1)]
            )
        ),
        close=lambda: None,
    )
    with patch("boat_cli.main.BoAtClient", return_value=fake_client):
        result = runner.invoke(app, ["sim", "list"])
    assert result.exit_code == 0
    assert "simulation_id" in result.stdout
