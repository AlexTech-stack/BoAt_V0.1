from __future__ import annotations

import sys

import grpc
import typer

from boat.v1 import simulation_pb2

from .output import print_error, print_table

sim_app = typer.Typer()


def _rpc_error(ex: grpc.RpcError) -> None:
    print_error(f"RPC error [{ex.code().name}]: {ex.details()}")
    sys.exit(1)


@sim_app.command("create")
def create_simulation(ctx: typer.Context, scenario: str = typer.Option(..., "--scenario")) -> None:
    try:
        response = ctx.obj["client"].simulation.CreateSimulation(
            simulation_pb2.CreateSimulationRequest(scenario_id=scenario)
        )
        sim = response.simulation
        print_table(["simulation_id"], [[sim.simulation_id]], ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@sim_app.command("start")
def start_simulation(ctx: typer.Context, sim_id: str) -> None:
    try:
        response = ctx.obj["client"].simulation.StartSimulation(
            simulation_pb2.StartSimulationRequest(simulation_id=sim_id)
        )
        print_table(["status"], [[int(response.simulation.state)]], ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@sim_app.command("pause")
def pause_simulation(ctx: typer.Context, sim_id: str) -> None:
    try:
        response = ctx.obj["client"].simulation.PauseSimulation(
            simulation_pb2.PauseSimulationRequest(simulation_id=sim_id)
        )
        print_table(["status"], [[int(response.simulation.state)]], ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@sim_app.command("step")
def step_simulation(ctx: typer.Context, sim_id: str, ticks: int = typer.Option(1, "--ticks")) -> None:
    try:
        response = ctx.obj["client"].simulation.StepSimulation(
            simulation_pb2.StepSimulationRequest(simulation_id=sim_id, ticks=ticks)
        )
        print_table(["tick"], [[response.simulation.tick]], ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@sim_app.command("stop")
def stop_simulation(ctx: typer.Context, sim_id: str) -> None:
    try:
        response = ctx.obj["client"].simulation.StopSimulation(
            simulation_pb2.StopSimulationRequest(simulation_id=sim_id)
        )
        print_table(["status"], [[int(response.simulation.state)]], ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@sim_app.command("state")
def state_simulation(ctx: typer.Context, sim_id: str) -> None:
    try:
        response = ctx.obj["client"].simulation.GetSimulationState(
            simulation_pb2.GetSimulationStateRequest(simulation_id=sim_id)
        )
        print_table(["state"], [[int(response.simulation.state)]], ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@sim_app.command("reset")
def reset_simulation(ctx: typer.Context, sim_id: str) -> None:
    try:
        response = ctx.obj["client"].simulation.ResetSimulation(
            simulation_pb2.ResetSimulationRequest(simulation_id=sim_id)
        )
        print_table(["status"], [[int(response.simulation.state)]], ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@sim_app.command("list")
def list_simulations(ctx: typer.Context) -> None:
    try:
        response = ctx.obj["client"].simulation.ListSimulations(simulation_pb2.ListSimulationsRequest())
        rows = [[sim.simulation_id, int(sim.state)] for sim in response.simulations]
        print_table(["simulation_id", "state"], rows, ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@sim_app.command("watch")
def watch_simulation(ctx: typer.Context, sim_id: str) -> None:
    try:
        stream = ctx.obj["client"].simulation.WatchSimulation(
            simulation_pb2.GetSimulationStateRequest(simulation_id=sim_id)
        )
        for item in stream:
            print_table(
                ["simulation_id", "tick", "state"],
                [[item.simulation.simulation_id, item.simulation.tick, int(item.simulation.state)]],
                ctx.obj["json_mode"],
            )
    except grpc.RpcError as ex:
        _rpc_error(ex)
