# Test Strategy

## Test Pyramid

```text
         ┌──────────────┐
         │   E2E / HIL  │  (few, slow, hardware-dependent)
         ├──────────────┤
         │ Integration  │  (moderate, Docker-based)
         ├──────────────┤
         │  Unit Tests  │  (many, fast, isolated)
         └──────────────┘
```

## Unit Tests (C++ / Catch2)

- `core/scheduler`: tick ordering, clock accuracy, pause/resume
- `core/signal`: routing correctness, filter predicates
- `core/determinism`: RNG reproducibility, tick-order stability
- `core/fault`: fault timing and signal corruption behavior
- `store/event_store`: insert/query correctness and batch performance
- `ipc/shm`: zero-copy publish/subscribe round-trip validation

## Unit Tests (Python / pytest)

- `boat-py` SDK: gRPC stubs and scenario builder APIs
- `boat-cli`: command parsing and output formatting
- `boat-ai`: prompt construction and response parsing

## Integration Tests

- Full lifecycle over gRPC: create -> start -> run 1000 ticks -> stop -> query events
- Plugin load/unload while simulation is running
- Replay determinism verification by event stream comparison
- Multi-client concurrent signal subscriptions
- Fault injection path with verification in event store

## Determinism Validation

- Dedicated CI job runs the same scenario twice with the same seed.
- Binary traces are compared; output must be bit-identical.
- Fuzz seed sweep runs 100 seeds and checks divergence anomalies.

## HIL Testing

- HIL suite located in `tests/hil/`.
- Tests are skipped in CI by default unless `BOAT_HIL_ENABLED=1`.
- Uses `vcan0` for CI-compatible HIL smoke paths.
- Full hardware tests run on dedicated lab runners.

## AI-Assisted Test Generation

- `boat-ai` can generate scenario YAML from natural language.
- Generated scenarios are validated through `ScenarioService.ValidateScenario`.
- Anomaly detection flags deviations from learned normal signal distributions.

