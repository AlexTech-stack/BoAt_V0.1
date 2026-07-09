from __future__ import annotations

import sys
from typing import Annotated

import typer

from .output import print_error, print_table

can_tp_app = typer.Typer(help="CAN Transport Protocol (ISO 15765-2) commands.")


@can_tp_app.command("configure")
def can_tp_configure(
    ctx: typer.Context,
    nsdu_id: Annotated[str, typer.Option("--nsdu-id", help="N-SDU ID (hex or decimal).")],
    source_addr: Annotated[str, typer.Option("--source-addr", help="CAN ID of this node (hex or decimal).")] = "",
    target_addr: Annotated[str, typer.Option("--target-addr", help="CAN ID of the peer node (hex or decimal).")] = "",
    block_size: Annotated[int, typer.Option("--bs", help="Block Size to advertise in FC (0=unlimited).")] = 0,
    st_min: Annotated[int, typer.Option("--stmin", help="Separation Time in ms to advertise in FC.")] = 0,
    rx_buffer_size: Annotated[int, typer.Option("--rx-buffer", help="RX reassembly buffer size.")] = 4095,
    can_dlc: Annotated[int, typer.Option("--dlc", help="CAN DLC (8 or 64 for CAN-FD).")] = 8,
) -> None:
    """Configure an N-SDU connection for ISO 15765-2.

    Both the local (source) and peer (target) CAN addresses are required
    for proper multi-ID session handling.

    \b
    Examples:
      # Single-ID (nsdu_id used as both source and target)
      boat can-tp configure --nsdu-id 0x7E0

      # Dual-ID session (tester sending to ECU)
      boat can-tp configure --nsdu-id my_session --source-addr 0x7E0 --target-addr 0x7E8 --bs 0 --stmin 0
    """
    from boat.can_tp import CanTpHandle

    resolved_id = int(nsdu_id, 0)
    kwargs = dict(
        can_dlc=can_dlc,
        block_size=block_size,
        st_min=st_min,
        rx_buffer_size=rx_buffer_size,
    )
    if source_addr:
        kwargs["source_addr"] = int(source_addr, 0)
    if target_addr:
        kwargs["target_addr"] = int(target_addr, 0)

    import glob as _glob
    candidates = (
        _glob.glob("build/debug/src/plugins/can_tp/can_tp.so") +
        _glob.glob("build/release/src/plugins/can_tp/can_tp.so") +
        _glob.glob("/usr/local/lib/boat/plugins/can_tp.so")
    )
    so_path = candidates[0] if candidates else "./build/debug/src/plugins/can_tp/can_tp.so"

    try:
        handle = CanTpHandle(so_path)
    except FileNotFoundError:
        print_error(
            f"CanTp plugin not found at '{so_path}'. "
            f"Build it first: cmake --build --preset debug"
        )
        sys.exit(1)
    except OSError as ex:
        print_error(f"Failed to load CanTp plugin: {ex}")
        sys.exit(1)

    result = handle.configure(resolved_id, **kwargs)
    if result:
        print_table(
            ["nsdu_id", "source_addr", "target_addr", "bs", "stmin", "dlc"],
            [[f"0x{resolved_id:X}",
              f"0x{kwargs.get('source_addr', resolved_id):X}",
              f"0x{kwargs.get('target_addr', resolved_id):X}",
              block_size, st_min, can_dlc]],
            ctx.obj.get("json_mode", False),
        )
    else:
        print_error(f"configure failed for nsdu_id=0x{resolved_id:X}")
        sys.exit(1)


@can_tp_app.command("send")
def can_tp_send(
    ctx: typer.Context,
    nsdu_id: Annotated[str, typer.Option("--nsdu-id", help="N-SDU ID (hex or decimal).")],
    data: Annotated[str, typer.Option("--data", help="Hex payload (large, will be segmented).")],
    source_addr: Annotated[str, typer.Option("--source-addr", help="CAN ID of this node (hex or decimal).")] = "",
    target_addr: Annotated[str, typer.Option("--target-addr", help="CAN ID of the peer node (hex or decimal).")] = "",
    block_size: Annotated[int, typer.Option("--bs", help="Block Size to advertise in FC (0=unlimited).")] = 0,
    st_min: Annotated[int, typer.Option("--stmin", help="Separation Time in ms to advertise in FC.")] = 0,
    can_dlc: Annotated[int, typer.Option("--dlc", help="CAN DLC (8 or 64 for CAN-FD).")] = 8,
) -> None:
    """Send a large PDU via ISO 15765-2 segmentation.

    Uses the CanTp plugin's standalone C API directly.

    \b
    Example:
      boat can-tp send --nsdu-id 0x7E0 --source-addr 0x7E0 --target-addr 0x7E8 --data 0123456789ABCDEF...
    """
    from boat.can_tp import CanTpHandle

    resolved_id = int(nsdu_id, 0)
    payload = bytes.fromhex(data.replace(":", "").replace(" ", ""))

    if len(payload) <= can_dlc - 1:
        print_table(
            ["nsdu_id", "len", "note"],
            [[f"0x{resolved_id:X}", len(payload),
              "Payload fits in a single CAN frame. Use 'boat pdu send --id --data' instead."]],
            ctx.obj.get("json_mode", False),
        )
        return

    import glob as _glob
    candidates = (
        _glob.glob("build/debug/src/plugins/can_tp/can_tp.so") +
        _glob.glob("build/release/src/plugins/can_tp/can_tp.so") +
        _glob.glob("/usr/local/lib/boat/plugins/can_tp.so")
    )
    so_path = candidates[0] if candidates else "./build/debug/src/plugins/can_tp/can_tp.so"

    try:
        handle = CanTpHandle(so_path)
    except FileNotFoundError:
        print_error(
            f"CanTp plugin not found at '{so_path}'. "
            f"Build it first: cmake --build --preset debug"
        )
        sys.exit(1)
    except OSError as ex:
        print_error(f"Failed to load CanTp plugin: {ex}")
        sys.exit(1)

    kwargs = dict(can_dlc=can_dlc, block_size=block_size, st_min=st_min)
    if source_addr:
        kwargs["source_addr"] = int(source_addr, 0)
    if target_addr:
        kwargs["target_addr"] = int(target_addr, 0)
    handle.configure(resolved_id, **kwargs)
    result = handle.send(resolved_id, payload)

    if result:
        print_table(
            ["nsdu_id", "len", "result"],
            [[f"0x{resolved_id:X}", len(payload), "initiated"]],
            ctx.obj.get("json_mode", False),
        )
    else:
        print_error(f"can_tp_send failed for nsdu_id=0x{resolved_id:X}")
        sys.exit(1)
