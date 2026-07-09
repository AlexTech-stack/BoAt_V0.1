from __future__ import annotations

import sys
from typing import Annotated, List, Optional

import grpc
import typer

from boat.v1 import pdu_pb2

from .completions import (
    complete_iface,
    complete_json_file,
    complete_pdu_msg_name,
    complete_transport,
)
from .output import print_error, print_table

pdu_app = typer.Typer(help="PDU routing, transmission and group commands.")


def _rpc_error(ex: grpc.RpcError) -> None:
    print_error(f"RPC error [{ex.code().name}]: {ex.details()}")
    sys.exit(1)


def _load_msg(db_path: str, msg_name: str):
    import os
    from boat.pdu_db import PduDatabase
    if not os.path.exists(db_path):
        print_error(f"Database not found: '{db_path}'. Use --db to specify the path.")
        sys.exit(1)
    db = PduDatabase(db_path)
    entry = db.by_name(msg_name)
    if entry is None:
        print_error(f"Message '{msg_name}' not found in '{db_path}'.")
        sys.exit(1)
    return entry


def _apply_sigs(msg, sig_list: list[str]) -> None:
    for item in sig_list:
        if "=" not in item:
            print_error(f"--sig must be Name=value, got '{item}'")
            sys.exit(1)
        name, _, val = item.partition("=")
        try:
            msg.set(name.strip(), float(val.strip()))
        except KeyError as e:
            print_error(str(e))
            sys.exit(1)


def _send_type_from_str(s: str) -> int:
    m = {
        "none": pdu_pb2.SEND_TYPE_NONE,
        "cyclic": pdu_pb2.SEND_TYPE_CYCLIC,
        "onchange": pdu_pb2.SEND_TYPE_ON_CHANGE,
        "mixed": pdu_pb2.SEND_TYPE_MIXED,
    }
    val = m.get(s.lower())
    if val is None:
        print_error(f"Unknown send type '{s}'. Choose: none, cyclic, onchange, mixed.")
        sys.exit(1)
    return val


def _send_type_to_str(st: int) -> str:
    m = {
        pdu_pb2.SEND_TYPE_NONE: "none",
        pdu_pb2.SEND_TYPE_CYCLIC: "cyclic",
        pdu_pb2.SEND_TYPE_ON_CHANGE: "onchange",
        pdu_pb2.SEND_TYPE_MIXED: "mixed",
    }
    return m.get(st, "?")


# ── PDU send ────────────────────────────────────────────────────────────────────

@pdu_app.command("send")
def send_pdu(
    ctx: typer.Context,
    msg_name: Annotated[str, typer.Option("--msg", help="Message name from the PDU database.", autocompletion=complete_pdu_msg_name)] = "",
    pdu_id: Annotated[str, typer.Option("--id", help="32-bit PDU ID (hex or decimal). Overrides database value.")] = "",
    data: Annotated[str, typer.Option("--data", help="Raw hex payload. Overrides signal packing.")] = "",
    sig: Annotated[Optional[List[str]], typer.Option("--sig", help="Set signal physical value: Name=value (repeatable).")] = None,
    db: Annotated[str, typer.Option("--db", help="PDU database JSON file.", autocompletion=complete_json_file)] = "pdu_db.json",
) -> None:
    """Send a PDU via the gateway."""
    if not msg_name and not pdu_id:
        print_error("Provide --msg (database lookup) or --id with --data.")
        sys.exit(1)

    if msg_name:
        from boat.message import Message
        entry  = _load_msg(db, msg_name)
        if entry["BusType"] != "ETH_PDU":
            print_error(f"'{msg_name}' has BusType={entry['BusType']}, expected ETH_PDU.")
            sys.exit(1)
        msg_obj  = Message(entry)
        _apply_sigs(msg_obj, sig or [])
        payload  = bytes.fromhex(data.replace(":", "").replace(" ", "")) if data else msg_obj.pack()
        resolved_id = int(pdu_id, 0) if pdu_id else entry["PduId"]
    else:
        if not data:
            print_error("--data is required when --msg is not used.")
            sys.exit(1)
        payload      = bytes.fromhex(data.replace(":", "").replace(" ", ""))
        resolved_id  = int(pdu_id, 0)

    frame = pdu_pb2.PduFrame(pdu_id=resolved_id, payload=payload)
    try:
        resp = ctx.obj["client"].pdu.SendPdu(pdu_pb2.SendPduRequest(pdu=frame))
        print_table(["pdu_id", "payload", "accepted"],
                    [[f"0x{resolved_id:08X}", payload.hex().upper(), resp.accepted]],
                    ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


# ── Route management ────────────────────────────────────────────────────────────

@pdu_app.command("route")
def configure_route(
    ctx: typer.Context,
    pdu_id:   Annotated[str, typer.Option("--id",        help="32-bit PDU ID (hex or decimal).")],
    iface:    Annotated[str, typer.Option("--iface",     help="Network interface, e.g. vcan0 or veth0.", autocompletion=complete_iface)],
    transport:Annotated[str, typer.Option("--transport", help="Transport: can or eth.", autocompletion=complete_transport)],
    can_id:   Annotated[str, typer.Option("--can-id",    help="CAN frame ID override (default: same as pdu_id).")] = "0",
    ethertype:Annotated[str, typer.Option("--ethertype", help="EtherType (default: 0x88B5 sim-only).")] = "0x88B5",
    src_ip:   Annotated[str, typer.Option("--src-ip",    help="Source IP (IPv4 dotted or IPv6). Enables UDP/IP mode.")] = "",
    dst_ip:   Annotated[str, typer.Option("--dst-ip",    help="Destination IP.")] = "",
    src_port: Annotated[int, typer.Option("--src-port")] = 0,
    dst_port: Annotated[int, typer.Option("--dst-port")] = 0,
    ttl:      Annotated[int, typer.Option("--ttl")] = 64,
    vlan_id:  Annotated[int, typer.Option("--vlan")] = 0,
    send_type:Annotated[str, typer.Option("--send-type", help="Transmission schedule: none, cyclic, onchange, mixed.")] = "none",
    cycle_ms: Annotated[int, typer.Option("--cycle-ms",  help="Base cycle in ms for cyclic/mixed modes.")] = 0,
    fast_ms:  Annotated[int, typer.Option("--fast-ms",   help="Fast period in ms for n-times repetitions.")] = 0,
    reps:     Annotated[int, typer.Option("--reps",      help="Number of fast repetitions per change event.")] = 0,
) -> None:
    """Configure a PDU routing rule on the gateway.

    Use --send-type none to remove an existing transmission schedule
    without changing the route.  Use 'boat pdu remove-route --id X' to
    delete the route entirely.
    """
    import socket

    resolved_id = int(pdu_id, 0)
    resolved_can_id = int(can_id, 0)

    transport_map = {"can": pdu_pb2.PDU_TRANSPORT_CAN, "eth": pdu_pb2.PDU_TRANSPORT_ETHERNET}
    t = transport_map.get(transport.lower())
    if t is None:
        print_error(f"--transport must be 'can' or 'eth', got '{transport}'")
        sys.exit(1)

    def _ip_to_bytes(addr: str) -> bytes:
        if not addr:
            return b""
        try:
            return socket.inet_pton(socket.AF_INET, addr)
        except OSError:
            return socket.inet_pton(socket.AF_INET6, addr)

    schedule = pdu_pb2.PduSchedule(
        send_type=_send_type_from_str(send_type),
        cycle_ms=cycle_ms,
        fast_ms=fast_ms,
        repetitions=reps,
    )
    route = pdu_pb2.PduRoute(
        pdu_id=resolved_id,
        transport=t,
        iface=iface,
        can_id=resolved_can_id,
        ethertype=int(ethertype, 0),
        vlan_id=vlan_id,
        src_ip=_ip_to_bytes(src_ip),
        dst_ip=_ip_to_bytes(dst_ip),
        src_port=src_port,
        dst_port=dst_port,
        ttl=ttl,
        schedule=schedule,
    )
    try:
        resp = ctx.obj["client"].pdu.ConfigureRoute(pdu_pb2.ConfigureRouteRequest(route=route))
        print_table(["pdu_id", "iface", "transport", "schedule", "ok"],
                    [[f"0x{resolved_id:08X}", iface, transport.upper(),
                      f"{send_type}({cycle_ms}ms/{fast_ms}ms/{reps}x)" if send_type != "none" else "-",
                      resp.ok]],
                    ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@pdu_app.command("remove-route")
def remove_route(
    ctx: typer.Context,
    pdu_id: Annotated[str, typer.Option("--id", help="32-bit PDU ID (hex or decimal) to remove.")],
) -> None:
    """Remove a PDU routing rule and its transmission schedule."""
    resolved_id = int(pdu_id, 0)
    try:
        resp = ctx.obj["client"].pdu.RemoveRoute(
            pdu_pb2.RemoveRouteRequest(pdu_id=resolved_id)
        )
        print_table(["pdu_id", "ok"],
                    [[f"0x{resolved_id:08X}", resp.ok]],
                    ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@pdu_app.command("list-routes")
def list_routes(ctx: typer.Context) -> None:
    """List all configured PDU routing rules."""
    try:
        resp = ctx.obj["client"].pdu.ListRoutes(pdu_pb2.ListRoutesRequest())
        rows = []
        for r in resp.routes:
            t = {pdu_pb2.PDU_TRANSPORT_CAN: "CAN",
                 pdu_pb2.PDU_TRANSPORT_ETHERNET: "ETH"}.get(r.transport, "?")
            sched = "none"
            if r.HasField("schedule") and r.schedule.send_type != pdu_pb2.SEND_TYPE_NONE:
                st = _send_type_to_str(r.schedule.send_type)
                if r.schedule.send_type == pdu_pb2.SEND_TYPE_CYCLIC:
                    sched = f"cyclic@{r.schedule.cycle_ms}ms"
                elif r.schedule.send_type == pdu_pb2.SEND_TYPE_ON_CHANGE:
                    sched = f"onchange({r.schedule.repetitions}x@{r.schedule.fast_ms}ms)"
                elif r.schedule.send_type == pdu_pb2.SEND_TYPE_MIXED:
                    sched = f"mixed@{r.schedule.cycle_ms}ms+onchange({r.schedule.repetitions}x@{r.schedule.fast_ms}ms)"
            rows.append([f"0x{r.pdu_id:08X}", t, r.iface,
                         f"0x{r.can_id:X}" if r.can_id else "-",
                         f"0x{r.ethertype:04X}", sched])
        print_table(["pdu_id", "transport", "iface", "can_id", "ethertype", "schedule"],
                    rows, ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


# ── Container management ────────────────────────────────────────────────────────

@pdu_app.command("container")
def configure_container(
    ctx: typer.Context,
    msg_name: Annotated[str,  typer.Option("--msg",      help="ETH container message name from the PDU database.", autocompletion=complete_pdu_msg_name)] = "",
    cid:      Annotated[str,  typer.Option("--id",       help="Container ID (hex or decimal). Overrides database value.")] = "",
    iface:    Annotated[str,  typer.Option("--iface",    help="Ethernet interface. Overrides database value.", autocompletion=complete_iface)] = "",
    src_ip:   Annotated[str,  typer.Option("--src-ip",   help="Source IP. Overrides database value.")] = "",
    dst_ip:   Annotated[str,  typer.Option("--dst-ip",   help="Destination IP. Overrides database value.")] = "",
    src_port: Annotated[int,  typer.Option("--src-port")] = 0,
    dst_port: Annotated[int,  typer.Option("--dst-port")] = 0,
    ttl:      Annotated[int,  typer.Option("--ttl")]      = 0,
    vlan_id:  Annotated[int,  typer.Option("--vlan")]     = 0,
    db:       Annotated[str,  typer.Option("--db",       help="PDU database JSON file.", autocompletion=complete_json_file)] = "pdu_db.json",
) -> None:
    """Register an IpduM container on the gateway."""
    import os, socket
    from boat.v1 import pdu_pb2

    def ip_to_bytes(addr: str) -> bytes:
        try:
            return socket.inet_pton(socket.AF_INET, addr)
        except OSError:
            return socket.inet_pton(socket.AF_INET6, addr)

    if msg_name:
        entry = _load_msg(db, msg_name)
        if entry["BusType"] != "ETH":
            print_error(f"'{msg_name}' has BusType={entry['BusType']}, expected ETH.")
            sys.exit(1)
        from boat.pdu_db import PduDatabase
        database = PduDatabase(db)
        pdu_ids = []
        for member_id in entry.get("IpduMEntries", []):
            member = database.by_id(member_id)
            if member is None:
                print_error(f"IpduMEntries references DbId={member_id} which is not in the database.")
                sys.exit(1)
            pdu_ids.append(member["PduId"])

        resolved_cid      = int(cid, 0)  if cid      else entry["DbId"]
        resolved_iface    = iface        if iface    else entry.get("Bus", "")
        resolved_src_ip   = ip_to_bytes(src_ip) if src_ip else ip_to_bytes(entry.get("SrcIP", ""))
        resolved_dst_ip   = ip_to_bytes(dst_ip) if dst_ip else ip_to_bytes(entry.get("DstIP", ""))
        resolved_src_port = src_port or entry.get("SrcPort", 0)
        resolved_dst_port = dst_port or entry.get("DstPort", 0)
        resolved_ttl      = ttl      or entry.get("TTL", 64)
        resolved_vlan     = vlan_id  or entry.get("VlanId", 0)
    else:
        if not cid or not dst_ip or not src_ip:
            print_error("Provide --msg (database mode) or --id, --src-ip, --dst-ip (manual mode).")
            sys.exit(1)
        resolved_cid      = int(cid, 0)
        resolved_iface    = iface
        resolved_src_ip   = ip_to_bytes(src_ip)
        resolved_dst_ip   = ip_to_bytes(dst_ip)
        resolved_src_port = src_port
        resolved_dst_port = dst_port
        resolved_ttl      = ttl or 64
        resolved_vlan     = vlan_id
        pdu_ids           = []

    container = pdu_pb2.PduContainerDef(
        container_id=resolved_cid,
        iface=resolved_iface,
        src_ip=resolved_src_ip,
        dst_ip=resolved_dst_ip,
        src_port=resolved_src_port,
        dst_port=resolved_dst_port,
        ttl=resolved_ttl,
        vlan_id=resolved_vlan,
        pdu_ids=pdu_ids,
    )
    try:
        resp = ctx.obj["client"].pdu.ConfigureContainer(
            pdu_pb2.ConfigureContainerRequest(container=container)
        )
        print_table(
            ["container_id", "iface", "pdu_ids", "ok"],
            [[str(resolved_cid), resolved_iface,
              str([f"0x{p:08X}" for p in pdu_ids]), resp.ok]],
            ctx.obj["json_mode"],
        )
    except grpc.RpcError as ex:
        _rpc_error(ex)


# ── Group management ────────────────────────────────────────────────────────────

@pdu_app.command("group")
def configure_group(
    ctx: typer.Context,
    group_id: Annotated[str,  typer.Option("--id",     help="Group ID (hex or decimal).")],
    name:     Annotated[str,  typer.Option("--name",   help="Human-readable group name.")] = "",
    pdu_ids:  Annotated[List[str], typer.Option("--pdu", help="PDU ID to add to group (repeatable).")] = [],
    enabled:  Annotated[bool,  typer.Option("--enabled/--disabled", help="Start enabled?")] = True,
) -> None:
    """Create or update an I-PDU group.

    PDUs in a disabled group are silently dropped on send and receive.
    """
    resolved_id = int(group_id, 0)
    resolved_pdu_ids = [int(p, 0) for p in pdu_ids]

    group = pdu_pb2.PduGroup(
        group_id=resolved_id,
        name=name,
        pdu_ids=resolved_pdu_ids,
        enabled=enabled,
    )
    try:
        resp = ctx.obj["client"].pdu.ConfigureGroup(
            pdu_pb2.ConfigureGroupRequest(group=group)
        )
        print_table(["group_id", "name", "pdu_ids", "enabled", "ok"],
                    [[f"0x{resolved_id:X}", name,
                      str([f"0x{p:08X}" for p in resolved_pdu_ids]),
                      str(enabled), resp.ok]],
                    ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@pdu_app.command("enable-group")
def enable_group(
    ctx: typer.Context,
    group_id: Annotated[str, typer.Option("--id", help="Group ID (hex or decimal).")],
) -> None:
    """Enable an I-PDU group, allowing its PDUs to be sent/received."""
    resolved_id = int(group_id, 0)
    try:
        resp = ctx.obj["client"].pdu.EnableGroup(
            pdu_pb2.EnableGroupRequest(group_id=resolved_id)
        )
        print_table(["group_id", "ok"], [[f"0x{resolved_id:X}", resp.ok]], ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@pdu_app.command("disable-group")
def disable_group(
    ctx: typer.Context,
    group_id: Annotated[str, typer.Option("--id", help="Group ID (hex or decimal).")],
) -> None:
    """Disable an I-PDU group, silently dropping its PDUs."""
    resolved_id = int(group_id, 0)
    try:
        resp = ctx.obj["client"].pdu.DisableGroup(
            pdu_pb2.DisableGroupRequest(group_id=resolved_id)
        )
        print_table(["group_id", "ok"], [[f"0x{resolved_id:X}", resp.ok]], ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


@pdu_app.command("list-groups")
def list_groups(ctx: typer.Context) -> None:
    """List all configured I-PDU groups."""
    try:
        resp = ctx.obj["client"].pdu.ListGroups(pdu_pb2.ListGroupsRequest())
        rows = []
        for g in resp.groups:
            pids = ", ".join(f"0x{p:08X}" for p in g.pdu_ids)
            rows.append([f"0x{g.group_id:X}", g.name, pids, "yes" if g.enabled else "no"])
        print_table(["group_id", "name", "pdu_ids", "enabled"], rows, ctx.obj["json_mode"])
    except grpc.RpcError as ex:
        _rpc_error(ex)


# ── Subscribe ───────────────────────────────────────────────────────────────────

@pdu_app.command("subscribe")
def subscribe_pdus(
    ctx: typer.Context,
    ids:   Annotated[Optional[List[str]], typer.Option("--id", help="PDU ID to subscribe (repeatable, default: all).")] = None,
    count: Annotated[int, typer.Option("--count", help="Stop after N PDUs (0 = unlimited).")] = 0,
) -> None:
    """Stream incoming PDU frames from the gateway."""
    pdu_ids = [int(i, 0) for i in (ids or [])]
    stream  = ctx.obj["client"].pdu.SubscribePdus(
        pdu_pb2.SubscribePdusRequest(pdu_ids=pdu_ids)
    )
    received = 0
    try:
        for frame in stream:
            print_table(
                ["pdu_id", "payload", "source", "iface", "timestamp_ns"],
                [[f"0x{frame.pdu_id:08X}",
                  frame.payload.hex().upper(),
                  frame.source,
                  frame.iface,
                  frame.timestamp_ns]],
                ctx.obj["json_mode"],
            )
            received += 1
            if count > 0 and received >= count:
                break
    except grpc.RpcError as ex:
        _rpc_error(ex)
    finally:
        stream.cancel()
