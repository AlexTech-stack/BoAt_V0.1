from __future__ import annotations

import sys

import grpc
import typer

from boat.v1 import device_pb2

from .output import print_error, print_table

device_app = typer.Typer()

_KIND = {
    device_pb2.DEVICE_KIND_POWER_SUPPLY: "power_supply",
    device_pb2.DEVICE_KIND_RELAY: "relay",
    device_pb2.DEVICE_KIND_GENERATOR: "generator",
    device_pb2.DEVICE_KIND_GENERIC_IO: "generic_io",
    device_pb2.DEVICE_KIND_UNSPECIFIED: "unspecified",
}


def _rpc_error(ex: grpc.RpcError) -> None:
    print_error(f"RPC error [{ex.code().name}]: {ex.details()}")
    sys.exit(1)


@device_app.command("list")
def list_devices(ctx: typer.Context) -> None:
    """List devices discovered on the signal bus."""
    try:
        resp = ctx.obj["client"].device.ListDevices(device_pb2.ListDevicesRequest())
    except grpc.RpcError as ex:
        _rpc_error(ex)
    rows = []
    for d in resp.devices:
        chans = ", ".join(
            f"{c.name}{'*' if c.settable else ''}"
            + (f"={c.value:g}{c.unit}" if c.has_value else "")
            for c in d.channels
        )
        rows.append([d.device_id, _KIND.get(d.kind, "?"), chans])
    print_table(["device_id", "kind", "channels (*=settable)"], rows,
                ctx.obj["json_mode"])


@device_app.command("set")
def set_control(
    ctx: typer.Context,
    device_id: str = typer.Argument(..., help="e.g. psu.main, relay.kl15"),
    channel: str = typer.Argument(..., help="e.g. voltage, enable, state"),
    value: float = typer.Argument(..., help="setpoint / command (relay: 0=open, 1=closed)"),
) -> None:
    """Drive a controllable channel of a device."""
    try:
        resp = ctx.obj["client"].device.SetControl(
            device_pb2.SetControlRequest(
                device_id=device_id, channel=channel, value=value
            )
        )
    except grpc.RpcError as ex:
        _rpc_error(ex)
    if not resp.accepted:
        print_error(resp.error.message or "rejected")
        sys.exit(1)
    print_table(["device_id", "channel", "value", "status"],
                [[device_id, channel, f"{value:g}", "accepted"]],
                ctx.obj["json_mode"])


@device_app.command("read")
def read_state(
    ctx: typer.Context,
    device_id: str = typer.Argument(..., help="e.g. psu.main"),
) -> None:
    """Read a device's channels and last measured values."""
    try:
        resp = ctx.obj["client"].device.ReadState(
            device_pb2.ReadStateRequest(device_id=device_id)
        )
    except grpc.RpcError as ex:
        _rpc_error(ex)
    if not resp.found:
        print_error(f"device '{device_id}' not found")
        sys.exit(1)
    rows = [
        [c.name, "yes" if c.settable else "", "yes" if c.readable else "",
         (f"{c.value:g}" if c.has_value else "-"), c.unit]
        for c in resp.device.channels
    ]
    print_table(["channel", "settable", "readable", "value", "unit"], rows,
                ctx.obj["json_mode"])
