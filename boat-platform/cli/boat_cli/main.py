from __future__ import annotations

import typer

from boat.client import BoAtClient

from .ai import ai_app
from .can_tp import can_tp_app
from .db import db_app
from .frame import frame_app
from .pdu import pdu_app
from .plugin import plugin_app
from .replay import replay_app
from .scenario import scenario_app
from .sim import sim_app
from .test import test_app
from .trace import trace_app

app = typer.Typer()

app.add_typer(ai_app,       name="ai",    help="AI-powered assistants (scenario, bus-setup, cli, plugin).")
app.add_typer(sim_app,      name="sim")
app.add_typer(scenario_app, name="scenario")
app.add_typer(replay_app,   name="replay")
app.add_typer(plugin_app,   name="plugin")
app.add_typer(can_tp_app,   name="can-tp")
app.add_typer(frame_app,    name="frame", help="Unified frame send / subscribe via FrameService.")
app.add_typer(pdu_app,      name="pdu",  help="PDU routing and transmission.")
app.add_typer(db_app,       name="db",   help="PDU database inspection.")
app.add_typer(test_app,     name="test",  help="Run tests and inspect test configurations.")
app.add_typer(trace_app,    name="trace")


@app.callback()
def main(
    ctx: typer.Context,
    host: str = typer.Option("localhost:50051", "--host"),
    json_mode: bool = typer.Option(False, "--json"),
) -> None:
    ctx.obj = {"host": host, "json_mode": json_mode, "client": BoAtClient(address=host)}
