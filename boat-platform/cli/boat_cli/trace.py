"""boat trace — start / stop / list / replay trace recording sessions.

The start/stop/status commands communicate with the BoAt recorder daemon
(demo/recorder.py, default port 8083) rather than the gateway directly.
Start the recorder before using those commands.

The replay command reads a local .asc or .blf file and re-injects CAN
frames directly through the gateway via gRPC.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from .output import print_error, print_table

_ETHERTYPE_NAMES: dict[str, int] = {
    "ipv4": 0x0800, "ip": 0x0800,
    "arp": 0x0806,
    "ipv6": 0x86DD,
    "vlan": 0x8100,
    "boat": 0x88B5,
}

_PROTOCOL_NAMES: dict[str, int] = {
    "icmp": 1, "icmpv4": 1,
    "igmp": 2,
    "tcp": 6,
    "udp": 17,
    "ipv6": 41,
    "icmpv6": 58,
    "ospf": 89,
    "sctp": 132,
}


def _resolve_value(value: str, table: dict[str, int]) -> int:
    """Resolve a name or numeric value to an integer.

    Tries *table* lookup first, then hex (``0x...``), then decimal.
    """
    key = value.lower()
    if key in table:
        return table[key]
    if value.startswith("0x") or value.startswith("0X"):
        return int(value, 16)
    return int(value, 10)


trace_app = typer.Typer(help="Manage trace recording sessions and replay trace files.")

_DEFAULT_RECORDER = "http://localhost:8083"


def _client(recorder_url: str):
    """Return a TraceRecorder pointing at *recorder_url*."""
    try:
        sys.path.insert(0, "/home/testuser/ProjectBoat/boat-platform/sdk/python")
        from boat.trace_recorder import TraceRecorder
        return TraceRecorder(recorder_url=recorder_url)
    except ImportError as e:
        print_error(f"Cannot import boat SDK: {e}")
        raise typer.Exit(1)


def _die(msg: str) -> None:
    print_error(msg)
    raise typer.Exit(1)


# ── Commands ───────────────────────────────────────────────────────────────────

@trace_app.command("start")
def cmd_start(
    ctx:      typer.Context,
    fmt:      str  = typer.Option("asc",  "--format", "-f",
                        help="Output format: asc | blf | pcap | pcapng (pcapng recommended "
                             "for mixed CAN+Ethernet -- one file, multiple interfaces)"),
    buses:    str  = typer.Option("",     "--buses",  "-b",
                        help="Comma-separated CAN buses, e.g. vcan0,vcan1  (default: all)"),
    eth:      str  = typer.Option("",     "--eth",
                        help="Comma-separated Ethernet interfaces (pcap/pcapng only)"),
    signals:  bool = typer.Option(True,   "--signals/--no-signals",
                        help="Record BoAt bus signals to .jsonl sidecar"),
    output:   str  = typer.Option("traces", "--output", "-o",
                        help="Output directory for trace files"),
    name:     str  = typer.Option("",     "--name", "-n",
                        help="Optional session label"),
    recorder: str  = typer.Option(_DEFAULT_RECORDER, "--recorder",
                        help="Recorder daemon URL"),
) -> None:
    """Start a new recording session."""
    bus_list = [b.strip() for b in buses.split(",") if b.strip()] if buses else []
    eth_list = [e.strip() for e in eth.split(",")   if e.strip()] if eth   else []

    gateway = ctx.obj["host"] if ctx.obj else "localhost:50051"

    try:
        rec     = _client(recorder)
        rec.gateway = gateway
        session = rec.start(
            buses           = bus_list,
            eth_ifaces      = eth_list,
            include_signals = signals,
            fmt             = fmt,
            output_dir      = output,
            name            = name,
        )
    except Exception as e:
        _die(str(e))
        return

    files = ", ".join(f["name"] for f in session.get("files", []))
    print_table(
        ["session_id", "format", "buses", "signals", "files"],
        [[
            session["session_id"],
            session["format"],
            ", ".join(session["buses"]) or "all",
            str(session["include_signals"]),
            files or "(pending)",
        ]],
        ctx.obj.get("json_mode", False) if ctx.obj else False,
    )


@trace_app.command("stop")
def cmd_stop(
    ctx:        typer.Context,
    session_id: Optional[str] = typer.Argument(None,
                    help="Session ID to stop (omit to stop all running sessions)"),
    recorder:   str = typer.Option(_DEFAULT_RECORDER, "--recorder"),
) -> None:
    """Stop a recording session (or all sessions if no ID given)."""
    try:
        rec = _client(recorder)
        if session_id:
            result = rec.stop(session_id)
            rows   = [[result["session_id"], result["can_count"],
                       result["eth_count"],  result["sig_count"],
                       str(result.get("stopped_at", ""))]]
            headers = ["session_id", "can_frames", "eth_frames", "signals", "stopped_at"]
        else:
            result  = rec.stop_all()
            stopped = result.get("stopped", [])
            rows    = [[sid] for sid in stopped] or [["(none running)"]]
            headers = ["stopped_session_id"]
    except Exception as e:
        _die(str(e))
        return

    json_mode = ctx.obj.get("json_mode", False) if ctx.obj else False
    print_table(headers, rows, json_mode)


@trace_app.command("status")
def cmd_status(
    ctx:      typer.Context,
    recorder: str = typer.Option(_DEFAULT_RECORDER, "--recorder"),
) -> None:
    """Show all recording sessions (active and completed)."""
    try:
        sessions = _client(recorder).sessions()
    except Exception as e:
        _die(str(e))
        return

    if not sessions:
        typer.echo("No sessions recorded yet.")
        return

    rows = []
    for s in sessions:
        files = " ".join(f["name"] for f in s.get("files", []))
        rows.append([
            s["session_id"],
            s.get("name") or "—",
            s["format"],
            ", ".join(s["buses"]) or "all",
            "running" if s["running"] else "done",
            s["can_count"],
            s["sig_count"],
            files or "—",
        ])

    json_mode = ctx.obj.get("json_mode", False) if ctx.obj else False
    print_table(
        ["session_id", "name", "format", "buses", "status",
         "can_frames", "signals", "files"],
        rows,
        json_mode,
    )


@trace_app.command("replay")
def cmd_replay(
    ctx:     typer.Context,
    file:    Path = typer.Argument(..., help="Path to .asc or .blf CAN trace file, or a "
                                              ".pcapng file (only its CAN/CAN-FD records "
                                              "are replayed; any Ethernet records are "
                                              "skipped)"),
    buses:   str  = typer.Option("",    "--buses",  "-b",
                        help="Comma-separated CAN interfaces for channel mapping "
                             "(ch1->first, ch2->second, ...). Default: vcan0"),
    speed:   float = typer.Option(1.0,  "--speed",  "-s",
                        help="Playback speed multiplier (1.0=real-time, 0=max)"),
    loop:    Optional[int] = typer.Option(None, "--loop", "-l",
                        help="Loop the file with N ms gap between the last message of one "
                             "run and the first message of the next. Omit to replay once."),
    sim_id:  str   = typer.Option("",   "--sim-id",
                        help="Simulation ID forwarded with every frame"),
    verbose: bool  = typer.Option(False, "--verbose", "-v",
                        help="Print every frame as it is sent"),
    channel: int | None = typer.Option(None, "--channel", "-c",
                        help="Only replay frames from this CAN channel (1-based)"),
    can_id: str | None = typer.Option(None, "--id", "-i",
                        help="Only replay frames with this CAN ID (hex, e.g. 0x100). "
                             "Comma-separated for multiple IDs."),
) -> None:
    """Replay a CAN trace file (.asc, .blf, or the CAN records of a
    .pcapng) through the gateway in real time, sending each frame
    individually via gRPC.

    For Ethernet replay (.pcap, or the Ethernet records of a .pcapng), use
    `boat replay import` + `boat replay start`/`stream` instead -- this
    command supports CAN only.
    """
    try:
        sys.path.insert(0, "/home/testuser/ProjectBoat/boat-platform/sdk/python")
        from boat.trace_replay import TraceReplayer, TraceReplayError
    except ImportError as e:
        print_error(f"Cannot import boat SDK: {e}")
        raise typer.Exit(1)

    file = file.resolve()
    if not file.exists():
        print_error(f"File not found: {file}")
        raise typer.Exit(1)
    if file.suffix.lower() == ".pcap":
        print_error(
            "boat trace replay only supports CAN traces (.asc/.blf/.pcapng). "
            "For Ethernet/pcap replay, use `boat replay import` + "
            "`boat replay start`/`stream` instead."
        )
        raise typer.Exit(1)

    bus_list = [b.strip() for b in buses.split(",") if b.strip()] if buses else []
    gateway  = ctx.obj["host"] if ctx.obj else "localhost:50051"
    id_set: set[int] | None = None
    if can_id:
        id_set = {int(s.strip(), 16) for s in can_id.split(",") if s.strip()}

    def _on_frame(idx: int, msg) -> None:
        if verbose:
            iface = bus_list[min(max(0, (getattr(msg, "channel", 1) or 1) - 1),
                                 len(bus_list) - 1)] if bus_list else "vcan0"
            typer.echo(
                f"[{idx:6d}] t={msg.timestamp:.6f}  "
                f"id=0x{msg.arbitration_id:08X}  "
                f"iface={iface}  "
                f"data={msg.data.hex()}"
            )

    replayer = TraceReplayer(
        gateway        = gateway,
        buses          = bus_list,
        speed          = speed,
        simulation_id  = sim_id,
        on_frame       = _on_frame if verbose else None,
        channel_filter = channel,
        id_filter      = id_set,
    )

    speed_label = f"{speed}x" if speed > 0 else "max"
    ch_label = f" ch={channel}" if channel is not None else ""
    id_label = f" id={[hex(i) for i in sorted(id_set)]}" if id_set else ""
    typer.echo(
        f"Replaying {file.name} -> {gateway}  "
        f"[speed={speed_label}  loop={loop or 'off'}{ch_label}{id_label}"
        f"  buses={bus_list or ['vcan0']}]"
    )

    try:
        total = replayer.replay(str(file), loop=loop)
    except TraceReplayError as e:
        print_error(str(e))
        raise typer.Exit(1)
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.")
        raise typer.Exit(0)

    typer.echo(f"Done -- {total} frame(s) sent.")
