# IPC Architecture

## IPC Strategy Matrix

| Channel | Technology | Use Case | Latency Target |
|---|---|---|---|
| High-throughput data | Eclipse iceoryx2 (shared memory) | Signal/sensor data between plugins | < 1 us |
| Control plane | Unix Domain Sockets (UDS) | Lifecycle commands, config push | < 100 us |
| External API | gRPC over UDS (local) / TCP+TLS (remote) | CLI, dashboard, CI runners | < 5 ms |
| Event streaming | gRPC server-side streaming | Live trace streaming to clients | < 10 ms |
| Replay | Memory-mapped files (`mmap`) | Deterministic event replay | N/A |

## iceoryx2 Integration

- `ShmPublisher<T>` wraps `iox2::Publisher` for zero-copy publish on named topics.
- `ShmSubscriber<T>` wraps `iox2::Subscriber` with wait-set integration.
- Channel routing is centralized in `ipc/IpcChannelSelector`: payloads `>= 4 KB` use shared memory, payloads `< 4 KB` use UDS.
- **Runtime dispatch:** `UdsClient::SendMessage` calls `IpcChannelSelector::SelectChannel()` on `payload_bytes` size, then `PrepareOutboundUdsControlPayload` in `ipc/ipc_payload_dispatch.cpp`. Inline payloads stay on the UDS frame; large payloads are published as `ShmPayloadSample` on an instance-scoped SHM topic derived from the resolved socket path: `LargeControlPayloadShmTopicForSocket(resolved_socket_path)` -> `boat/ipc/uds_control_payload_<instance_id>`. For SHM transport, the dispatcher generates a random non-zero `shm_payload_token`, stores it in `ShmPayloadSample.payload_token`, sets `UdsControlMessage.shm_payload_topic` and `UdsControlMessage.shm_payload_token`, and clears `payload_bytes` on the wire.
- **Inbound restoration semantics:** `UdsServer::Start` resolves/normalizes the socket path, derives the same instance-scoped topic, and opens a long-lived `ShmSubscriber<ShmPayloadSample>` for that topic. Incoming samples are indexed by `payload_token` (`large_payload_by_token_`) with FIFO order tracking (`large_payload_order_`) and bounded queue eviction (`kMaxQueued`). During request handling, `ResolveInboundUdsControlPayload` validates topic/token, waits for the exact token via `DequeueLargePayloadShm(token, ...)`, restores `payload_bytes`, and clears `shm_payload_topic`/`shm_payload_token` before invoking the command handler so handlers always receive a single reconstructed inline payload view.
- Topic naming convention: `boat/<scenario_id>/<signal_name>`.

## Control Channel (UDS)

- Each simulation instance exposes a UDS socket at `/run/boat/<instance_id>.sock`.
- UDS setup paths normalize provided instance/socket values to `/run/boat/<instance_id>.sock`; absolute non-`/run/boat` paths are accepted unchanged as a fallback for custom deployments.
- Protocol uses length-prefixed protobuf messages shared with gRPC models.
- Supported commands:
  - `START`
  - `PAUSE`
  - `STEP`
  - `RESET`
  - `STOP`
  - `INJECT_FAULT`
  - `QUERY_STATE`

