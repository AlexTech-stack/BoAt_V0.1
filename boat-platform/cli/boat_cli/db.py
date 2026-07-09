from __future__ import annotations

import os
import sys
from typing import Annotated, Optional

import typer

from .completions import complete_json_file, complete_msg_name
from .output import print_error, print_table

db_app = typer.Typer(help="PDU database inspection commands.")

_DEFAULT_DB = "pdu_db.json"


def _open_db(db_path: str):
    from boat.pdu_db import PduDatabase
    if not os.path.exists(db_path):
        print_error(f"Database not found: '{db_path}'. Use --db to specify the path.")
        sys.exit(1)
    return PduDatabase(db_path)


@db_app.command("list")
def db_list(
    db: Annotated[str, typer.Option("--db", help="PDU database JSON file.", autocompletion=complete_json_file)] = _DEFAULT_DB,
) -> None:
    """List all messages defined in the PDU database."""
    database = _open_db(db)
    rows = []
    for name in sorted(database.names()):
        e = database.by_name(name)
        rows.append([str(e["DbId"]), e["BusType"], e["Bus"], name,
                     str(e["signalcount"]), e["SendType"], str(e["CycleTime"])])
    print_table(["DbId", "BusType", "Bus", "MessageName", "Signals", "SendType", "CycleTime(ms)"],
                rows, False)


@db_app.command("show")
def db_show(
    msg_name: Annotated[str,  typer.Option("--msg", help="Message name.", autocompletion=complete_msg_name)],
    db:       Annotated[str,  typer.Option("--db",  help="PDU database JSON file.", autocompletion=complete_json_file)] = _DEFAULT_DB,
    pack:     Annotated[bool, typer.Option("--pack", help="Show packed InitValue bytes.")] = False,
) -> None:
    """Show the full definition of a message including all signals."""
    from boat.message import Message

    database = _open_db(db)
    entry = database.by_name(msg_name)
    if entry is None:
        print_error(f"Message '{msg_name}' not found.")
        sys.exit(1)

    # ── header ────────────────────────────────────────────────────────
    extra_header = []
    if entry.get("Comment"):
        extra_header.append(["Comment", entry["Comment"]])
    if entry.get("Node"):
        extra_header.append(["Node", entry["Node"]])
    print_table(
        ["Field", "Value"],
        [
            ["DbId",        str(entry["DbId"])],
            ["MessageName", entry["MessageName"]],
            ["Bus",         entry["Bus"]],
            ["BusType",     entry["BusType"]],
            ["Direction",   ["Source", "Routed"][entry["Direction"]]],
            ["RoutingType", ["Source", "MessageRouting", "SignalRouting"][entry["RoutingType"]]],
            ["SendType",    entry["SendType"]],
            ["CycleTime",   f"{entry['CycleTime']} ms"],
            ["CycleTimeFast", f"{entry['CycleTimeFast']} ms"],
            ["isE2E",       str(entry["isE2E"])],
        ] + extra_header,
        False,
    )

    # ── bus-type-specific fields ──────────────────────────────────────
    bt = entry["BusType"]
    if bt in ("CAN", "CANFD"):
        ft = "Extended (29-bit)" if entry.get("FrameType") else "Standard (11-bit)"
        print_table(
            ["Field", "Value"],
            [
                ["Identifier", f"0x{entry['Identifier']:X}"],
                ["FrameType",  ft],
                ["Length",     f"{entry['Length']} bytes"],
                ["BRS",        str(entry.get("BRS", False))],
            ],
            False,
        )
    elif bt == "ETH":
        print_table(
            ["Field", "Value"],
            [
                ["EtherType",    f"0x{entry.get('EtherType',0):04X}"],
                ["SrcIP→DstIP",  f"{entry.get('SrcIP','')} → {entry.get('DstIP','')}"],
                ["Ports",        f"{entry.get('SrcPort',0)} → {entry.get('DstPort',0)}"],
                ["TTL",          str(entry.get("TTL", 64))],
                ["IpduM PDUs",   str(entry.get("IpduMEntries", []))],
            ],
            False,
        )
    elif bt == "ETH_PDU":
        print_table(
            ["Field", "Value"],
            [
                ["PduId",       f"0x{entry['PduId']:08X}"],
                ["ContainerDbId", str(entry["ContainerDbId"])],
                ["Length",      f"{entry['Length']} bytes"],
            ],
            False,
        )

    # ── signals ───────────────────────────────────────────────────────
    if entry["signals"]:
        sig_rows = []
        for s in entry["signals"]:
            bo = "Intel" if s["ByteOrder"] == 0 else "Motorola"
            enum_str = ", ".join(f"{k}={v}" for k, v in (s["EnumValues"] or {}).items())
            mux_str = "MUX" if s.get("IsMuxor") else (str(s["MuxValue"]) if s.get("MuxValue") is not None else "-")
            comment_str = (s.get("Comment") or "")[:40]
            sig_rows.append([
                str(s["id"]),
                s["SignalName"],
                f"{s['Length']} bit",
                f"@{s['StartPos']}",
                bo,
                s["ValueType"],
                str(s["InitValue"]),
                f"×{s['Factor']} +{s['Offset']}",
                f"{s['Min']}…{s['Max']} {s['Unit']}".strip(),
                enum_str or "-",
                mux_str,
                comment_str,
            ])
        print_table(
            ["id", "SignalName", "Length", "StartPos", "ByteOrder",
             "ValueType", "InitVal", "Scale", "Range", "EnumValues",
             "Mux", "Comment"],
            sig_rows,
            False,
        )

    # ── packed init bytes ─────────────────────────────────────────────
    if pack and entry["signals"]:
        raw = Message(entry).pack()
        typer.echo(f"\nPacked InitValues ({len(raw)} bytes): {raw.hex().upper()}")


@db_app.command("signal-routes")
def db_signal_routes(
    db: Annotated[str, typer.Option("--db", help="PDU database JSON file.", autocompletion=complete_json_file)] = _DEFAULT_DB,
) -> None:
    """List all signal routing rules defined in the database."""
    database = _open_db(db)
    rows = []
    for r in database.signal_routes():
        rows.append([
            f"{r['SrcDbId']}.{r['SrcSignalId']}",
            f"{r['DstDbId']}.{r['DstSignalId']}",
        ])
    if not rows:
        typer.echo("No signal routes defined.")
        return
    print_table(["Source (DbId.SigId)", "Destination (DbId.SigId)"], rows, False)
