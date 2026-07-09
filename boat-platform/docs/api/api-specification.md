# API Specification

All protobuf service files are defined under `proto/boat/v1/`, package `boat.v1`.
14 gRPC services across 16 `.proto` files.

### `simulation.proto` — SimulationService (9 RPCs)

| Method | Type | Description |
|---|---|---|
| `CreateSimulation` | Unary | Create instance from scenario |
| `StartSimulation` | Unary | Transition to RUNNING |
| `PauseSimulation` | Unary | Transition to PAUSED |
| `StepSimulation` | Unary | Advance N ticks (PAUSED only) |
| `ResetSimulation` | Unary | Reset to tick 0 |
| `StopSimulation` | Unary | Terminate instance |
| `GetSimulationState` | Unary | Query current state |
| `WatchSimulation` | Server-streaming | Live state change events |
| `ListSimulations` | Unary | Paginated list |

### `signal.proto` — SignalService (3 RPCs)

| Method | Type | Description |
|---|---|---|
| `InjectSignal` | Unary | Override signal value at next tick |
| `SubscribeSignals` | Server-streaming | Stream signal values by filter |
| `GetSignalHistory` | Unary | Query historical values (paginated) |

### `scenario.proto` — ScenarioService (5 RPCs)

| Method | Type | Description |
|---|---|---|
| `CreateScenario` | Unary | Register new scenario |
| `GetScenario` | Unary | Fetch scenario definition |
| `ListScenarios` | Unary | Paginated list |
| `ValidateScenario` | Unary | Dry-run validation |
| `DeleteScenario` | Unary | Remove scenario |

### `replay.proto` — ReplayService (8 RPCs)

| Method | Type | Description |
|---|---|---|
| `StartReplay` | Unary | Begin deterministic replay from trace |
| `SeekReplay` | Unary | Jump to tick N |
| `StreamReplay` | Server-streaming | Stream replayed events |
| `PauseReplay` | Unary | Pause active replay |
| `ResumeReplay` | Unary | Resume paused replay |
| `StopReplay` | Unary | Terminate replay session |
| `ImportTraceData` | Unary | Import raw trace data for replay |
| `StartReplayFromEvents` | Unary | Start replay from in-memory events |

Replay session identity: `StartReplay` returns `ReplayControlResponse.replay_id`. Clients pass this to all subsequent replay RPCs.

### `plugin.proto` — PluginService (4 RPCs)

| Method | Type | Description |
|---|---|---|
| `RegisterPlugin` | Unary | Register plugin .so (optional `config_json` field for configuration) |
| `ListPlugins` | Unary | List available plugins |
| `GetPluginInfo` | Unary | Fetch plugin metadata |
| `UnloadPlugin` | Unary | Hot-unload plugin |

### `metrics.proto` — MetricsService (2 RPCs)

| Method | Type | Description |
|---|---|---|
| `GetMetrics` | Unary | Snapshot of current metrics |
| `StreamMetrics` | Server-streaming | Live metrics stream |

### `trace.proto` — TraceService (4 RPCs)

| Method | Type | Description |
|---|---|---|
| `GetTrace` | Unary | Fetch trace events by id |
| `ListTraces` | Unary | Paginated trace listing |
| `StreamTrace` | Server-streaming | Live trace stream |
| `MarkStep` | Unary | Record a step marker in the trace |

### `fault.proto` — FaultService (2 RPCs)

| Method | Type | Description |
|---|---|---|
| `InjectFault` | Unary | Schedule fault injection for simulation |
| `ListFaults` | Unary | Paginated fault event listing |

### `frame.proto` — FrameService (2 RPCs)

| Method | Type | Description |
|---|---|---|
| `SendFrame` | Unary | Transmit a unified BoatFrame (CAN, CANFD, Ethernet, TCP, PDU) |
| `SubscribeFrames` | Server-streaming | Stream incoming frames by bus type filter |

Unified frame send/subscribe endpoint that replaces the older `CanService` and `EthernetService` for new development. The `Frame` message carries a `bus_type` discriminator (CAN, CANFD, ETHERNET, TCP, PDU) and per-bus metadata in a `oneof` block.

### `can.proto` — CanService (3 RPCs)

| Method | Type | Description |
|---|---|---|
| `SendCanFrame` | Unary | Transmit a CAN/CAN FD frame on a registered interface |
| `SubscribeCanFrames` | Server-streaming | Stream incoming CAN frames (with optional interface filter) |
| `ListBuses` | Unary | List registered CAN interfaces with metadata (driver, state, FD support, bitrate) |

### `ethernet.proto` — EthernetService (3 RPCs)

| Method | Type | Description |
|---|---|---|
| `SendFrame` | Unary | Transmit an Ethernet frame |
| `SubscribeFrames` | Server-streaming | Stream incoming Ethernet frames |
| `ListInterfaces` | Unary | List registered Ethernet interfaces |

### `bus.proto` — BusService (2 RPCs)

| Method | Type | Description |
|---|---|---|
| `Publish` | Unary | Publish a named value on the always-on node signal bus |
| `Subscribe` | Server-streaming | Subscribe to bus signals by filter |

### `pdu.proto` — PduService (10 RPCs)

| Method | Type | Description |
|---|---|---|
| `SendPdu` | Unary | Transmit a PDU on a configured route |
| `SubscribePdus` | Server-streaming | Stream incoming PDUs |
| `ConfigureRoute` | Unary | Create or update a PDU route with transmission schedule |
| `ListRoutes` | Unary | List configured PDU routes |
| `ConfigureContainer` | Unary | Configure IpduM container parameters |
| `ConfigureGroup` | Unary | Create or update an I-PDU group |
| `EnableGroup` | Unary | Enable a PDU group (allow traffic) |
| `DisableGroup` | Unary | Disable a PDU group (silence traffic) |
| `ListGroups` | Unary | List configured PDU groups |
| `RemoveRoute` | Unary | Remove a PDU route |

### `debug.proto` — DebugService (1 RPC)

| Method | Type | Description |
|---|---|---|
| `StreamEvents` | Server-streaming | Stream internal RPC events for debugging |

## Proto files without services

- `common.proto` — Shared message types (PaginationRequest, UUID, etc.)
- `control.proto` — Control messages (StartCommand, etc.)

## API Versioning Strategy

- Package versions follow `boat.v1`, `boat.v2`, where breaking changes increment major.
- Non-breaking additions (new fields or RPCs) stay in the same major package.
- Deprecated fields are marked with `[deprecated = true]` and removed after two major versions.
- Every request carries header `x-boat-api-version`.

## Error Handling Model

- Core gRPC status codes: `NOT_FOUND`, `INVALID_ARGUMENT`, `FAILED_PRECONDITION`, `INTERNAL`, `UNAVAILABLE`.
- Error payloads use `google.rpc.Status`.
- `details` includes `ErrorInfo` with `domain`, `reason`, and metadata.
- Invalid state transition operations return `FAILED_PRECONDITION` and include current state metadata.
