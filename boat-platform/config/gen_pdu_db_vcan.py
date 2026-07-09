#!/usr/bin/env python3
"""Generate pdu_db_vcan.json — 30 CAN messages across vcan0 and vcan1."""

import json
import random

random.seed(777)

_db_counter = 0
def next_db():
    global _db_counter
    _db_counter += 1
    return _db_counter

_CYCLE_TIMES = [10, 50, 100, 500, 1000]

SIGNAL_DEFS = [
    ("VehicleSpeed",     16, 0.01,   0.0, 0,   0.0,  300.0,   "km/h",  None),
    ("EngineRPM",        16, 0.125,  0.0, 0,   0.0,  8000.0,  "rpm",   None),
    ("BatterySOC",        8, 0.5,    0.0, 0,   0.0,  100.0,   "%",     None),
    ("Odometer",         24, 0.1,    0.0, 0,   0.0,  999999.0,"km",    None),
    ("CoolantTemp",       8, 1.0,    0.0, 0,   0.0,  150.0,   "degC",  None),
    ("FuelLevel",          8, 0.4,    0.0, 0,   0.0,  100.0,   "%",     None),
    ("SteeringAngle",    16, 0.1,    0.0, 0,   0.0,  4095.0,  "deg",   None),
    ("BrakePressure",    16, 0.01,   0.0, 0,   0.0,  250.0,   "bar",   None),
    ("WheelSpeed_FL",    16, 0.01,   0.0, 0,   0.0,  300.0,   "km/h",  None),
    ("WheelSpeed_FR",    16, 0.01,   0.0, 0,   0.0,  300.0,   "km/h",  None),
    ("WheelSpeed_RL",    16, 0.01,   0.0, 0,   0.0,  300.0,   "km/h",  None),
    ("WheelSpeed_RR",    16, 0.01,   0.0, 0,   0.0,  300.0,   "km/h",  None),
    ("TirePressure_FL",   8, 0.05,   0.0, 0,   0.0,    5.0,   "bar",   None),
    ("TirePressure_FR",   8, 0.05,   0.0, 0,   0.0,    5.0,   "bar",   None),
    ("TirePressure_RL",   8, 0.05,   0.0, 0,   0.0,    5.0,   "bar",   None),
    ("TirePressure_RR",   8, 0.05,   0.0, 0,   0.0,    5.0,   "bar",   None),
    ("AccelPedalPos",     8, 0.4,    0.0, 0,   0.0,  100.0,   "%",     None),
    ("BrakePedalPos",     8, 0.4,    0.0, 0,   0.0,  100.0,   "%",     None),
    ("BatteryVoltage",    8, 0.1,    0.0, 0,   0.0,   25.5,   "V",     None),
    ("CruiseSpeed",      16, 0.01,   0.0, 0,   0.0,  250.0,   "km/h",  None),
    ("OutsideTemp",       8, 1.0,    0.0, 0,   0.0,  150.0,   "degC",  None),
    ("OilPressure",       8, 0.1,    0.0, 0,   0.0,   25.5,   "bar",   None),
    ("HV_Current",       16, 0.1,    0.0, 0,   0.0,  500.0,   "A",     None),
    ("HV_Voltage",       16, 0.1,    0.0, 0,   0.0,  800.0,   "V",     None),
    ("SeatPos",           8, 1.0,    0.0, 0,   0.0,  100.0,   "%",     None),
    ("GearPosition",      4, 1.0,    0.0, 0,   0.0,    6.0,   "",      {"0": "P","1": "R","2": "N","3": "D","4": "S","5": "M"}),
    ("AirbagStatus",      2, 1.0,    0.0, 0,   0.0,    3.0,   "",      {"0": "Ok","1": "Deployed","2": "Fault","3": "Unknown"}),
    ("DoorLockStatus",    2, 1.0,    0.0, 0,   0.0,    3.0,   "",      {"0": "Locked","1": "Unlocked","2": "Error","3": "Unknown"}),
    ("HV_BatteryTemp",    8, 1.0,    0.0, 0,   0.0,  150.0,   "degC",  None),
    ("FuelPressure",      8, 0.1,    0.0, 0,   0.0,   25.5,   "bar",   None),
]

# Bus assignment
BUS0 = "vcan0"
BUS1 = "vcan1"

bus0_signals = [
    "VehicleSpeed", "EngineRPM", "BatterySOC", "Odometer", "CoolantTemp",
    "FuelLevel", "AccelPedalPos", "BrakePedalPos", "HV_Current", "HV_Voltage",
    "HV_BatteryTemp", "GearPosition", "CruiseSpeed", "OilPressure", "FuelPressure",
]

bus1_signals = [
    "SteeringAngle", "BrakePressure",
    "WheelSpeed_FL", "WheelSpeed_FR", "WheelSpeed_RL", "WheelSpeed_RR",
    "TirePressure_FL", "TirePressure_FR", "TirePressure_RL", "TirePressure_RR",
    "BatteryVoltage", "OutsideTemp", "SeatPos", "AirbagStatus", "DoorLockStatus",
]

# CAN ID assignment
# 11-bit IDs: 0x100-0x7FF, 29-bit: 0x18FF0000-0x18FFFFFF
# First 20 messages get 11-bit IDs, last 10 get 29-bit
STD_BASE = 0x100
EXT_BASE = 0x18FF0010

def make_signal(name, sig_def):
    length, factor, offset, init_val, mn, mx, unit, enum_vals = sig_def
    return {
        "id": 1,
        "SignalName": f"s_{name}",
        "Length": length,
        "StartPos": 0,
        "ByteOrder": 0,
        "ValueType": "Unsigned",
        "SigSendType": False,
        "Repetitions": 0,
        "InitValue": init_val,
        "Factor": factor,
        "Offset": offset,
        "Min": mn,
        "Max": mx,
        "Unit": unit,
        "EnumValues": enum_vals,
        "IsMuxor": False,
        "MuxValue": None,
        "Comment": "",
    }

def make_message(name, sig_def, bus, can_id, frame_type):
    signal = make_signal(name, sig_def)
    sig_len = signal["Length"]
    dlc = max(1, (sig_len + 7) // 8)
    cycle = random.choice(_CYCLE_TIMES)
    return {
        "DbId": next_db(),
        "MessageName": f"m_{name}",
        "Bus": bus,
        "BusType": "CAN",
        "MessageType": 0,
        "Direction": 0,
        "RoutingType": 2,
        "TargetDbIds": None,
        "SourceDbId": None,
        "isE2E": 0,
        "SendType": "Cyclic",
        "CycleTime": cycle,
        "CycleTimeFast": 0,
        "NrOfRepetitions": 0,
        "Identifier": can_id,
        "FrameType": frame_type,
        "Length": max(8, dlc),
        "BRS": False,
        "signalcount": 1,
        "signals": [signal],
        "Comment": "",
        "Node": "",
    }

messages = []

# Build signal lookup
sig_lookup = {s[0]: s for s in SIGNAL_DEFS}

# Bus0: 15 messages (IDs 0x100-0x10E), all 11-bit
for i, name in enumerate(bus0_signals):
    frame_type = 1 if i >= 20 else 0
    if i < 10:
        can_id = STD_BASE + i
        ft = 0
    else:
        can_id = EXT_BASE + (i - 10)
        ft = 1
    messages.append(make_message(name, sig_lookup[name][1:], BUS0, can_id, ft))

# Bus1: 15 messages (IDs 0x200-0x20E), all 11-bit
for i, name in enumerate(bus1_signals):
    if i < 10:
        can_id = STD_BASE + 0x100 + i
        ft = 0
    else:
        can_id = EXT_BASE + 0x10 + (i - 10)
        ft = 1
    messages.append(make_message(name, sig_lookup[name][1:], BUS1, can_id, ft))

db = {
    "schema_version": "1.0",
    "messages": messages,
    "signal_routes": [],
}

out_path = __file__.replace("gen_pdu_db_vcan.py", "pdu_db_vcan.json")
with open(out_path, "w") as f:
    json.dump(db, f, indent=2)

print(f"Written {len(messages)} messages to {out_path}")

# Summary
for m in messages:
    sig = m["signals"][0]
    print(f"  DbId={m['DbId']:2d}  {m['MessageName']:25s}  bus={m['Bus']:6s}  "
          f"ID=0x{m['Identifier']:07X}  {'29bit' if m['FrameType'] else '11bit'}  "
          f"cycle={m['CycleTime']:4d}ms  sig={sig['SignalName']:20s}  "
          f"len={sig['Length']:2d}bits  {sig['Unit']}")
