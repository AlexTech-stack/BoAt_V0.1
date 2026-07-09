"""CLI: boat frame send / subscribe — unified FrameService."""

from __future__ import annotations

import os
import sys

import grpc
import typer

from boat.v1 import can_pb2
from boat.v1 import frame_pb2

from boat.client import BoAtClient

from .output import print_error

frame_app = typer.Typer()


def _parse_hex(hex_str: str) -> bytes:
    cleaned = hex_str.replace(":", "").replace(" ", "")
    return bytes.fromhex(cleaned)


def _parse_int(value: str) -> int:
    if value.startswith("0x") or value.startswith("0X"):
        return int(value, 16)
    return int(value)


def _pick_iface(client: BoAtClient, bus_type: str) -> str:
    """Return the first registered gateway interface when none is specified."""
    if bus_type.upper() in ("CAN", "CANFD"):
        try:
            resp = client.can.ListBuses(can_pb2.ListBusesRequest())
            if resp.buses:
                return resp.buses[0].iface
        except grpc.RpcError:
            pass
    elif bus_type.upper() == "ETHERNET":
        try:
            from boat.v1 import ethernet_pb2
            resp = client.ethernet.ListInterfaces(ethernet_pb2.ListEthernetInterfacesRequest())
            if resp.ifaces:
                return resp.ifaces[0]
        except (grpc.RpcError, AttributeError):
            pass
    return ""


@frame_app.command("send")
def send_frame(
    ctx: typer.Context,
    bus_type: str = typer.Option(
        ..., "--bus-type", "-b",
        help="CAN, CANFD, ETHERNET, or PDU (TCP is not supported here -- "
             "it's connection-oriented; use the TCP plugin instead)"),
    iface: str = typer.Option("", "--iface", "-i", help="Interface name (auto-selected if omitted)"),
    can_id: str = typer.Option("0", "--can-id", help="CAN identifier (decimal or 0x hex)"),
    data: str = typer.Option(..., "--data", "-d", help="Payload hex, e.g. AABBCCDD"),
    ethertype: str = typer.Option("0", "--ethertype", help="Ethernet EtherType (decimal or 0x hex)"),
    dst_mac: str = typer.Option("", "--dst-mac", help="Destination MAC (xx:xx:xx:xx:xx:xx)"),
    src_mac: str = typer.Option("", "--src-mac", help="Source MAC"),
    dst_ip: str = typer.Option("", "--dst-ip", help="Destination IP"),
    src_ip: str = typer.Option("", "--src-ip", help="Source IP"),
    dst_port: str = typer.Option("0", "--dst-port", help="TCP/UDP destination port (decimal or 0x hex)"),
    src_port: str = typer.Option("0", "--src-port", help="TCP/UDP source port (decimal or 0x hex)"),
    pdu_id: str = typer.Option("0", "--pdu-id", help="PDU identifier (decimal or 0x hex)"),
) -> None:
    """Send a unified Frame via FrameService."""
    client = ctx.obj["client"]

    try:
        payload = _parse_hex(data)
    except ValueError as e:
        print_error(f"Invalid hex payload: {e}")
        sys.exit(1)

    try:
        can_id_int = _parse_int(can_id)
        ethertype_int = _parse_int(ethertype)
        dst_port_int = _parse_int(dst_port)
        src_port_int = _parse_int(src_port)
        pdu_id_int = _parse_int(pdu_id)
    except ValueError as e:
        print_error(f"Invalid numeric value: {e}")
        sys.exit(1)

    bt_map = {
        "CAN": frame_pb2.Frame.CAN,
        "CANFD": frame_pb2.Frame.CANFD,
        "ETHERNET": frame_pb2.Frame.ETHERNET,
        "TCP": frame_pb2.Frame.TCP,
        "PDU": frame_pb2.Frame.PDU,
    }
    bt = bt_map.get(bus_type.upper())
    if bt is None:
        print_error(f"Unknown bus type: {bus_type}. "
                     f"Valid: {', '.join(bt_map.keys())}")
        sys.exit(1)
    if bt == frame_pb2.Frame.TCP:
        print_error(
            "boat frame send does not support TCP -- it's connection-oriented, "
            "not a fire-and-forget frame. Use the TCP plugin's own connection "
            "API instead (`boat-platform/src/plugins/tcp/`)."
        )
        sys.exit(1)

    if not iface:
        iface = _pick_iface(client, bus_type)

    frame = frame_pb2.Frame()
    frame.bus_type = bt
    frame.iface = iface
    frame.payload = payload

    if bt in (frame_pb2.Frame.CAN, frame_pb2.Frame.CANFD):
        frame.can.can_id = can_id_int
        frame.can.dlc = len(payload)
        frame.can.flags = 0x04 if bt == frame_pb2.Frame.CANFD else 0  # FDF flag for FD frames
    elif bt == frame_pb2.Frame.ETHERNET:
        frame.eth.dst_mac = _parse_hex(dst_mac) if dst_mac else b"\x00" * 6
        frame.eth.src_mac = _parse_hex(src_mac) if src_mac else b"\x00" * 6
        frame.eth.ethertype = ethertype_int
        if dst_ip:
            parts = dst_ip.split(".")
            frame.eth.dst_ip = bytes(int(p) for p in parts) if len(parts) == 4 else b""
            frame.eth.ip_version = 4
    elif bt == frame_pb2.Frame.TCP:
        if dst_ip:
            parts = dst_ip.split(".")
            ip_bytes = bytes(int(p) for p in parts)
            if len(parts) == 4:
                frame.tcp.dst_ip = ip_bytes
        if src_ip:
            parts = src_ip.split(".")
            ip_bytes = bytes(int(p) for p in parts)
            if len(parts) == 4:
                frame.tcp.src_ip = ip_bytes
        frame.tcp.dst_port = dst_port_int
        frame.tcp.src_port = src_port_int
        frame.tcp.ip_version = 4
    elif bt == frame_pb2.Frame.PDU:
        frame.pdu.pdu_id = pdu_id_int

    try:
        req = frame_pb2.SendFrameRequest(frame=frame)
        resp = client.frame.SendFrame(req)
        if resp.accepted:
            typer.echo(f"Frame sent: bus_type={bus_type} iface={iface or 'auto'}")
        else:
            print_error("Frame not accepted (unrecognized bus type or no handler)")
            sys.exit(1)
    except grpc.RpcError as e:
        print_error(f"RPC error [{e.code().name}]: {e.details()}")
        sys.exit(1)


@frame_app.command("subscribe")
def subscribe_frames(
    ctx: typer.Context,
    bus_types: str = typer.Option(
        "", "--bus-types", help="Comma-separated: CAN,ETHERNET,TCP,PDU (empty = all)"),
    iface: str = typer.Option("", "--iface", "-i", help="Interface filter"),
) -> None:
    """Stream unified Frames from the gateway."""
    client = ctx.obj["client"]

    req = frame_pb2.SubscribeFramesRequest()
    if iface:
        req.iface_filter = iface

    if bus_types:
        for bt_name in bus_types.split(","):
            bt_name = bt_name.strip().upper()
            bt_map = {
                "CAN": frame_pb2.Frame.CAN,
                "CANFD": frame_pb2.Frame.CANFD,
                "ETHERNET": frame_pb2.Frame.ETHERNET,
                "TCP": frame_pb2.Frame.TCP,
                "PDU": frame_pb2.Frame.PDU,
            }
            if bt_name in bt_map:
                req.bus_types.append(bt_map[bt_name])

    typer.echo(f"Subscribing to frames (bus_types={bus_types or 'all'})...")

    try:
        for frame in client.frame.SubscribeFrames(req):
            bt_name = frame_pb2.Frame.BusType.Name(frame.bus_type)
            iface_str = frame.iface or "-"
            payload_hex = frame.payload.hex() if frame.payload else "(empty)"
            extra = ""
            if frame.HasField("can"):
                extra = f" can_id=0x{frame.can.can_id:03X} dlc={frame.can.dlc}"
            elif frame.HasField("eth"):
                extra = f" eth={frame.eth.ethertype:#06x}"
            elif frame.HasField("tcp"):
                extra = f" tcp={frame.tcp.src_port}->{frame.tcp.dst_port}"
            elif frame.HasField("pdu"):
                extra = f" pdu_id=0x{frame.pdu.pdu_id:03X}"
            extra += f" [{len(frame.payload)}B]"
            typer.echo(f"[{bt_name}] {iface_str} {extra}  {payload_hex}")
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.CANCELLED:
            return
        print_error(f"RPC error [{e.code().name}]: {e.details()}")
        sys.exit(1)


@frame_app.command("list-ifaces")
def list_ifaces(ctx: typer.Context) -> None:
    """List all interfaces the gateway has access to (CAN + Ethernet)."""
    client = ctx.obj["client"]

    try:
        from boat.v1 import ethernet_pb2

        can_resp = client.can.ListBuses(can_pb2.ListBusesRequest())
        eth_resp = client.ethernet.ListInterfaces(
            ethernet_pb2.ListEthernetInterfacesRequest())

        from .output import print_table

        rows = []
        for bus in can_resp.buses:
            rows.append((
                bus.iface,
                "CAN",
                bus.driver,
                bus.state,
                "yes" if bus.fd_support else "no",
            ))
        for name in eth_resp.ifaces:
            rows.append((name, "ETHERNET", "", "", ""))

        print_table(
            ["iface", "type", "driver", "state", "fd"],
            rows,
            ctx.obj["json_mode"],
        )
    except grpc.RpcError as e:
        print_error(f"RPC error [{e.code().name}]: {e.details()}")
        sys.exit(1)
