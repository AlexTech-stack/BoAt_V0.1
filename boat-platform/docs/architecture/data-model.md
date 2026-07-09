# Data Model

## Core Entities

```text
SimulationInstance
  id: UUID
  scenario_id: UUID
  state: ENUM(IDLE|RUNNING|PAUSED|STOPPED|ERROR)
  created_at: Timestamp
  config: SimConfig

Scenario
  id: UUID
  name: String
  version: SemVer
  description: String
  plugins: [PluginRef]
  signals: [SignalDef]
  fault_schedule: [FaultEvent]
  duration_ticks: uint64

Signal
  id: UUID
  name: String
  type: ENUM(FLOAT64|INT64|BOOL|BYTES|STRING)
  unit: String
  source_plugin: PluginRef
  consumers: [PluginRef]

Event
  id: UUID
  simulation_id: UUID
  tick: uint64
  wall_time: Timestamp
  signal_id: UUID
  value: oneof(float64|int64|bool|bytes|string)
  tags: map<string,string>

Trace
  id: UUID
  simulation_id: UUID
  start_tick: uint64
  end_tick: uint64
  format: ENUM(BINARY|MF4|CSV)
  storage_path: String

Plugin
  id: UUID
  name: String
  version: SemVer
  so_path: String
  config_schema: JSONSchema
  capabilities: [ENUM]

FaultEvent
  tick: uint64
  target_signal: UUID
  fault_type: ENUM(STUCK|NOISE|DROPOUT|INVERT)
  parameters: map<string,string>

Frame  (ABI v8 unified frame — src/core Frame / boat.v1.Frame / BoatFrame)
  bus_type: ENUM(CAN|CANFD|ETH|PDU|TCP)   # single type for all buses
  iface: String                            # e.g. vcan0, can1, eth0
  timestamp_ns: uint64
  payload: bytes
  meta: oneof(CanMeta|EthMeta)             # can_id/dlc/flags OR macs/ips/ethertype/flags
```

The `Frame` entity replaces the pre-v8 `BoatCanFrame` / `BoatEthFrame` split.
It is the unit exchanged across the plugin ABI (`on_frame` /
`set_frame_publisher`), persisted in traces, and streamed by `FrameService`.

## Storage Mapping

- `SimulationInstance` maps to `simulations` table.
- `Scenario` maps to `scenarios` table.
- `Event` maps to `events` table.
- `Trace` maps to `traces` table.
- `Plugin`, `Signal`, and `FaultEvent` are embedded in scenario definitions and/or plugin registry metadata.

## Trace format (ABI v8)

- Binary traces are a stream of length-delimited `boat.v1.Frame` protobuf
  records: `uint32 len` (4 bytes) followed by the serialized `Frame`.
- `Frame` carries bus-agnostic metadata (`bus_type`, `iface`, `timestamp_ns`,
  `payload`) plus CAN or Ethernet specifics, so a single trace can hold mixed
  CAN/CAN-FD/Ethernet traffic. Import converts `.asc` / `.blf` / `.pcap` into
  this format.

## HIL Event Type Registration

- CAN frame events published by `HilBridge` use `BusEvent::type = 0xCA1F0001` (`kEventTypeCanFrame`).

