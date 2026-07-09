# Sequence Diagrams

## 1) Simulation Lifecycle (create -> start -> tick loop -> stop)

```mermaid
sequenceDiagram
    participant Client
    participant Gateway as API Gateway
    participant Agent as boat-agent
    participant Core as Simulation Core
    participant Store as Event Store

    Client->>Gateway: CreateSimulation
    Gateway->>Agent: UDS Create
    Agent->>Core: Instantiate SimulationInstance
    Core-->>Gateway: simulation_id
    Gateway-->>Client: Created

    Client->>Gateway: StartSimulation
    Gateway->>Agent: UDS START
    Agent->>Core: Transition to RUNNING
    loop Tick Loop
        Core->>Core: Advance tick
        Core->>Store: Persist events (async)
    end
    Client->>Gateway: StopSimulation
    Gateway->>Agent: UDS STOP
    Agent->>Core: Transition to STOPPED
```

## 2) Plugin Hot-Load During Running Simulation

```mermaid
sequenceDiagram
    participant Client
    participant Gateway
    participant PluginSvc as PluginService
    participant PM as PluginManager
    participant Core as Simulation Core

    Client->>Gateway: RegisterPlugin(path)
    Gateway->>PluginSvc: RegisterPlugin
    PluginSvc->>PM: Validate ABI + load via dlopen
    PM->>Core: Attach plugin lifecycle hooks
    Core-->>PM: Plugin initialized at safe tick boundary
    PM-->>Client: Plugin registered and active
```

## 3) Signal Subscription and Streaming to gRPC Client

```mermaid
sequenceDiagram
    participant Client
    participant Gateway
    participant SignalSvc as SignalService
    participant Router as SignalRouter
    participant SHM as iceoryx2 SHM

    Client->>Gateway: SubscribeSignals(filter)
    Gateway->>SignalSvc: Open stream
    SignalSvc->>Router: Register compiled predicate
    SHM->>Router: Publish signal payload
    Router->>SignalSvc: Matched events
    SignalSvc-->>Client: StreamSignalValue
```

## 4) Deterministic Replay Flow (ABI v8, core sink)

Replay no longer injects events directly into the core, nor through a forwarder
plugin. It parses trace records into `core::Frame` and transmits each through the
single `FrameSink`, which routes to the bus registries. The registry's RX
dispatch then delivers replayed frames to plugins' `on_frame`.

```mermaid
sequenceDiagram
    participant Client
    participant ReplaySvc as ReplayService
    participant Replay as ReplayController
    participant Store as TraceStore
    participant Sink as FrameSink
    participant Reg as Can/Eth Registry
    participant Bus as Hardware Bus

    Client->>ReplaySvc: StartReplay(trace_id)
    ReplaySvc->>Replay: Initialize replay context
    Replay->>Store: mmap(trace_file)
    loop Replay ticks (timerfd-scheduled)
        Store-->>Replay: next boat.v1.Frame record
        Replay->>Replay: ProtoToCoreFrame() -> core::Frame
        Replay->>Sink: Publish(core::Frame)
        Sink->>Reg: SendFrame(iface) by bus_type
        Reg->>Bus: write frame (vcan0 / can1 / eth0)
        Reg-->>Reg: DispatchRx(self-sent) -> plugins' on_frame
    end
    Replay-->>ReplaySvc: ConsumeEvents()
    ReplaySvc-->>Client: StreamReplay events
```

## 5) Fault Injection Sequence

```mermaid
sequenceDiagram
    participant Client
    participant Gateway
    participant FaultSvc as FaultService
    participant Core as Simulation Core
    participant FI as FaultInjector
    participant Store as EventStore

    Client->>Gateway: InjectFault(target, type, tick)
    Gateway->>FaultSvc: InjectFault
    FaultSvc->>Core: Schedule fault event
    Core->>FI: Apply fault at target tick
    FI->>Core: Corrupted signal emitted
    Core->>Store: Persist fault-tagged event
```

