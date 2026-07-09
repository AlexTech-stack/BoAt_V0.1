# DBC2BoatJSON HOWTO

Convert industry-standard CAN DBC files to BoAt's PDU database JSON format
for use with the gateway, CLI, and PDU editor.

## Overview

`tools/dbc2boatjson.py` parses a `.dbc` file and produces a valid
`pdu_db.json` that can be loaded by `PduDatabase`, the `boat-cli`,
and the PDU editor. The tool is **self-contained** — no external
DBC parsing library required.

Supported DBC elements:

| Element | Maps to |
|---------|---------|
| `BO_` message | `Identifier`, `MessageName`, `Length`, `Node` (sender) |
| `SG_` signal | `SignalName`, `StartPos`, `Length`, `ByteOrder`, `ValueType`, `Factor`, `Offset`, `Min`, `Max`, `Unit` |
| `SG_ M` / `SG_ mN` | `IsMuxor`, `MuxValue` |
| `VAL_` | `EnumValues` |
| `CM_` | `Comment` on messages and signals |
| `BA_ "GenMsgCycleTime"` | `CycleTime` + `SendType` (Cyclic if >0, Spontaneous if 0/missing) |

## Quick start

```bash
# Convert a DBC file to BoAt JSON
python3 tools/dbc2boatjson.py boat-platform/config/pdu_db.schema.json input.dbc output.json
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--bus` | `CAN` | Bus name assigned to all messages |
| `--bus-type` | `CAN` | `CAN` or `CANFD` |
| `--node` | `""` | Default sender node (used when BO_ has no sender) |
| `--start-id` | `1` | First `DbId` to assign |
| `--default-cycle-ms` | `0` | Default cycle time for messages without BA_ or name-suffix timing (0 = Spontaneous) |
| `--validate` | — | Validate the generated output against `pdu_db.schema.json` (requires `jsonschema`) |

### Examples

```bash
# Convert with custom bus and FD flag
python3 tools/dbc2boatjson.py \
    boat-platform/config/pdu_db.schema.json \
    my_vehicle.dbc \
    my_vehicle.json \
    --bus "Powertrain_CAN" \
    --bus-type CANFD

# Apply a default 100ms cycle time to all messages without explicit timing
python3 tools/dbc2boatjson.py \
    boat-platform/config/pdu_db.schema.json \
    my_vehicle.dbc \
    my_vehicle.json \
    --default-cycle-ms 100

# Validate the output against the schema (requires jsonschema)
python3 tools/dbc2boatjson.py \
    boat-platform/config/pdu_db.schema.json \
    input.dbc output.json \
    --validate
```

## What happens during conversion

### Messages

Each `BO_` line becomes one message entry:

```
BO_ 36 KINEMATICS: 8 XXX
```
→
```json
{
  "DbId": 1,
  "MessageName": "KINEMATICS",
  "Identifier": 36,
  "Length": 8,
  "FrameType": 0,
  "SendType": "Spontaneous",
  "CycleTime": 0,
  "Node": "XXX"
}
```

- **FrameType**: `1` (29-bit) if `Identifier > 0x7FF`, otherwise `0` (11-bit)
- **SendType** / **CycleTime**: determined by the first matching rule:
  1. `BA_ "GenMsgCycleTime"` if present and `> 0` → `Cyclic`
  2. Message name suffix `_<N>ms` (e.g. `ESC_02_10ms`) → `Cyclic` with N ms
  3. `--default-cycle-ms` if set and `> 0` → `Cyclic`
  4. Otherwise → `Spontaneous` with `CycleTime: 0`
- **Node**: from the `BO_` sender field (e.g. `XXX`, `EPS`, `HCU`)

### Signals

Each `SG_` line becomes one signal entry:

```
SG_ ENGINE_RPM : 7|16@0+ (1,0) [0|65535] "rpm" XXX
```
→
```json
{
  "id": 1,
  "SignalName": "ENGINE_RPM",
  "Length": 16,
  "StartPos": 7,
  "ByteOrder": 1,
  "ValueType": "Unsigned",
  "Factor": 1.0,
  "Offset": 0.0,
  "Min": 0.0,
  "Max": 65535.0,
  "Unit": "rpm"
}
```

**Byte order mapping:**

| DBC | BoAt |
|-----|------|
| `@0` (Motorola) | `ByteOrder: 1` |
| `@1` (Intel) | `ByteOrder: 0` |

**ValueType mapping:**

| DBC sign + size | BoAt ValueType |
|----------------|----------------|
| `+`, size 1 | `"Bool"` |
| `-` | `"Signed"` |
| `+`, size > 1 | `"Unsigned"` |

**Min/Max**: converted from raw to physical via `raw × factor + offset`.

### Multiplexed signals

```
SG_ MUX M : 1|2@0+ (1,0) [0|3] "" XXX
SG_ SIG_0 m0 : 15|8@0+ (1,0) [0|255] "" XXX
SG_ SIG_1 m1 : 15|8@0+ (1,0) [0|255] "" XXX
```
→
```json
{ "SignalName": "MUX",   "IsMuxor": true,  "MuxValue": null,  "StartPos": 1, "Length": 2 }
{ "SignalName": "SIG_0", "IsMuxor": false, "MuxValue": 0,     "StartPos": 15, "Length": 8 }
{ "SignalName": "SIG_1", "IsMuxor": false, "MuxValue": 1,     "StartPos": 15, "Length": 8 }
```

Note that `SIG_0` and `SIG_1` share bit position 15 — the active signal
depends on the value of the multiplexor `MUX`.

### Value descriptions (enums)

```
VAL_ 295 GEAR 0 "P" 1 "R" 2 "N" 3 "D" 4 "B" ;
```
→
```json
{ "EnumValues": { "0": "P", "1": "R", "2": "N", "3": "D", "4": "B" } }
```

### Comments

```
CM_ SG_ 36 ACCEL_Y "unit is tbd";
```
→
```json
{ "SignalName": "ACCEL_Y", "Comment": "unit is tbd" }
```

## Working with the output

Once you have a `pdu_db.json`, you can use it with any BoAt tool:

```bash
# Inspect the database
boat db list --db my_vehicle.json
boat db show --msg "KINEMATICS" --db my_vehicle.json

# Edit in the PDU editor
python3 tools/pdu_editor.py

# Use in Python
from boat.pdu_db import PduDatabase
from boat.message import Message

db = PduDatabase("my_vehicle.json")
msg = Message(db.by_name_and_bus("KINEMATICS", "CAN"))
msg.set("SPEED", 50.0)
payload = msg.pack()
```

## Schema validation

Validation is **off by default**. Pass `--validate` to check the generated
JSON against `pdu_db.schema.json`. Requires `jsonschema`:

```bash
pip install jsonschema
python3 tools/dbc2boatjson.py schema.json input.dbc output.json --validate
```

Validation errors are printed to stderr and the tool exits with code 1.
