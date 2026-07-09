# Tick Execution Pipeline

Every simulation tick follows this strict order:

```
BeforeTick(seed ⊕ tick)
    ↓
EventBus::Dispatch()
    ↓
PluginManager::TickAll(tick)    [sorted by .so path]
    ↓
SimClock::Step()
```

This is defined in `TickScheduler::ExecuteTick()` and
`TickScheduler::ExecuteTickSynchronously()` at `src/core/scheduler/tick_scheduler.cpp`.

## Determinism contract

### RNG seeding

`DeterminismEngine::BeforeTick(tick)` reseeds `std::mt19937_64` with
`seed_ ⊕ tick`.  This guarantees that the same (seed, tick) pair
produces the same `NextRandom()` output on every run and every host.

### Container ordering

All iterable containers that affect deterministic tick output must use
deterministic ordering:

| Container | Type | Order | Rationale |
|---|---|---|---|
| `PluginManager::plugins_` | `std::map` | `.so` path (alphabetical) | `TickAll()` iterates plugins in this order; every run must tick plugins identically |
| `EventBus::subscribers_` | `std::unordered_map` | — | Lookup-only by event type; never iterated for dispatch |
| `SignalBus::subscriptions_` | `std::unordered_map` | — | Lookup-only by subscription ID; signal_index_ is iterated per-signal |
| `TickScheduler::local_queues_` | `std::deque` | FIFO | Worker tasks drain in submission order; intended for barrier only |
| `ReplayController` trace scan | linear forward scan | tick order | Replay reads trace records sequentially |

### Thread safety restrictions

The following components are NOT thread-safe and must only be called
from the coordinator thread (the thread running `ExecuteTick`):

- `DeterminismEngine::BeforeTick` / `NextRandom`
- `SimClock::Step` / `tick`
- `EventBus::Dispatch`

The following components ARE thread-safe and may be called from any
thread:

- `EventBus::Publish` / `Subscribe` / `Unsubscribe`
- `SignalBus::Subscribe` / `Unsubscribe` / `Publish`
- `PluginManager::Load` / `Unload` / `TickAll` (via snapshot pattern)

## Floating-point reproducibility

If cross-platform determinism is required (e.g. running the same
simulation on Windows and Linux):

- All plugins must use deterministic floating-point modes:
  `/fp:strict` on MSVC, `-frounding-math` on GCC/Clang.
- Avoid trigonometric and transcendental functions (`sin`, `cos`,
  `exp`, `log`) whose implementations differ across libm versions.
- Prefer fixed-point arithmetic or deterministic polynomial
  approximations for critical paths.

The core simulation engine (`DeterminismEngine`, `SimClock`,
`EventBus`) does not perform any floating-point operations — it is
trivially reproducible.

## Adding new pipeline steps

Any new per-tick operation MUST be added as a named step in the
pipeline.  Do not add side-effect threads that modify simulation
state outside this sequence.

If the new step must run before or after an existing step, add it to
both `ExecuteTick()` and `ExecuteTickSynchronously()` in the same
relative position.

## History

| Date | Change |
|---|---|
| 2026-06 | Created as part of core-hardening Phase 5 — formalized the pipeline contract that was implicit in the original scheduler design |
