#!/usr/bin/env python3
"""Generate pdu_db_test.json — exactly 120 entries (80 CAN + 40 Ethernet)."""

import json
from copy import deepcopy

# ── Reusable signal shapes ─────────────────────────────────────────────────

SIGNAL_DEFS = {
    "VehicleSpeed":    (16, 0, 0, "Unsigned", 0.01,  0.0,    0.0,  300.0,   "km/h"),
    "EngineRPM":       (16, 0, 0, "Unsigned", 0.125, 0.0,    0.0,  8000.0,  "rpm"),
    "BatterySOC":      (8,  0, 0, "Unsigned", 0.5,   0.0,    0.0,  100.0,   "%"),
    "AmbientTemp":     (8,  0, 1, "Signed",   1.0,   0.0,  -40.0,   85.0,   "degC"),
    "Odometer":        (24, 0, 0, "Unsigned", 0.1,   0.0,    0.0, 999999.0, "km"),
    "FuelLevel":       (8,  0, 0, "Unsigned", 0.4,   0.0,    0.0,  100.0,   "%"),
    "CoolantTemp":     (8,  0, 1, "Unsigned", 1.0,   0.0,    0.0,  150.0,   "degC"),
    "BattTemp":        (8,  0, 1, "Unsigned", 1.0,   0.0,  -20.0,  100.0,   "degC"),
    "HV_Current":      (16, 0, 1, "Signed",   0.1,   0.0, -500.0,  500.0,   "A"),
    "HV_Voltage":      (16, 0, 1, "Unsigned", 0.1,   0.0,    0.0,  800.0,   "V"),
    "AccelPedalPos":   (8,  0, 0, "Unsigned", 0.4,   0.0,    0.0,  100.0,   "%"),
    "GearPosition":    (4,  0, 0, "Unsigned", 1.0,   0.0,    0.0,    6.0,   "",),
    "SteeringAngle":   (16, 0, 0, "Signed",   0.1,   0.0, -780.0,  780.0,   "deg"),
    "WheelSpeed_FL":   (16, 0, 0, "Unsigned", 0.01,  0.0,    0.0,  300.0,   "km/h"),
    "WheelSpeed_FR":   (16, 0, 0, "Unsigned", 0.01,  0.0,    0.0,  300.0,   "km/h"),
    "WheelSpeed_RL":   (16, 0, 0, "Unsigned", 0.01,  0.0,    0.0,  300.0,   "km/h"),
    "WheelSpeed_RR":   (16, 0, 0, "Unsigned", 0.01,  0.0,    0.0,  300.0,   "km/h"),
    "BrakePressure":   (16, 0, 0, "Unsigned", 0.01,  0.0,    0.0,  250.0,   "bar"),
    "TirePressure_FL": (8,  0, 0, "Unsigned", 0.05,  0.0,    0.0,    5.0,   "bar"),
    "TirePressure_FR": (8,  0, 0, "Unsigned", 0.05,  0.0,    0.0,    5.0,   "bar"),
    "TirePressure_RL": (8,  0, 0, "Unsigned", 0.05,  0.0,    0.0,    5.0,   "bar"),
    "TirePressure_RR": (8,  0, 0, "Unsigned", 0.05,  0.0,    0.0,    5.0,   "bar"),
    "OutsideTemp":     (8,  0, 1, "Signed",   1.0,   0.0,  -40.0,   85.0,   "degC"),
    "CruiseSpeed":     (16, 0, 0, "Unsigned", 0.01,  0.0,    0.0,  250.0,   "km/h"),
    "DoorStatus":      (4,  0, 0, "Unsigned", 1.0,   0.0,    0.0,    2.0,   ""),
    "SeatPos":         (8,  0, 0, "Unsigned", 1.0,   0.0,    0.0,  100.0,   "%"),
    "HVAC_Temp":       (8,  0, 1, "Unsigned", 0.5,   0.0,   14.0,   32.0,   "degC"),
    "FanSpeed":        (4,  0, 0, "Unsigned", 1.0,   0.0,    0.0,    7.0,   ""),
    "LampStatus":      (4,  0, 0, "Unsigned", 1.0,   0.0,    0.0,    3.0,   ""),
    "WiperStatus":     (4,  0, 0, "Unsigned", 1.0,   0.0,    0.0,    3.0,   ""),
    "HandsOnWheel":    (1,  0, 0, "Bool",     1.0,   0.0,    0.0,    1.0,   ""),
    "LaneDeparture":   (2,  0, 0, "Unsigned", 1.0,   0.0,    0.0,    3.0,   ""),
    "DriverFatigue":   (8,  0, 0, "Unsigned", 1.0,   0.0,    0.0,  100.0,   "%"),
    "RadarTargetDist": (16, 0, 0, "Unsigned", 0.01,  0.0,    0.0,  250.0,   "m"),
    "MotorTorque":     (16, 0, 0, "Signed",   0.5,   0.0, -500.0,  500.0,   "Nm"),
    "DC_DC_Status":    (4,  0, 0, "Unsigned", 1.0,   0.0,    0.0,    3.0,   ""),
    "DiagReq":         (64, 0, 0, "Unsigned", 1.0,   0.0,    0.0,    0.0,   ""),
    "DiagResp":        (64, 0, 0, "Unsigned", 1.0,   0.0,    0.0,    0.0,   ""),
    "VIN":             (96, 0, 0, "Unsigned", 1.0,   0.0,    0.0,    0.0,   ""),
    "ECUSerial":       (32, 0, 0, "Unsigned", 1.0,   0.0,    0.0,    0.0,   ""),
}

ENUM_MAP = {
    "GearPosition": {"0": "P", "1": "R", "2": "N", "3": "D", "4": "S", "5": "M"},
    "DoorStatus":   {"0": "Closed", "1": "Open", "2": "Error"},
    "LampStatus":   {"0": "Off", "1": "LowBeam", "2": "HighBeam", "3": "Fog"},
    "WiperStatus":  {"0": "Off", "1": "Int", "2": "Slow", "3": "Fast"},
    "LaneDeparture":{"0": "Off", "1": "Left", "2": "Right", "3": "Both"},
    "DC_DC_Status": {"0": "Off", "1": "On", "2": "Error", "3": "Derated"},
}

CYCLETIMES = [100, 500, 1000, 5000]

def make_signal(sig_id, name):
    L, sp, bo, vt, fac, off, mn, mx, unit = SIGNAL_DEFS[name]
    d = {
        "id": sig_id,
        "SignalName": name,
        "Length": L,
        "StartPos": sp,
        "ByteOrder": bo,
        "ValueType": vt,
        "SigSendType": False,
        "Repetitions": 0,
        "InitValue": 0,
        "Factor": fac,
        "Offset": off,
        "Min": mn,
        "Max": mx,
        "Unit": unit,
        "EnumValues": ENUM_MAP.get(name, None),
        "IsMuxor": False,
        "MuxValue": None,
        "Comment": "",
    }
    return d

def make_signals(names):
    return [make_signal(i+1, n) for i, n in enumerate(names)]

def frame_len(signals):
    bits = sum(s["Length"] for s in signals)
    return max(8, (bits + 7) // 8)

# Global DbId counter
_db_counter = 0
def next_db():
    global _db_counter
    _db_counter += 1
    return _db_counter

# ── Build messages ─────────────────────────────────────────────────────────

messages = []

# ── CAN bus configs ────────────────────────────────────────────────────────

# (bus, bus_type, base_id, count, shared_on_this, fd)
CAN_CFG = [
    # Powertrain: 10 CAN + 10 CANFD
    ("Powertrain_CAN", "CAN",   0x100, 10, ["VehicleSpeed","EngineRPM","BatterySOC","Odometer","CoolantTemp"]),
    ("Powertrain_CAN", "CANFD", 0x10E, 10, []),
    # Chassis: 20
    ("Chassis_CAN",    "CAN",   0x200, 20, ["VehicleSpeed","AmbientTemp","OutsideTemp","SteeringAngle"]),
    # Body: 20
    ("Body_CAN",       "CAN",   0x300, 20, ["BatterySOC","AmbientTemp","OutsideTemp","CoolantTemp"]),
    # Infotainment: 20
    ("Infotainment_CAN","CAN",  0x400, 20, ["EngineRPM","Odometer","SteeringAngle"]),
]

BUS_POOLS = {
    "Powertrain_CAN": ["MotorTorque","BattTemp","HV_Current","HV_Voltage","AccelPedalPos",
                       "GearPosition","CruiseSpeed","HandsOnWheel","DC_DC_Status","DoorStatus"],
    "Chassis_CAN":    ["BrakePressure","WheelSpeed_FL","WheelSpeed_FR","WheelSpeed_RL","WheelSpeed_RR",
                       "TirePressure_FL","TirePressure_FR","TirePressure_RL","TirePressure_RR",
                       "CruiseSpeed","LaneDeparture","DriverFatigue","RadarTargetDist","HandsOnWheel"],
    "Body_CAN":       ["FuelLevel","DoorStatus","SeatPos","HVAC_Temp","FanSpeed",
                       "LampStatus","WiperStatus","HV_Current","BattTemp","OutsideTemp"],
    "Infotainment_CAN":["DoorStatus","LampStatus","WiperStatus","SeatPos","HVAC_Temp",
                        "FanSpeed","DriverFatigue","CruiseSpeed","HandsOnWheel","GearPosition",
                        "RadarTargetDist","CameraObjCount","OutsideTemp","AccelPedalPos"],
}

SHARED_ID_MAP = {
    "VehicleSpeed":  0x100,
    "EngineRPM":     0x101,
    "BatterySOC":    0x108,
    "Odometer":       0x104,
    "CoolantTemp":   0x105,
    "AmbientTemp":   0x230,
    "OutsideTemp":   0x232,
    "SteeringAngle": 0x200,
}

# Fixed CAN ID per message name (so same name → same ID across all buses)
_NAME_ID_MAP = {
    "VehicleSpeed":  0x100,
    "EngineRPM":     0x101,
    "BatterySOC":    0x108,
    "Odometer":       0x104,
    "CoolantTemp":   0x105,
    "AmbientTemp":   0x230,
    "OutsideTemp":   0x232,
    "SteeringAngle": 0x200,
}
_next_can_id = 0x500  # for unique messages

def can_id_for(name):
    global _next_can_id
    if name in _NAME_ID_MAP:
        return _NAME_ID_MAP[name]
    if name not in _NAME_ID_MAP:
        _NAME_ID_MAP[name] = _next_can_id
        _next_can_id += 1
    return _NAME_ID_MAP[name]

def make_can_msgs(bus, bus_type, base_id, count, shared_names, fd=False):
    msgs = []
    used_ids = set()

    shared_list = []
    for sh_name in shared_names:
        sh_id = SHARED_ID_MAP[sh_name]
        shared_list.append((sh_id, sh_name))
        used_ids.add(sh_id)

    # Shared messages
    for sh_id, sh_name in shared_list:
        signals = make_signals([sh_name])
        cycle = 100 if sh_name in ("VehicleSpeed", "SteeringAngle") else \
                500 if sh_name in ("EngineRPM", "BatterySOC", "CoolantTemp") else \
                1000
        msgs.append({
            "DbId": next_db(),
            "MessageName": sh_name,
            "Bus": bus,
            "BusType": bus_type,
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
            "Identifier": sh_id,
            "FrameType": 1 if fd else 0,
            "Length": frame_len(signals),
            "BRS": fd,
            "signalcount": len(signals),
            "signals": signals,
            "Comment": "",
            "Node": "",
        })

    # Unique messages
    pool = BUS_POOLS.get(bus, [])
    next_id = base_id
    while next_id in used_ids:
        next_id += 1

    uniq_count = count - len(shared_list)
    for i in range(uniq_count):
        raw_name = pool[i % len(pool)] if pool else f"Msg{i}"
        sig_names = [raw_name] if raw_name in SIGNAL_DEFS else ["VehicleSpeed"]
        name = f"{raw_name}_FD" if fd and raw_name != "VehicleSpeed" else raw_name
        signals = make_signals(sig_names)
        cycle = CYCLETIMES[i % len(CYCLETIMES)]
        can_id = can_id_for(name)
        msgs.append({
            "DbId": next_db(),
            "MessageName": name,
            "Bus": bus,
            "BusType": bus_type,
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
            "FrameType": 1 if fd else 0,
            "Length": max(8, frame_len(signals)),
            "BRS": fd,
            "signalcount": len(signals),
            "signals": signals,
            "Comment": "",
            "Node": "",
        })

    return msgs

for bus, bus_type, base_id, count, shared in CAN_CFG:
    messages.extend(make_can_msgs(bus, bus_type, base_id, count, shared, fd=(bus_type == "CANFD")))

print(f"CAN messages: {len(messages)}")

# ── Ethernet: 2 buses × 20 entries (4 containers + 16 ETH_PDUs each) ──────

ETH_CFG = {
    "Vehicle_ETH": {
        "src_ip": "10.0.0.1", "dst_ip": "10.0.0.2",
        "src_port": 5000, "dst_port": 5001,
        "containers": [
            ("VehicleData",  100, ["VehicleSpeed","EngineRPM","BatterySOC","Odometer"]),
            ("ChassisData",  100, ["SteeringAngle","BrakePressure","WheelSpeed_FL","TirePressure_FL"]),
            ("ADASData",     500, ["RadarTargetDist","LaneDeparture","DriverFatigue","CruiseSpeed"]),
            ("PowertrainData",100, ["MotorTorque","AccelPedalPos","HV_Current","HV_Voltage"]),
        ],
    },
    "Diagnostic_ETH": {
        "src_ip": "10.0.1.1", "dst_ip": "10.0.1.2",
        "src_port": 5100, "dst_port": 5101,
        "containers": [
            ("DiagData",    1000, ["DiagReq","DiagResp","VIN","ECUSerial"]),
            ("FlashData",    500, ["DiagReq","DiagResp","ECUSerial","BatterySOC"]),
            ("EOLData",     5000, ["VIN","ECUSerial","Odometer","AmbientTemp"]),
            ("ConfigData",  1000, ["ECUSerial","VIN","CoolantTemp","Odometer"]),
        ],
    },
}

for bus_name, cfg in ETH_CFG.items():
    base_pdu_id = 0xAC0001 if "Vehicle" in bus_name else 0xAC0101
    for ci, (cname, ccycle, pdu_sig_names) in enumerate(cfg["containers"]):
        container_id = next_db()
        pdu_ids = list(range(base_pdu_id, base_pdu_id + len(pdu_sig_names)))
        base_pdu_id += len(pdu_sig_names)

        messages.append({
            "DbId": container_id,
            "MessageName": f"{cname}Container",
            "Bus": bus_name,
            "BusType": "ETH",
            "MessageType": 0,
            "Direction": 0,
            "RoutingType": 0,
            "TargetDbIds": None,
            "SourceDbId": None,
            "isE2E": 0,
            "SendType": "Cyclic",
            "CycleTime": ccycle,
            "CycleTimeFast": 0,
            "NrOfRepetitions": 0,
            "EtherType": 0x88B5,
            "VlanId": 0,
            "SrcMAC": "",
            "DstMAC": "",
            "SrcIP": cfg["src_ip"],
            "DstIP": cfg["dst_ip"],
            "SrcPort": cfg["src_port"],
            "DstPort": cfg["dst_port"],
            "TTL": 64,
            "IpduMEntries": pdu_ids,
            "signalcount": 0,
            "signals": [],
            "Comment": "",
            "Node": "",
        })

        for pi, pdu_name_suffix in enumerate(pdu_sig_names):
            sig_name = pdu_sig_names[pi]
            signals = make_signals([sig_name])
            pdu_id = pdu_ids[pi]
            messages.append({
                "DbId": next_db(),
                "MessageName": f"{sig_name}_PDU",
                "Bus": bus_name,
                "BusType": "ETH_PDU",
                "MessageType": 0,
                "Direction": 0,
                "RoutingType": 0,
                "TargetDbIds": None,
                "SourceDbId": None,
                "isE2E": 0,
                "SendType": "Cyclic",
                "CycleTime": ccycle,
                "CycleTimeFast": 0,
                "NrOfRepetitions": 0,
                "PduId": pdu_id,
                "ContainerDbId": container_id,
                "Length": max(8, frame_len(signals)),
                "signalcount": len(signals),
                "signals": signals,
                "Comment": "",
                "Node": "",
            })

print(f"Total messages: {len(messages)}")

# ── Signal routes ──────────────────────────────────────────────────────────

def by_name_bus(name, bus):
    for m in messages:
        if m["MessageName"] == name and m["Bus"] == bus:
            return m
    return None

routes_config = [
    ("VehicleSpeed",  "Powertrain_CAN", "Chassis_CAN"),
    ("VehicleSpeed",  "Powertrain_CAN", "Infotainment_CAN"),
    ("EngineRPM",     "Powertrain_CAN", "Infotainment_CAN"),
    ("BatterySOC",    "Powertrain_CAN", "Body_CAN"),
    ("CoolantTemp",   "Powertrain_CAN", "Body_CAN"),
    ("Odometer",      "Powertrain_CAN", "Infotainment_CAN"),
    ("AmbientTemp",   "Chassis_CAN",    "Body_CAN"),
    ("SteeringAngle", "Chassis_CAN",    "Infotainment_CAN"),
    ("OutsideTemp",   "Chassis_CAN",    "Body_CAN"),
    ("CruiseSpeed",   "Powertrain_CAN", "Chassis_CAN"),
    ("GearPosition",  "Powertrain_CAN", "Infotainment_CAN"),
]

signal_routes = []
for msg_name, src_bus, dst_bus in routes_config:
    src = by_name_bus(msg_name, src_bus)
    dst = by_name_bus(msg_name, dst_bus)
    if src and dst:
        signal_routes.append({
            "SrcDbId": src["DbId"],
            "SrcSignalId": 1,
            "DstDbId": dst["DbId"],
            "DstSignalId": 1,
        })

print(f"Signal routes: {len(signal_routes)}")

# ── Write output ───────────────────────────────────────────────────────────

db = {
    "schema_version": "1.0",
    "messages": messages,
    "signal_routes": signal_routes,
}

out_path = __file__.replace("gen_pdu_db_test.py", "pdu_db_test.json")
with open(out_path, "w") as f:
    json.dump(db, f, indent=2)

print(f"Written to {out_path}")
print(f"  Messages: {len(messages)}")
print(f"  Signal routes: {len(signal_routes)}")
