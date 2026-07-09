"""One-shot CLI for BoAt — non-interactive subcommand mode.

Usage
-----
python3 -m boat [--db <path>] [--gateway <addr>] <command> [options]

Commands
--------
can send   --msg <MessageName> [--bus <iface>] [--id <hex|int>]
           [--data <hex>]          raw hex payload (bypasses signal packing)
           [--sig Name=val ...]    set individual signal physical values

pdu send   --msg <MessageName>
           [--sig Name=val ...]

eth send   --msg <MessageName> [--iface <name>]
           [--data <hex>]
           [--src-mac <xx:xx:xx:xx:xx:xx>] [--dst-mac <xx:xx:xx:xx:xx:xx>]

db  list                           list all messages in the database
db  show   --msg <MessageName>     print message definition

Examples
--------
  python3 -m boat can send --msg Motor_1 --sig MotorSpeed=100 --sig Clamp15=1
  python3 -m boat can send --msg Motor_1 --data DEADBEEF --bus vcan0
  python3 -m boat pdu send --msg MotorSpeed_PDU --sig MotorSpeed=250.5
  python3 -m boat db list
  python3 -m boat db show --msg Motor_1
"""

from __future__ import annotations

import argparse
import sys
from typing import List


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_sigs(sig_list: List[str] | None) -> dict[str, float]:
    """Parse ['Name=val', ...] into {name: float}."""
    result = {}
    for item in (sig_list or []):
        if "=" not in item:
            raise ValueError(f"--sig must be Name=value, got '{item}'")
        name, _, val = item.partition("=")
        result[name.strip()] = float(val.strip())
    return result


def _parse_hex_or_int(s: str) -> int:
    return int(s, 16) if s.startswith("0x") or s.startswith("0X") else int(s, 0)


def _mac_str_to_bytes(s: str) -> bytes:
    return bytes(int(b, 16) for b in s.split(":"))


# ── sub-handlers ──────────────────────────────────────────────────────────────

def _handle_can_send(args, db, client) -> int:
    from boat.message import Message
    from boat.v1 import can_pb2

    entry = db.by_name(args.msg)
    if entry is None:
        print(f"Error: message '{args.msg}' not found in database.")
        return 1

    msg = Message(entry)

    # Apply --sig overrides
    for name, value in _parse_sigs(args.sig).items():
        try:
            msg.set(name, value)
        except KeyError as e:
            print(f"Error: {e}")
            return 1

    # Raw payload: --data wins over signal packing
    if args.data:
        try:
            payload = bytes.fromhex(args.data.replace(" ", ""))
        except ValueError:
            print(f"Error: --data '{args.data}' is not valid hex.")
            return 1
    else:
        payload = msg.pack()

    iface  = args.bus or entry["Bus"]
    can_id = args.id  if args.id is not None else entry["Identifier"]
    flags  = 0x04 if entry["BusType"] == "CANFD" else 0

    frame = can_pb2.CanFrame(
        can_id=can_id,
        dlc=len(payload),
        data=payload,
        iface=iface,
        flags=flags,
    )
    resp = client.can.SendCanFrame(can_pb2.SendCanFrameRequest(frame=frame))
    if resp.accepted:
        print(f"OK  can_id=0x{can_id:X}  iface={iface}  data={payload.hex().upper()}")
        return 0
    print("Gateway rejected frame (interface not registered or no route).")
    return 1


def _handle_pdu_send(args, db, client) -> int:
    from boat.message import Message
    from boat.v1 import pdu_pb2

    entry = db.by_name(args.msg)
    if entry is None:
        print(f"Error: message '{args.msg}' not found.")
        return 1
    if entry["BusType"] != "ETH_PDU":
        print(f"Error: '{args.msg}' has BusType={entry['BusType']}, expected ETH_PDU.")
        return 1

    msg = Message(entry)
    for name, value in _parse_sigs(args.sig).items():
        try:
            msg.set(name, value)
        except KeyError as e:
            print(f"Error: {e}")
            return 1

    payload = msg.pack()
    pdu     = pdu_pb2.PduFrame(pdu_id=entry["PduId"], payload=payload)
    resp    = client.pdu.SendPdu(pdu_pb2.SendPduRequest(pdu=pdu))
    if resp.accepted:
        print(f"OK  pdu_id=0x{entry['PduId']:08X}  payload={payload.hex().upper()}")
        return 0
    print("Gateway rejected PDU (no route configured for this PduId).")
    return 1


def _handle_eth_send(args, db, client) -> int:
    from boat.message import Message
    from boat.v1 import ethernet_pb2

    entry = db.by_name(args.msg)
    if entry is None:
        print(f"Error: message '{args.msg}' not found.")
        return 1
    if entry["BusType"] != "ETH":
        print(f"Error: '{args.msg}' has BusType={entry['BusType']}, expected ETH.")
        return 1

    if args.data:
        try:
            payload = bytes.fromhex(args.data.replace(" ", ""))
        except ValueError:
            print(f"Error: --data '{args.data}' is not valid hex.")
            return 1
    else:
        payload = b""

    iface   = args.iface or entry["Bus"]
    src_mac = _mac_str_to_bytes(args.src_mac) if args.src_mac else bytes(6)
    dst_mac = _mac_str_to_bytes(args.dst_mac) if args.dst_mac else bytes(6)

    frame = ethernet_pb2.EthernetFrame(
        iface=iface,
        src_mac=src_mac,
        dst_mac=dst_mac,
        ethertype=entry.get("EtherType", 0x88B5),
        payload=payload,
    )
    resp = client.ethernet.SendFrame(
        ethernet_pb2.SendEthernetFrameRequest(frame=frame)
    )
    if resp.accepted:
        print(f"OK  iface={iface}  ethertype=0x{entry.get('EtherType',0):04X}  "
              f"len={len(payload)}")
        return 0
    print("Gateway rejected frame.")
    return 1


def _handle_db_list(args, db) -> int:
    print(f"{'DbId':>6}  {'BusType':<10}  {'MessageName'}")
    print("-" * 50)
    for name in sorted(db.names()):
        e = db.by_name(name)
        print(f"{e['DbId']:>6}  {e['BusType']:<10}  {name}")
    return 0


def _handle_db_show(args, db) -> int:
    from boat.message import Message

    entry = db.by_name(args.msg)
    if entry is None:
        print(f"Error: message '{args.msg}' not found.")
        return 1
    msg = Message(entry)
    print(repr(msg))
    print(f"\n  Bus       : {entry['Bus']}")
    print(f"  SendType  : {entry['SendType']}  "
          f"CycleTime={entry['CycleTime']}ms  "
          f"FastCycle={entry['CycleTimeFast']}ms")
    if entry["BusType"] in ("CAN", "CANFD"):
        ft = "Extended" if entry.get("FrameType") else "Standard"
        print(f"  Identifier: 0x{entry['Identifier']:X}  FrameType={ft}")
    if entry["BusType"] == "ETH_PDU":
        print(f"  PduId     : 0x{entry['PduId']:08X}  Container DbId={entry['ContainerDbId']}")
    if entry["BusType"] == "ETH":
        print(f"  IP        : {entry.get('SrcIP','')} → {entry.get('DstIP','')}"
              f"  Ports: {entry.get('SrcPort',0)}→{entry.get('DstPort',0)}")
        print(f"  IpduM PDUs: {entry.get('IpduMEntries', [])}")
    return 0


# ── argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="boat",
        description="BoAt one-shot message CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    root.add_argument("--db",      default=None,              metavar="PATH",
                      help="PDU database JSON (default: pdu_db.json in cwd)")
    root.add_argument("--gateway", default="localhost:50051", metavar="HOST:PORT",
                      help="Gateway gRPC address")

    sub = root.add_subparsers(dest="domain", metavar="<command>")
    sub.required = True

    # ── can ──────────────────────────────────────────────────────────────
    can_p  = sub.add_parser("can",  help="CAN/CANFD commands")
    can_s  = can_p.add_subparsers(dest="action", metavar="<action>")
    can_s.required = True

    can_send = can_s.add_parser("send", help="Send a CAN frame")
    can_send.add_argument("--msg",  required=True, metavar="NAME",
                          help="Message name from the database")
    can_send.add_argument("--bus",  default=None,  metavar="IFACE",
                          help="Override CAN interface (e.g. vcan0)")
    can_send.add_argument("--id",   default=None,  type=_parse_hex_or_int,
                          metavar="ID",
                          help="Override CAN identifier (hex or decimal)")
    can_send.add_argument("--data", default=None,  metavar="HEX",
                          help="Raw payload hex string — bypasses signal packing")
    can_send.add_argument("--sig",  action="append", metavar="Name=value",
                          help="Set a signal physical value (repeatable)")

    # ── pdu ──────────────────────────────────────────────────────────────
    pdu_p  = sub.add_parser("pdu",  help="ETH_PDU commands")
    pdu_s  = pdu_p.add_subparsers(dest="action", metavar="<action>")
    pdu_s.required = True

    pdu_send = pdu_s.add_parser("send", help="Send an ETH_PDU via the gateway")
    pdu_send.add_argument("--msg",  required=True, metavar="NAME")
    pdu_send.add_argument("--sig",  action="append", metavar="Name=value",
                          help="Set a signal physical value (repeatable)")

    # ── eth ──────────────────────────────────────────────────────────────
    eth_p  = sub.add_parser("eth",  help="Raw Ethernet frame commands")
    eth_s  = eth_p.add_subparsers(dest="action", metavar="<action>")
    eth_s.required = True

    eth_send = eth_s.add_parser("send", help="Send a raw Ethernet frame")
    eth_send.add_argument("--msg",     required=True, metavar="NAME")
    eth_send.add_argument("--iface",   default=None)
    eth_send.add_argument("--data",    default=None,  metavar="HEX")
    eth_send.add_argument("--src-mac", default=None,  metavar="MAC")
    eth_send.add_argument("--dst-mac", default=None,  metavar="MAC")

    # ── db ───────────────────────────────────────────────────────────────
    db_p = sub.add_parser("db", help="Database inspection")
    db_s = db_p.add_subparsers(dest="action", metavar="<action>")
    db_s.required = True

    db_s.add_parser("list", help="List all messages")

    db_show = db_s.add_parser("show", help="Show a message definition")
    db_show.add_argument("--msg", required=True, metavar="NAME")

    return root


# ── entry point ───────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    import os
    from boat.pdu_db import PduDatabase

    parser = build_parser()
    args   = parser.parse_args(argv)

    # Locate DB file
    db_path = args.db or "pdu_db.json"
    if not os.path.exists(db_path):
        # fall back to script directory
        here = os.path.dirname(__file__)
        db_path = os.path.join(here, "..", "..", "config", "pdu_db_example.json")
    try:
        db = PduDatabase(db_path)
    except FileNotFoundError:
        print(f"Error: database not found at '{db_path}'. Use --db <path>.")
        return 1

    # DB-only commands don't need the gateway
    if args.domain == "db":
        if args.action == "list":
            return _handle_db_list(args, db)
        if args.action == "show":
            return _handle_db_show(args, db)

    # All other commands need a gRPC connection
    import grpc
    from boat.client import BoAtClient
    try:
        client = BoAtClient(args.gateway)
        if args.domain == "can":
            return _handle_can_send(args, db, client)
        if args.domain == "pdu":
            return _handle_pdu_send(args, db, client)
        if args.domain == "eth":
            return _handle_eth_send(args, db, client)
    except grpc.RpcError as e:
        print(f"gRPC error: {e.details()}")
        return 1
    finally:
        try:
            client.close()
        except Exception:
            pass

    return 0
