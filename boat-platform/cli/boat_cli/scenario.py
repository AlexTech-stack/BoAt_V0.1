from __future__ import annotations

from pathlib import Path

import typer

from boat.v1 import scenario_pb2

from .output import print_table

scenario_app = typer.Typer()


@scenario_app.command("create")
def create_scenario(ctx: typer.Context, file: Path = typer.Option(..., "--file")) -> None:
    import json as _json
    content = file.read_text(encoding="utf-8")
    parsed = _json.loads(content)
    scenario_id = parsed.get("id", file.stem)
    name = parsed.get("name", file.stem)
    req = scenario_pb2.CreateScenarioRequest(
        scenario=scenario_pb2.Scenario(scenario_id=scenario_id, name=name, content=content)
    )
    response = ctx.obj["client"].scenario.CreateScenario(req)
    print_table(["scenario_id"], [[response.scenario.scenario_id]], ctx.obj["json_mode"])


@scenario_app.command("get")
def get_scenario(ctx: typer.Context, scenario_id: str) -> None:
    response = ctx.obj["client"].scenario.GetScenario(
        scenario_pb2.GetScenarioRequest(scenario_id=scenario_id)
    )
    print_table(
        ["scenario_id", "name", "content"],
        [[response.scenario.scenario_id, response.scenario.name, response.scenario.content]],
        ctx.obj["json_mode"],
    )


@scenario_app.command("list")
def list_scenarios(ctx: typer.Context) -> None:
    response = ctx.obj["client"].scenario.ListScenarios(scenario_pb2.ListScenariosRequest())
    rows = [[item.scenario_id, item.name] for item in response.scenarios]
    print_table(["scenario_id", "name"], rows, ctx.obj["json_mode"])


@scenario_app.command("delete")
def delete_scenario(ctx: typer.Context, scenario_id: str) -> None:
    response = ctx.obj["client"].scenario.DeleteScenario(
        scenario_pb2.DeleteScenarioRequest(scenario_id=scenario_id)
    )
    print_table(["deleted"], [[bool(response.deleted)]], ctx.obj["json_mode"])


@scenario_app.command("validate")
def validate_scenario(ctx: typer.Context, file: Path = typer.Option(..., "--file")) -> None:
    content = file.read_text(encoding="utf-8")
    response = ctx.obj["client"].scenario.ValidateScenario(
        scenario_pb2.ValidateScenarioRequest(content=content)
    )
    rows = [[bool(response.valid), ",".join(response.issues)]]
    print_table(["valid", "issues"], rows, ctx.obj["json_mode"])
