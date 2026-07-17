from __future__ import annotations

import struct
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from boat.v1 import replay_pb2

from .output import print_error, print_table
from .trace import _resolve_value, _ETHERTYPE_NAMES, _PROTOCOL_NAMES

replay_app = typer.Typer()

_SPEED_MAP = {
    "real-time":   replay_pb2.REPLAY_SPEED_REAL_TIME,
    "accelerated": replay_pb2.REPLAY_SPEED_ACCELERATED,
    "step":        replay_pb2.REPLAY_SPEED_STEP_BY_STEP,
}


def _parse_mac_map(mac_map: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not mac_map:
        return result
    for pair in mac_map.split(","):
        pair = pair.strip()
        if "=" in pair:
            ip_str, mac_str = pair.split("=", 1)
            result[ip_str.strip()] = mac_str.strip()
    return result


@replay_app.command("start")
def start_replay(
    ctx: typer.Context,
    trace: str = typer.Option(..., "--trace"),
    speed: str = typer.Option("real-time", "--speed", "-s",
                              help="Replay speed: real-time, accelerated, step"),
    multiplier: float = typer.Option(1.0, "--multiplier", "-m",
                                     help="Speed multiplier (>0). 2.0 = twice as fast."),
    loop: Optional[int] = typer.Option(None, "--loop", "-l",
                                        help="Loop with N ms gap between runs"),
    mac_map: Optional[str] = typer.Option(None, "--mac-map",
                                           help="IP→MAC mappings (comma-separated). "
                                                "Example: 192.168.0.100=02:de:ad:be:ef:01"),
    eth_iface: Optional[str] = typer.Option(None, "--eth-iface",
                                              help="Target Ethernet interface for reconstructed frames"),
    buses: str = typer.Option("", "--buses", "-b",
                              help="Comma-separated CAN interface names for channel mapping"),
    sim_id: str = typer.Option("", "--sim-id", help="Simulation ID"),
) -> None:
    proto_speed = _SPEED_MAP.get(speed, replay_pb2.REPLAY_SPEED_REAL_TIME)
    bus_list = [b.strip() for b in buses.split(",") if b.strip()] if buses else []
    response = ctx.obj["client"].replay.StartReplay(
        replay_pb2.StartReplayRequest(
            trace_id=trace,
            simulation_id=sim_id,
            speed=proto_speed,
            speed_multiplier=multiplier,
            eth_iface=eth_iface or "",
            mac_map=_parse_mac_map(mac_map),
            loop_delay_ms=loop or 0,
            buses=bus_list,
        )
    )
    print_table(["accepted", "replay_id"], [[bool(response.accepted), response.replay_id]], ctx.obj["json_mode"])


@replay_app.command("seek")
def seek_replay(ctx: typer.Context, tick: int = typer.Option(..., "--tick"), replay_id: str = "") -> None:
    response = ctx.obj["client"].replay.SeekReplay(
        replay_pb2.SeekReplayRequest(replay_id=replay_id, tick=tick)
    )
    print_table(["accepted"], [[bool(response.accepted)]], ctx.obj["json_mode"])


@replay_app.command("stream")
def stream_replay(
    ctx: typer.Context,
    trace: str = typer.Option(..., "--trace",
                              help="Trace ID to replay (from `boat replay import`)"),
    speed: str = typer.Option("real-time", "--speed", "-s",
                              help="Replay speed: real-time, accelerated, step"),
    multiplier: float = typer.Option(1.0, "--multiplier", "-m",
                                     help="Speed multiplier (>0). 2.0 = twice as fast."),
    loop: Optional[int] = typer.Option(None, "--loop", "-l",
                                        help="Loop with N ms gap between runs"),
    mac_map: Optional[str] = typer.Option(None, "--mac-map",
                                           help="IP\u2192MAC mappings (comma-separated). "
                                                "Example: 192.168.0.100=02:de:ad:be:ef:01"),
    eth_iface: Optional[str] = typer.Option(None, "--eth-iface",
                                              help="Target Ethernet interface for reconstructed frames"),
    buses: str = typer.Option("", "--buses", "-b",
                              help="Comma-separated CAN interface names for channel mapping"),
    sim_id: str = typer.Option("", "--sim-id", help="Simulation ID forwarded with every frame"),
    verbose: bool = typer.Option(False, "--verbose", "-v",
                                 help="Print tick and hex payload for each event"),
) -> None:
    """Start replaying an imported trace and stream events until completion.

    Combines StartReplay + StreamReplay into a single command so the caller
    does not need to manage replay_id manually.
    """
    proto_speed = _SPEED_MAP.get(speed, replay_pb2.REPLAY_SPEED_REAL_TIME)
    bus_list = [b.strip() for b in buses.split(",") if b.strip()] if buses else []

    speed_label = f"{multiplier}x" if proto_speed == replay_pb2.REPLAY_SPEED_ACCELERATED else speed
    typer.echo(f"Streaming {trace}  [speed={speed_label}  loop={loop or 'off'}]")

    # ── Start ──────────────────────────────────────────────────────────────
    try:
        resp = ctx.obj["client"].replay.StartReplay(
            replay_pb2.StartReplayRequest(
                trace_id=trace,
                simulation_id=sim_id,
                speed=proto_speed,
                speed_multiplier=multiplier,
                eth_iface=eth_iface or "",
                mac_map=_parse_mac_map(mac_map),
                loop_delay_ms=loop or 0,
                buses=bus_list,
            )
        )
    except Exception as e:
        print_error(f"StartReplay failed: {e}")
        raise typer.Exit(1)

    if not resp.accepted:
        msg = resp.error.message if resp.error and resp.error.message else "unknown error"
        print_error(f"StartReplay rejected: {msg}")
        raise typer.Exit(1)

    replay_id = resp.replay_id
    typer.echo(f"replay_id: {replay_id}  (use with `boat replay pause/resume/stop --replay-id ...` from another terminal)")

    # ── Stream ─────────────────────────────────────────────────────────────
    total = 0
    last_progress_at = 0.0
    try:
        stream = ctx.obj["client"].replay.StreamReplay(
            replay_pb2.StreamReplayRequest(replay_id=replay_id)
        )
        for event in stream:
            total += 1
            if verbose:
                payload_hex = event.payload.hex() if event.payload else "(empty)"
                typer.echo(f"[{total:4d}] tick={event.tick}  payload={payload_hex}")
            else:
                now = time.monotonic()
                if now - last_progress_at >= 0.1:
                    typer.echo(f"\rStreaming...  {total} frame(s) sent  tick={event.tick}", nl=False)
                    sys.stdout.flush()
                    last_progress_at = now
    except KeyboardInterrupt:
        try:
            ctx.obj["client"].replay.StopReplay(
                replay_pb2.StopReplayRequest(replay_id=replay_id)
            )
        except Exception:
            pass
        typer.echo(f"\nStopped after {total} frame(s).")
        raise typer.Exit(0)

    if not verbose and total > 0:
        typer.echo()
    typer.echo(f"Done \u2014 {total} frame(s).")


@replay_app.command("pause")
def pause_replay(ctx: typer.Context, replay_id: str = "") -> None:
    response = ctx.obj["client"].replay.PauseReplay(
        replay_pb2.PauseReplayRequest(replay_id=replay_id)
    )
    print_table(["accepted"], [[bool(response.accepted)]], ctx.obj["json_mode"])


@replay_app.command("resume")
def resume_replay(ctx: typer.Context, replay_id: str = "") -> None:
    response = ctx.obj["client"].replay.ResumeReplay(
        replay_pb2.ResumeReplayRequest(replay_id=replay_id)
    )
    print_table(["accepted"], [[bool(response.accepted)]], ctx.obj["json_mode"])


@replay_app.command("stop")
def stop_replay(ctx: typer.Context, replay_id: str = "") -> None:
    response = ctx.obj["client"].replay.StopReplay(
        replay_pb2.StopReplayRequest(replay_id=replay_id)
    )
    print_table(["accepted"], [[bool(response.accepted)]], ctx.obj["json_mode"])


@replay_app.command("from-events")
def start_replay_from_events(
    ctx: typer.Context,
    sim_id: str = typer.Option(..., "--sim-id", help="Simulation ID to replay events from"),
    signal_id: str = typer.Option("", "--signal-id", help="Filter by signal ID"),
    tick_min: int = typer.Option(0, "--tick-min", help="Minimum tick"),
    tick_max: int = typer.Option(0, "--tick-max", help="Maximum tick"),
    speed: str = typer.Option("real-time", "--speed", "-s",
                              help="Replay speed: real-time, accelerated, step"),
    multiplier: float = typer.Option(1.0, "--multiplier", "-m",
                                     help="Speed multiplier (>0). 2.0 = twice as fast."),
) -> None:
    proto_speed = _SPEED_MAP.get(speed, replay_pb2.REPLAY_SPEED_REAL_TIME)
    response = ctx.obj["client"].replay.StartReplayFromEvents(
        replay_pb2.StartReplayFromEventsRequest(
            simulation_id=sim_id,
            signal_id=signal_id,
            tick_min=tick_min,
            tick_max=tick_max,
            speed=proto_speed,
            speed_multiplier=multiplier,
        )
    )
    print_table(["accepted", "replay_id"], [[bool(response.accepted), response.replay_id]], ctx.obj["json_mode"])


@replay_app.command("import")
def cmd_import(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Path to .asc, .blf, or .pcap trace file"),
    trace_id: str = typer.Option("", "--trace-id",
                                 help="Trace ID (default: filename stem)"),
    channel: Optional[int] = typer.Option(None, "--channel", "-c",
                                           help="Only include CAN frames from this channel (1-based)"),
    can_id: Optional[str] = typer.Option(None, "--id", "-i",
                                          help="Only include CAN frames with these arbitration IDs "
                                               "(hex, comma-separated). Example: 0x100,0x200"),
    ip_map: Optional[str] = typer.Option(None, "--ip-map",
                                          help="Rewrite IP addresses: old=new pairs (comma-separated). "
                                               "Example: 10.0.0.1=192.168.0.100,10.0.0.2=192.168.0.101"),
    ethertype: Optional[str] = typer.Option(None, "--ethertype",
                                             help="Only include this EtherType (hex or name, comma-separated). "
                                                  "Example: ipv4,0x86DD"),
    protocol: Optional[str] = typer.Option(None, "--protocol",
                                             help="Only include this L4 protocol (number or name, comma-separated). "
                                                  "Example: udp,17"),
    replay_src_ip: Optional[str] = typer.Option(None, "--replay-src-ip",
                                                  help="Source IP for reconstructed IP header (Ethernet pcap import)"),
    replay_dst_ip: Optional[str] = typer.Option(None, "--replay-dst-ip",
                                                  help="Destination IP for reconstructed IP header"),
    ip_filter: Optional[str] = typer.Option(None, "--ip-filter",
                                              help="Comma-separated IP addresses to filter by (applied after IP "
                                                   "mapping). Only packets whose rewritten src or dst matches are "
                                                   "kept. Example: 192.168.0.100,192.168.0.101"),
    src_ip_filter: Optional[str] = typer.Option(None, "--src-ip-filter",
                                                  help="Comma-separated source IP addresses to filter by (applied "
                                                       "after IP mapping). Only packets whose rewritten source IP "
                                                       "is in this set are kept. Example: 192.168.0.100"),
    dst_ip_filter: Optional[str] = typer.Option(None, "--dst-ip-filter",
                                                  help="Comma-separated destination IP addresses to filter by "
                                                       "(applied after IP mapping). Only packets whose rewritten "
                                                       "destination IP is in this set are kept. "
                                                       "Example: 192.168.0.101"),
    src_port: Optional[str] = typer.Option(None, "--src-port",
                                            help="Comma-separated UDP/TCP source port numbers to filter by. "
                                                 "Only packets whose source port is in this set are kept. "
                                                 "Example: 67,68"),
    dst_port: Optional[str] = typer.Option(None, "--dst-port",
                                            help="Comma-separated UDP/TCP destination port numbers to filter by. "
                                                 "Only packets whose destination port is in this set are kept. "
                                                 "Example: 30490"),
) -> None:
    """Convert a trace file to the internal binary format and upload it to the gateway for later replay."""
    file = file.resolve()
    if not file.exists():
        print_error(f"File not found: {file}")
        raise typer.Exit(1)

    tid = trace_id or file.stem

    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "sdk" / "python"))
        from boat.trace_replay import TraceReplayer, TraceReplayError
    except ImportError as e:
        print_error(f"Cannot import boat SDK: {e}")
        raise typer.Exit(1)

    # ── Parse filter args ─────────────────────────────────────────────────
    id_set: set[int] | None = None
    if can_id:
        id_set = {int(s.strip(), 16) for s in can_id.split(",") if s.strip()}

    ip_map_dict: dict[str, str] | None = None
    if ip_map:
        ip_map_dict = {}
        for pair in ip_map.split(","):
            pair = pair.strip()
            if "=" in pair:
                old_ip, new_ip = pair.split("=", 1)
                ip_map_dict[old_ip.strip()] = new_ip.strip()

    ethertype_set: set[int] | None = None
    if ethertype:
        ethertype_set = {_resolve_value(s.strip(), _ETHERTYPE_NAMES)
                         for s in ethertype.split(",") if s.strip()}

    protocol_set: set[int] | None = None
    if protocol:
        protocol_set = {_resolve_value(s.strip(), _PROTOCOL_NAMES)
                        for s in protocol.split(",") if s.strip()}

    ip_filter_set: set[str] | None = None
    if ip_filter:
        ip_filter_set = {s.strip() for s in ip_filter.split(",") if s.strip()}

    src_ip_filter_set: set[str] | None = None
    if src_ip_filter:
        src_ip_filter_set = {s.strip() for s in src_ip_filter.split(",") if s.strip()}

    dst_ip_filter_set: set[str] | None = None
    if dst_ip_filter:
        dst_ip_filter_set = {s.strip() for s in dst_ip_filter.split(",") if s.strip()}

    src_port_set: set[int] | None = None
    if src_port:
        src_port_set = {int(s.strip()) for s in src_port.split(",") if s.strip()}

    dst_port_set: set[int] | None = None
    if dst_port:
        dst_port_set = {int(s.strip()) for s in dst_port.split(",") if s.strip()}

    # ── Convert ───────────────────────────────────────────────────────────
    replayer = TraceReplayer(
        channel_filter=channel,
        id_filter=id_set,
        ip_map=ip_map_dict,
        ethertype_filter=ethertype_set,
        protocol_filter=protocol_set,
        replay_src_ip=replay_src_ip,
        replay_dst_ip=replay_dst_ip,
        ip_filter=ip_filter_set,
        src_ip_filter=src_ip_filter_set,
        dst_ip_filter=dst_ip_filter_set,
        src_port_filter=src_port_set,
        dst_port_filter=dst_port_set,
    )

    try:
        binary_data = replayer.convert_to_binary(file)
    except TraceReplayError as e:
        print_error(str(e))
        raise typer.Exit(1)

    # ── Upload ────────────────────────────────────────────────────────────
    suffix = file.suffix.lstrip(".").upper()
    try:
        response = ctx.obj["client"].replay.ImportTraceData(
            replay_pb2.ImportTraceDataRequest(
                trace_id=tid,
                format=suffix,
                data=binary_data,
            )
        )
    except Exception as e:
        print_error(f"ImportTraceData failed: {e}")
        raise typer.Exit(1)

    if not response.accepted:
        msg = response.error.message if response.error and response.error.message else "unknown error"
        print_error(f"ImportTraceData rejected: {msg}")
        raise typer.Exit(1)

    # ▸ Each record is a 4-byte little-endian length prefix followed by that
    #   many bytes of serialized boat.v1.Frame protobuf; walk the stream to
    #   get an exact count.
    n_frames = 0
    off = 0
    while off + 4 <= len(binary_data):
        (record_len,) = struct.unpack_from("<I", binary_data, off)
        off += 4 + record_len
        n_frames += 1
    size_kb = len(binary_data) / 1024
    print_table(
        ["accepted", "trace_id", "frames", "size"],
        [[True, tid, n_frames, f"{size_kb:.1f} KB"]],
        ctx.obj["json_mode"],
    )
