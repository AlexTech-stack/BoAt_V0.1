"""Shell-completion callbacks shared across boat CLI subcommands.

Typer 0.24.x accepts these signatures for the autocompletion parameter:
  - (incomplete: str) -> list[str]
  - (ctx, incomplete: str) -> list[str]
  (A third "param" argument is NOT accepted in this Typer version.)
"""
from __future__ import annotations

import glob
import os
from typing import List


def complete_transport(ctx, incomplete: str) -> List[str]:
    return [t for t in ("can", "eth") if t.startswith(incomplete.lower())]


def complete_iface(ctx, incomplete: str) -> List[str]:
    """Complete from the kernel's network interface list."""
    try:
        ifaces = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return []
    return [i for i in ifaces if i.startswith(incomplete)]


def complete_json_file(ctx, incomplete: str) -> List[str]:
    """Complete .json file paths."""
    pattern = (incomplete or "") + "*.json"
    return sorted(glob.glob(pattern))


def _msg_names_from_ctx(ctx, incomplete: str, bus_types=None) -> List[str]:
    """Read message names from the --db param (or default) and filter by bus_types."""
    db_path = (ctx.params or {}).get("db") or (ctx.params or {}).get("db_path") or "pdu_db.json"
    try:
        from boat.pdu_db import PduDatabase
        db = PduDatabase(db_path)
        names = db.names()
        if bus_types:
            names = [n for n in names if db.by_name(n).get("BusType") in bus_types]
        return [n for n in names if n.startswith(incomplete)]
    except Exception:
        return []


def complete_msg_name(ctx, incomplete: str) -> List[str]:
    """Complete any message name from the active --db file."""
    return _msg_names_from_ctx(ctx, incomplete)


def complete_can_msg_name(ctx, incomplete: str) -> List[str]:
    """Complete CAN/CANFD message names from --db."""
    return _msg_names_from_ctx(ctx, incomplete, bus_types=("CAN", "CANFD"))


def complete_pdu_msg_name(ctx, incomplete: str) -> List[str]:
    """Complete ETH_PDU message names from --db."""
    return _msg_names_from_ctx(ctx, incomplete, bus_types=("ETH_PDU",))
