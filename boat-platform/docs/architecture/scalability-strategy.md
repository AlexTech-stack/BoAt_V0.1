# Scalability Strategy

## Multi-Core Scaling

- `TickScheduler` uses a work-stealing thread pool with one thread per logical core.
- Plugins can be pinned with CPU affinity via `pthread_setaffinity_np`.
- Signal routing uses lock-free SPSC queues between plugin workers and router workers.
- Event store writer thread is isolated on a dedicated core to reduce contention.

## Shared Memory Strategy

- iceoryx2 `ServiceBuilder` creates named services for publishers/subscribers.
- Zero-copy flow: publisher loans a chunk, writes in-place, sends handle, subscriber reads directly.
- Pre-allocated payload pools by class:
  - 64 B
  - 512 B
  - 4 KB
  - 64 KB
  - 1 MB

## Distributed Simulation (Future)

- Phase 2 target: federated simulation through HLA or DDS bridge.
- Each node runs a `boat-agent`.
- A `boat-coordinator` manages global tick synchronization.
- Inter-node coordination via gRPC bidirectional streaming.

## Memory Strategy

- Hot path avoids dynamic allocations; pool allocators only in simulation loop.
- Trace writer can use `O_DIRECT` with aligned buffers to reduce page cache pressure.
- Optional strict plugin sandbox mode runs plugins in separate processes with shared memory data channels and UDS control channels.

