"""
BoAt Platform — DBC to PDU Database JSON Converter

Converts industry-standard CAN DBC files to BoAt's PDU database JSON format
(pdu_db.json).  Supports BO_, SG_ (including multiplexed M/mN), VAL_, CM_,
and BA_ "GenMsgCycleTime" elements.

Usage:
    python3 tools/DBC2BoatJSON.py pdu_db.schema.json input.dbc output.json
    python3 tools/DBC2BoatJSON.py --bus CAN --bus-type CAN pdu_db.schema.json input.dbc output.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional


# ── DBC grammar regexes ──────────────────────────────────────────────────────

BO_RE = re.compile(
    r"^BO_ (\w+) (\w+) *: (\w+) (\w+)"
)

SG_RE = re.compile(
    r"^SG_ (\w+)"                           # 1: signal name
    r"(?:\s+([Mm]\w*))?"                    # 2: optional mux indicator (M / m0 / m1 …)
    r"\s*:\s+"
    r"(\d+)\|"                              # 3: start bit
    r"(\d+)@"                               # 4: length (bits)
    r"(\d)"                                 # 5: byte order (0=Motorola, 1=Intel)
    r"([+-])"                               # 6: sign
    r"\s*\("
    r"([0-9.eE+\-]+)"                       # 7: factor
    r","
    r"([0-9.eE+\-]+)"                       # 8: offset
    r"\)\s*\["
    r"([0-9.eE+\-]+)"                       # 9: min (raw)
    r"\|"
    r"([0-9.eE+\-]+)"                       # 10: max (raw)
    r"\]\s*\""
    r"([^\"]*)"                             # 11: unit
    r"\"\s*(\S+)?"                          # 12: receiver (optional)
)

VAL_RE = re.compile(
    r"^VAL_ (\w+) (\w+) (.*);"
)

CM_RE = re.compile(
    r'^CM_ (\w+) (\w+)(?: (\w+))? "([^"]*)";'
)

BA_CYCLE_RE = re.compile(
    r'^BA_ "GenMsgCycleTime" BO_ (\w+) (\d+);'
)

# Message name suffix encoding cycle time (e.g. "ESC_02_10ms" → 10ms)
CYCLE_NAME_RE = re.compile(r"_(\d+)ms$")


# ── DBC parser ───────────────────────────────────────────────────────────────

def parse_dbc(path: str) -> dict:
    """Parse a .dbc file and return a dict of extracted data."""

    with open(path) as f:
        lines = f.readlines()

    messages: dict[int, dict] = {}         # address → {name, size, sender, signals, comment}
    signals: dict[int, list[dict]] = {}    # address → [{name, start_bit, size, …}]
    vals: dict[tuple[int, str], dict] = {} # (address, sig_name) → {raw: label}
    comments_msg: dict[int, str] = {}      # address → comment
    comments_sig: dict[tuple[int, str], str] = {}  # (address, sig_name) → comment
    cycle_times: dict[int, int] = {}       # address → cycle_ms

    cur_addr: Optional[int] = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # ── BO_ message ──────────────────────────────────────────────────
        m = BO_RE.match(line)
        if m:
            addr = int(m.group(1), 0)
            cur_addr = addr
            messages[addr] = {
                "name": m.group(2),
                "size": int(m.group(3), 0),
                "sender": m.group(4),
            }
            signals[addr] = []
            continue

        # ── SG_ signal ───────────────────────────────────────────────────
        m = SG_RE.match(line)
        if m:
            addr = cur_addr
            if addr is None:
                continue

            sig_name = m.group(1)
            mux_tag = m.group(2)  # None, "M", "m0", "m1", …

            is_muxor = mux_tag == "M" if mux_tag else False
            mux_value: Optional[int] = None
            if mux_tag and mux_tag != "M":
                # Extract integer from m0, m1, …
                num_part = mux_tag[1:]
                mux_value = int(num_part) if num_part else None

            start_bit = int(m.group(3))
            size = int(m.group(4))
            byte_order = int(m.group(5))   # 0=Motorola, 1=Intel
            sign = m.group(6)              # + or -
            factor = float(m.group(7))
            offset = float(m.group(8))
            raw_min = float(m.group(9))
            raw_max = float(m.group(10))
            unit = m.group(11)
            receiver = m.group(12) or ""

            signals[addr].append({
                "name": sig_name,
                "start_bit": start_bit,
                "size": size,
                "byte_order": byte_order,
                "sign": sign,
                "factor": factor,
                "offset": offset,
                "raw_min": raw_min,
                "raw_max": raw_max,
                "unit": unit,
                "receiver": receiver,
                "is_muxor": is_muxor,
                "mux_value": mux_value,
            })
            continue

        # ── VAL_ value descriptions ──────────────────────────────────────
        m = VAL_RE.match(line)
        if m:
            val_addr = int(m.group(1), 0)
            val_sig = m.group(2)
            defs_raw = m.group(3)
            pairs = re.findall(r'(\S+)\s+"([^"]*)"', defs_raw)
            mapping: dict[str, str] = {}
            for raw_val, label in pairs:
                mapping[raw_val] = label
            if mapping:
                vals[(val_addr, val_sig)] = mapping
            continue

        # ── CM_ comments ─────────────────────────────────────────────────
        m = CM_RE.match(line)
        if m:
            obj_type = m.group(1)  # "BO", "SG", or "BU"
            obj_addr = int(m.group(2), 0)
            obj_name = m.group(3)  # signal name for SG, None for BO
            comment = m.group(4)
            if obj_type == "BO":
                comments_msg[obj_addr] = comment
            elif obj_type == "SG" and obj_name:
                comments_sig[(obj_addr, obj_name)] = comment
            continue

        # ── BA_ "GenMsgCycleTime" ────────────────────────────────────────
        m = BA_CYCLE_RE.match(line)
        if m:
            ba_addr = int(m.group(1), 0)
            cycle_times[ba_addr] = int(m.group(2))
            continue

    return {
        "messages": messages,
        "signals": signals,
        "vals": vals,
        "comments_msg": comments_msg,
        "comments_sig": comments_sig,
        "cycle_times": cycle_times,
    }


# ── BoAt JSON builder ────────────────────────────────────────────────────────

def _name_suffix_cycle_ms(name: str) -> Optional[int]:
    """Extract cycle time from message name ending in ``_<N>ms`` (e.g. ``ESC_02_10ms``)."""
    m = CYCLE_NAME_RE.search(name)
    if m:
        val = int(m.group(1))
        if val > 0:
            return val
    return None


def build_boat_db(dbc: dict, *, bus: str = "CAN", bus_type: str = "CAN",
                  default_node: str = "", start_id: int = 1,
                  default_cycle_ms: int = 0) -> dict:
    """Convert parsed DBC data into a BoAt PDU database dict."""

    messages = dbc["messages"]
    signals = dbc["signals"]
    vals = dbc["vals"]
    comments_msg = dbc["comments_msg"]
    comments_sig = dbc["comments_sig"]
    cycle_times = dbc["cycle_times"]

    boat_messages: list[dict] = []
    next_db_id = start_id

    for addr in sorted(messages.keys()):
        msg = messages[addr]
        sigs = signals.get(addr, [])
        # Determine send type and cycle time (priority: BA_ > name suffix > default > Spontaneous)
        cycle_ms = cycle_times.get(addr, 0)
        if cycle_ms == 0:
            cycle_ms = _name_suffix_cycle_ms(msg["name"]) or 0
        if cycle_ms == 0 and default_cycle_ms > 0:
            cycle_ms = default_cycle_ms

        send_type = "Cyclic" if cycle_ms > 0 else "Spontaneous"

        # Frame type: 29-bit if address > 0x7FF
        frame_type = 1 if addr > 0x7FF else 0

        sig_id_counter = 0
        boat_signals: list[dict] = []

        for s in sigs:
            sig_id_counter += 1

            # ValueType
            if s["size"] == 1 and s["sign"] == "+":
                value_type = "Bool"
            elif s["sign"] == "-":
                value_type = "Signed"
            else:
                value_type = "Unsigned"

            # ByteOrder: 0=Intel, 1=Motorola
            byte_order = 0 if s["byte_order"] == 1 else 1

            # Physical min/max from raw range
            phys_min = s["raw_min"] * s["factor"] + s["offset"]
            phys_max = s["raw_max"] * s["factor"] + s["offset"]

            # Enum values from VAL_ entries
            enum_values = vals.get((addr, s["name"]))

            # Comment from CM_ SG_ line
            comment = comments_sig.get((addr, s["name"]), "")

            boat_signals.append({
                "id": sig_id_counter,
                "SignalName": s["name"],
                "Length": s["size"],
                "StartPos": s["start_bit"],
                "ByteOrder": byte_order,
                "ValueType": value_type,
                "SigSendType": False,
                "Repetitions": 0,
                "InitValue": 0,
                "Factor": s["factor"],
                "Offset": s["offset"],
                "Min": phys_min,
                "Max": phys_max,
                "Unit": s["unit"],
                "EnumValues": enum_values,
                "IsMuxor": s["is_muxor"],
                "MuxValue": s["mux_value"],
                "Comment": comment,
            })

        sender_node = msg.get("sender", "") or default_node

        boat_messages.append({
            "DbId": next_db_id,
            "MessageName": msg["name"],
            "Bus": bus,
            "BusType": bus_type,
            "MessageType": 0,
            "Direction": 0,
            "RoutingType": 0,
            "TargetDbIds": None,
            "SourceDbId": None,
            "isE2E": 0,
            "SendType": send_type,
            "CycleTime": cycle_ms,
            "CycleTimeFast": 0,
            "NrOfRepetitions": 0,
            "Identifier": addr,
            "FrameType": frame_type,
            "Length": msg["size"],
            "BRS": False,
            "signalcount": len(boat_signals),
            "signals": boat_signals,
            "Comment": comments_msg.get(addr, ""),
            "Node": sender_node,
        })

        next_db_id += 1

    return {
        "schema_version": "1.0",
        "messages": boat_messages,
        "signal_routes": [],
    }


# ── Schema validation ────────────────────────────────────────────────────────

def validate(schema_path: str, instance: dict) -> list[str]:
    """Validate *instance* against the JSON schema at *schema_path*.

    Returns a list of error messages (empty = valid).
    """
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema not installed — skipping validation"]

    with open(schema_path) as f:
        schema = json.load(f)

    validator = jsonschema.Draft202012Validator(schema)
    errors: list[str] = []
    for error in validator.iter_errors(instance):
        path = " → ".join(str(p) for p in error.absolute_path) or "root"
        errors.append(f"{path}: {error.message}")
    return errors


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a CAN DBC file to BoAt PDU database JSON format.",
    )
    parser.add_argument("schema", help="Path to pdu_db.schema.json")
    parser.add_argument("input",  help="Input .dbc file")
    parser.add_argument("output", help="Output .json file")
    parser.add_argument("--bus", default="CAN",
                        help="Default bus name (default: CAN)")
    parser.add_argument("--bus-type", default="CAN", choices=["CAN", "CANFD"],
                        help="Bus type (default: CAN)")
    parser.add_argument("--node", default="",
                        help="Default node name when BO_ sender is missing")
    parser.add_argument("--start-id", type=int, default=1,
                        help="First DbId to assign (default: 1)")
    parser.add_argument("--default-cycle-ms", type=int, default=0,
                        help="Default cycle time in ms for messages without BA_ GenMsgCycleTime or name suffix (default: 0 = Spontaneous)")
    parser.add_argument("--validate", action="store_true",
                        help="Validate the generated output against the schema (requires jsonschema)")

    args = parser.parse_args()

    # ── Parse ───────────────────────────────────────────────────────────
    print(f"Parsing DBC: {args.input}")
    dbc = parse_dbc(args.input)
    print(f"  Found {len(dbc['messages'])} messages, "
          f"{sum(len(s) for s in dbc['signals'].values())} signals")

    # ── Build ───────────────────────────────────────────────────────────
    boat_db = build_boat_db(
        dbc,
        bus=args.bus,
        bus_type=args.bus_type,
        default_node=args.node,
        start_id=args.start_id,
        default_cycle_ms=args.default_cycle_ms,
    )

    # ── Validate ────────────────────────────────────────────────────────
    if args.validate:
        print(f"Validating against schema: {args.schema}")
        errors = validate(args.schema, boat_db)
        if errors:
            print("Validation errors:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        print("  Schema validation passed")

    # ── Write ───────────────────────────────────────────────────────────
    with open(args.output, "w") as f:
        json.dump(boat_db, f, indent=2)
    print(f"Written {args.output} ({len(boat_db['messages'])} messages)")


if __name__ == "__main__":
    main()
