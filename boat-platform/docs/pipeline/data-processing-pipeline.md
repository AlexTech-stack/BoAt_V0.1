# Data Processing Pipeline

## Pipeline Stages

```mermaid
sequenceDiagram
    participant Plugin as Plugin (Producer)
    participant SHM as Shared Memory (iceoryx2)
    participant Router as Signal Router
    participant Bus as Event Bus
    participant Filter as Event Filter
    participant Store as Event Store
    participant Stream as gRPC Stream (Client)
    participant Replay as Replay Engine

    Plugin->>SHM: Publish signal (zero-copy)
    SHM->>Router: Notify (wait-set wake)
    Router->>Bus: Route to subscribers
    Bus->>Filter: Apply subscription filters
    Filter->>Store: Persist (async batch)
    Filter->>Stream: Stream to subscribed clients
    Replay->>Bus: Inject replayed events (deterministic)
```

## Event Ingestion

- Plugins publish on named iceoryx2 topics each tick.
- `SignalRouter` subscribes and routes by `SignalDef.consumers`.
- Throughput target: at least 1M events/sec on an 8-core system.

## Filtering

- Supported filters:
  - `signal_id`
  - `simulation_id`
  - tick range
  - tag key/value
  - value threshold
- Filters are compiled to predicates at subscription time to avoid per-event string parsing.

## Replay (ABI v8, core sink)

- `ReplayController` reads binary traces using `mmap`. Each record is a
  length-delimited `boat.v1.Frame` protobuf.
- Records are converted to `core::Frame` and transmitted through the single
  gateway `FrameSink` at original timestamps (absolute-time `timerfd`
  scheduling). The sink routes each frame by `bus_type` to the CAN/Ethernet bus
  registries; the registry's RX dispatch then delivers the frame to plugins'
  `on_frame` (tagged self-sent, so plugins can skip their own echoes).
- Determinism controls:
  - fixed RNG seed
  - fixed tick order
  - no wall-clock dependency
- Replay speeds:
  - 1x real-time
  - Nx accelerated
  - step-by-step

