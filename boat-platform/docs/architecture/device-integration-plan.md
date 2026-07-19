# Physical Device Integration — Phased Plan

> **Status: Proposal / WIP.** This document describes how to integrate non-frame
> physical electrical devices (power supplies, relays/contactors, generators,
> electronic loads, generic analog/digital I/O) into BoAt, and how those devices
> participate in replay. It is a design plan, not yet implemented.

## Context

BoAt models **buses, frames, PDUs, and signals — not devices**. The entire
abstraction stack is packet-shaped: the unified `BoatFrame`
(`sdk/cpp/include/boat/frame.h`) has a *closed* `bus_type` union
(`can/canfd/eth/tcp/pdu`); the only hardware seam, `IHalDriver`
(`src/hil/hal/hal_driver.h`), does `CanFrame` I/O; and there is currently **no**
concept of voltage, current, relay state, RPM, or analog/digital I/O anywhere in
the repo.

Restbus / SIL / HIL scenarios increasingly need those things: a plausible
KL30 (battery) / KL15 (ignition) state that gates whether the restbus transmits,
a battery voltage that feeds a simulated BMS status frame, a generator/alternator
model, or an electronic load. Today those values are static `InitValue`s in the
PDU JSON with no dynamics and no way to record or replay them.

A power supply / relay / generator is **not wire traffic**. It is:

- **Control targets** — "set 13.5 V", "open K1", "spin alternator to 2000 rpm"
- **State sources** — "measured current 4.2 A", "K1 is closed", "battery 12.8 V"

That is **named scalar/boolean I/O with setpoints and measurements** — a
different shape than a frame.

## Design principles (what we must not violate)

| Principle | How the plan honors it |
|---|---|
| **Unified frame stays frame-only** | Do **not** add a `BOAT_BUS_DEVICE` to `BoatFrame`/`Frame.BusType`. Device I/O rides the **always-on signal bus** (`BusService` / `set_bus_publisher` / `on_signal`), never the frame union. |
| **Everything is a plugin** | Every device (real or virtual) is a v8 `.so` plugin loaded via `BOAT_NODE_PLUGINS=…?{json}`. Core owns no device logic. |
| **Core stays a thin dispatcher** | The optional gRPC `DeviceService` *delegates* to a `device_manager` plugin via `PluginManager::FindService(...)`, exactly as `PduService` delegates to `pdu_router`. |
| **Determinism is the hard invariant** | Virtual device plugins are seeded + tick-driven (bit-identical). Physical device plugins are HIL-only, gated like `BOAT_HIL_ENABLED`, and excluded from `boat_determinism_seed`. |
| **Virtual/physical split = existing discipline** | Backend selection mirrors `vcan*`→`VirtualCanDriver` / else→`PhysicalCanDriver`. Replay drives **virtual** devices only; never pushes a recording back out to real bench hardware. |

**Precedent that makes this low-risk:** the `tcp` plugin
(`src/plugins/tcp/tcp_plugin.cpp`) already opens its *own* `AF_PACKET` socket +
RX thread inside the plugin, entirely outside the registry/`FrameSink`
machinery. "A plugin owns a hardware handle the core knows nothing about" is
already a blessed pattern. A device plugin owning a serial/SCPI/GPIO handle is
the same shape.

---

## Phases at a glance

| Phase | Deliverable | New surface | Invariants |
|---|---|---|---|
| **1** | `virtual_psu` + `virtual_relay` plugins & Python nodes; signal-bus naming convention; KL15 restbus demo | `on_signal` host→plugin hook (ABI v9) | frame stays frame-only; deterministic |
| **1.5** | Replay engine fix — `StartReplayFromEvents` republishes event-store records as named signals on the signal bus (not fake CAN frames) | none (fixes existing RPC) | tick-ordered; deterministic |
| **2** ✅ | `DeviceService` + `device_manager` plugin (discovery, typed control, capabilities) + SDK/CLI | `device.proto` (15th service, delegating) | core stays thin dispatcher |
| **2.5** ✅ | Bus-signal recording into the event store — closes the device record→replay loop (reuses Phase 1.5 replay) | env-gated recorder | opt-in; determinism unaffected |
| **3** ✅ | `IDeviceDriver` seam + SCPI-over-TCP driver + `scpi_device` plugin + `environment.schema.json` `devices:` block | device HAL family | virtual/physical split; physical = live-only |

Each phase is independently useful and shippable.

---

## What already works for free

Because replayed frames reach plugins through the **same registry RX dispatch as
live frames** (`CanBusRegistry::SubscribeFrame` → `PluginManager::DispatchFrame`
→ `on_frame`), any device plugin that *reacts* to bus traffic already sees
replayed traffic with zero new work:

- a virtual electronic-load that draws current based on a replayed ECU command,
- a relay toggled by a replayed KL15 frame,
- a generator whose RPM setpoint arrives on a replayed frame.

The design work below is only about **replaying recorded device *state*** (a
voltage curve, a relay timeline) that a frame trace cannot carry.

---

## Phase 1 — Devices as signals (data plane)

**Status: implemented.** See the "Phase 1 implementation" section below.

**Goal:** make devices exist, driven and observed as named signals.

**Transport:** the always-on node signal bus (`BusService` / `BusNode` /
`set_bus_publisher`). It already carries named typed values (`double`/`bool`/
`string`/`bytes`) **independent of the simulation lifecycle** — which matches
real bench hardware that is "always on."

**One small ABI addition was required.** The v8 signal bus was *publish-only*
from a plugin's perspective — the vtable's only inbound hook was `on_frame`
(frames), so a C++ device plugin could not receive a setpoint over the bus.
Phase 1 adds a single host→plugin `on_signal(ctx, name, value)` vtable slot
(the signal-bus counterpart of `on_frame`), bumping `BOAT_PLUGIN_ABI_VERSION`
to **9**. This preserves the deterministic, in-process, tick-driven device
model. Interactive/scripted control that does not need determinism is also
available via **Python `BusNode` device nodes**, which subscribe+publish on the
bus with no gateway rebuild. Both paths share the same signal contract below.

**Naming convention** (one channel = one signal):

```
psu.<id>.voltage.set     psu.<id>.voltage.meas    psu.<id>.current.meas
relay.<id>.state         (bool; e.g. relay.kl15.state, relay.kl30.state)
gen.<id>.rpm.set         gen.<id>.output_voltage.meas
```

**Plugins:**

- `virtual_psu.so` — subscribes to `.set`, models a supply (ramp/limits) in
  `on_tick`, publishes `.meas` via `set_bus_publisher`. Config:
  `virtual_psu.so?{"id":"main","channels":[...],"v_nominal":13.5}`.
- `virtual_relay.so` — subscribes to `.state` commands, models contactor
  open/close (with optional debounce), republishes settled `.state`.

**Restbus tie-in (the demo):** wire `relay.kl15.state` into
`demo/restbus_simulator.py` so toggling the ignition relay starts/stops cyclic
transmission — flip KL15 off and the restbus goes quiet, like a real vehicle.
Optionally feed `psu.main.voltage.meas` into a simulated BMS status frame's
voltage signal.

**Files:** `src/plugins/virtual_psu/`, `src/plugins/virtual_relay/` (new,
registered via `add_boat_plugin` in `src/plugins/CMakeLists.txt`);
`demo/restbus_simulator.py` (KL15 gate); reuse `sdk/python/boat/bus_node.py`.

**Reference plugins to copy:** `src/plugins/probe/probe_plugin.cpp` (config
parsing helpers `CfgStr/CfgInt`), `src/plugins/can_tp/can_tp_plugin.cpp`
(publisher stashing pattern).

### Phase 1 implementation (delivered)

ABI hook:
- `sdk/cpp/include/boat/frame.h` — `BoatSignalReceiveFn` typedef.
- `sdk/cpp/include/boat/plugin.h` — `on_signal` vtable slot; `BOAT_PLUGIN_ABI_VERSION = 9`.
- `src/core/plugin/plugin_manager.{h,cpp}` — `PluginManager::DispatchSignal(name, value)` fans a signal out to every plugin implementing `on_signal`.
- `src/gateway/grpc_gateway/main.cpp` — `signal_bus.Subscribe({}, …)` forwards numeric/bool signals to `node_manager.DispatchSignal` (subscribed before any plugin loads).

C++ plugins (deterministic, tick-driven):
- `src/plugins/virtual_psu/` — single-channel programmable DC supply. Consumes `psu.<id>.voltage.set` / `.enable` / `.load.resistance`; publishes `psu.<id>.voltage.meas` / `.current.meas` (Ohm's-law current, ramp-limited, `i_limit`-capped). Config: `{"id","v_set","v_min","v_max","ramp_v_per_tick","r_load","i_limit","enabled"}`.
- `src/plugins/virtual_relay/` — relay/contactor. Consumes `relay.<id>.set`; publishes `relay.<id>.state` (tick-counted debounce). Config: `{"id","default_closed","debounce_ticks"}`.

Python nodes (interactive, no rebuild): `demo/virtual_psu_node.py`, `demo/virtual_relay_node.py` (on `BusNode`, wall-clock ramp/debounce).

Restbus gate: `demo/restbus_simulator.py --kl15-relay kl15` subscribes `relay.kl15.state`; contact-closed configures+seeds routes, contact-open removes them.

**Run it** (gateway + plugins, then drive from Python):

```bash
GW=build/debug/src/gateway/grpc_gateway/boat_gateway
BOAT_CAN_INTERFACES=vcan0 \
  BOAT_NODE_PLUGINS='…/virtual_psu.so?{"id":"main","r_load":3.0},…/virtual_relay.so?{"id":"kl15"}' \
  $GW
# elsewhere:
python3 -c 'from boat.bus_node import BusNode; BusNode().publish("psu.main.voltage.set", 24.0)'
python3 -c 'from boat.bus_node import BusNode; BusNode().publish("relay.kl15.set", True)'
```

**Note (start order):** a subscriber that connects *after* a device published
its initial state won't see that value until the next change (plain pub/sub, no
last-value cache). For the KL15 demo, start the relay open, start the restbus
gate, then switch ignition on so the transition is captured. A low-rate state
re-announce could remove this ordering sensitivity if needed later.

---

## Phase 1.5 — Device state in replay (smallest replay win)

**Goal:** replay a device's recorded curve (voltage/current/relay timeline) into
virtual devices, using primitives that already exist.

**The fix:** `ReplayController::StartFromEvents`
(`src/replay/replay_engine/replay_engine.cpp:156`) already replays a signal's
event history from the `EventStore` — but it **repackages every event as a fake
CAN frame** (lines 173–188: `set_bus_type(CAN)`, stuffing `event.tick` into
`can_id`). Change it to **publish each event as its original named signal on the
signal bus** (`signal_bus.Publish` / the `BusService` path) instead of a synthetic
frame.

Combined with the recorder — `ui/recorder.py` already subscribes to the **Bus**
(signal) stream and writes a JSONL sidecar, so *recording* device state is
already done — this closes the record→replay loop for device signals.

**Scope:** self-contained; touches only `StartFromEvents` and its wiring in
`gateway/grpc_gateway/replay_service_impl.*`. No new proto, no trace-format
change. Lays the forwarder groundwork Phase 2.5 generalizes.

**Determinism:** events carry ticks; publishing them in tick order onto the
signal bus preserves ordering. Replay targets **virtual** device plugins only.

### Phase 1.5 implementation (delivered — engine fix only)

- `src/replay/replay_engine/replay_engine.{h,cpp}` — added a `SignalForwarder`
  (`SetSignalForwarder`) and rewrote `StartFromEvents` to run a dedicated
  `ReplaySignalLoop`: it queries the event store, sorts by tick, and replays
  each record as its **original named signal** (`signal_id` + a
  `DecodeNumericBlob`-decoded `double`) through the forwarder, honouring
  speed/pause/step and absolute-time scheduling — no more synthetic CAN frames.
- `src/gateway/grpc_gateway/main.cpp` — wired `SetSignalForwarder` →
  `signal_bus.Publish`.
- `src/tests/unit/test_replay_engine.cpp` — updated to assert the signal
  contract (name preserved, values decode exactly, tick order).

**Scope call-out (important):** this is the **engine fix only**. Investigation
found that the always-on `SignalBus` (where devices live) is **not persisted to
the event store** today — the event store is written only by the frame
`ReplayLoop`. So the fix makes event-store replay signal-shaped and correct, but
**replaying a recorded device curve end-to-end still needs a recording path**
(persisting bus signals into a trace/store). That recording half is deferred to
**Phase 2.5** (unified tick-ordered trace + formalized bus-signal recording).
The `ui/recorder.py` JSONL sidecar remains the interim capture mechanism.

---

## Phase 2 — DeviceService + device_manager plugin (control plane)

**Goal:** first-class, discoverable, typed device control — the semantics named
signals lack (enumeration, capabilities, structured setpoints).

**New proto service** `device.proto` (delegating — the 15th service):

```
ListDevices()        -> [{device_id, kind: POWER_SUPPLY|RELAY|GENERATOR|GENERIC_IO,
                          channels[], capabilities}]
SetControl(device_id, channel, value)      // setpoint / relay open-close / enable
ReadState / StreamState(device_id)         // measurements
```

**Delegation (the key architectural move):** the service impl holds no device
logic. It looks up `PluginManager::FindService("device_manager")` and forwards —
structurally identical to `PduServiceImpl` → `FindService("pdu_router")` and the
`IPduRouter` pattern (`src/core/pdu_router_interface.h`,
`src/gateway/grpc_gateway/pdu_service_impl.*`). The `device_manager` plugin
exports `boat_plugin_service_name()="device_manager"` +
`boat_plugin_service_ptr()` returning an `IDeviceManager*`.

**State mirroring:** the `device_manager` publishes all device state onto the
Phase 1 signal bus, so existing dashboards (`dashboard.py`), the recorder, and
replay observe devices for free — and CAN plugins can consume device
measurements to drive frame content (e.g. `psu.main.voltage.meas` → BMS status
frame).

**Files:** `proto/boat/v1/device.proto` (+ regenerate Python stubs via
`sdk/python/boat/stubs/generate_stubs.sh`); `src/gateway/grpc_gateway/
device_service_impl.*` (registered in `main.cpp` alongside the other services);
`src/plugins/device_manager/`; `sdk/python/boat/device_node.py` + CLI
`boat device list|set|read`.

### Phase 2 implementation (delivered)

- `proto/boat/v1/device.proto` — `DeviceService` (ListDevices / SetControl /
  ReadState / StreamState), `DeviceKind`, `DeviceInfo`, `DeviceChannel`. This is
  the **15th** gRPC service. Python stubs regenerated.
- `src/core/device_manager_interface.h` — `IDeviceManager` (the delegation
  contract), mirroring `IPduRouter`.
- `src/plugins/device_manager/` — **convention-driven** aggregator: discovers
  devices from observed `.meas`/`.state` signals via the v9 `on_signal` hook,
  seeds each device's controllable channels from a per-kind table
  (`ControlTable`), and `SetControl` publishes the derived setpoint signal via
  the bus publisher (`SetSignalFor`). Exports `IDeviceManager` via
  `boat_plugin_service_name`/`_ptr`. No config, no JSON parsing — the naming
  convention is the whole contract, so it works with any device (C++ plugin or
  Python node) that follows it.
- `src/gateway/grpc_gateway/device_service_impl.*` — thin facade delegating via
  `FindService("device_manager")`; registered in `main.cpp`.
- `sdk/python/boat/device_node.py` + `client.device`; CLI `boat device
  list|set|read` (`cli/boat_cli/device.py`).

**Design note:** discovery is by observation, so a device appears once it
publishes a measurement; controllable channels are advertised up front from the
per-kind table (and `SetControl` also works pre-discovery by inferring kind from
the id prefix). A device with no measurements stays invisible until it emits one
— acceptable for our devices, which all publish state.

**Verified end-to-end** (gateway + `device_manager` + `virtual_psu` +
`virtual_relay`): `ListDevices` discovers both devices with correct kinds and
settable/readable channel flags; `SetControl(psu.main, voltage, 24)` →
`voltage.meas` 24 V / `current.meas` 8 A (24 V / 3 Ω); `SetControl(relay.kl15,
state, 1)` → `state` 1.0; an unknown channel is rejected. All exercised through
both the Python SDK and the `boat device` CLI.

---

## Phase 2.5 — Device recording → replay (delivered)

**Goal delivered:** close the Phase 1.5 caveat so a recorded device curve
replays end-to-end. The key realization: the **event store already is a unified
tick-ordered store** — the frame `ReplayLoop` writes frame-derived events, and
Phase 1.5 replays event-store records as named signals. So the missing half was
purely *recording device signals into that store*; no new trace-file format was
required.

### Phase 2.5 implementation (delivered)

- `src/replay/bus_signal_recorder.{h,cpp}` — `BusSignalRecorder` subscribes to
  the always-on `SignalBus`, and a **background writer thread** persists each
  numeric/bool signal as an `EventRecord` (`signal_id` = name, value = 8-byte
  double blob, `tick` = elapsed / tick_duration) under a simulation-id tag, with
  an optional name-prefix filter. Signal callbacks only enqueue, so the hot
  publish path is never blocked by SQLite.
- `src/gateway/grpc_gateway/main.cpp` — env-gated wiring:
  `BOAT_RECORD_BUS_SIGNALS=<sim_id>` starts the recorder;
  `BOAT_RECORD_BUS_PREFIXES=psu.,relay.` narrows it. **Off by default**, so the
  determinism seed test and normal runs are unaffected.
- Replay reuses Phase 1.5 unchanged: `replay from-events --sim-id <tag>
  [--signal-id <name>]` republishes the recorded curve onto the signal bus.
- `src/tests/unit/test_replay_engine.cpp` — unit test for the recorder
  (filtering, numeric-only, exact value round-trip).

**Verified end-to-end:** a gateway run with recording on + `virtual_psu`
captured a 0→20 V ramp (203 rows); a second run replayed
`psu.main.voltage.meas` via `from-events`, and a `BusNode` subscriber received
all 101 values ramping 0.0 → 20.0. The record→replay loop is closed.

### Deferred (optional): single interleaved trace *file*

A combined trace-file format carrying **frames and signals interleaved in one
file** (with a `signal_map` retarget field on `StartReplayRequest`) remains a
possible future enhancement — but it is no longer on the critical path, since
the event store already provides the unified tick-ordered timeline for both.
The original design sketch:

**Trace extension:** add a signal/device record type alongside `boat.v1.Frame`
records so **one trace file carries an interleaved, tick-ordered timeline** of
frames *and* device signals.

**Trace extension:** add a signal/device record type alongside `boat.v1.Frame`
records so **one trace file carries an interleaved, tick-ordered timeline** of
frames *and* device signals.

**Replay:** add a **second forwarder** to `ReplayController` beside the existing
`EventForwarder` (`replay_engine.h:75`). Frame records → `FrameSink.Publish`
(as today); signal/device records → new signal forwarder → signal bus
(`signal_bus.Publish`). Device plugins observe replayed `.set`/`.meas` signals
exactly as they would live. Reuse `kReplayBusEventType = 9001`
(`replay_engine.h:25`) as the replay-event category for the streamed side.

**Retargeting:** add a `signal_map` (recorded name → device channel) to
`StartReplayRequest`, mirroring the existing `buses[]` channel→iface mapping —
lets a recording replay onto a different device layout.

**Recording side:** formalize `recorder.py`'s JSONL sidecar into first-class
signal records in the trace store, so record and replay use one format.

**Determinism:** the unified tick-ordered timeline keeps frame/signal ordering
deterministic, preserving the `boat_determinism_seed` guarantee.

**Files:** `proto/boat/v1/frame.proto` or trace schema (add signal record);
`src/store/trace_store/*`; `src/replay/replay_engine/replay_engine.{h,cpp}`
(second forwarder + `signal_map`); `proto/boat/v1/replay.proto`
(`signal_map` field); `ui/recorder.py`.

---

## Phase 3 — Device HAL + registry (physical bench hardware)

**Goal:** talk to real instruments, mirroring the CAN HAL/registry pattern.

- `IDeviceDriver` HAL interface under `src/hil/hal/` (setpoint/measure/enable),
  paralleling `IHalDriver`.
- `DeviceRegistry` paralleling `CanBusRegistry` (`src/hil/can_bus_registry.*`).
- Concrete backends: **SCPI over serial/LXI** (power supplies, e-loads),
  **GPIO / USB-relay boards** (relays), **analog/PWM or Modbus I/O**
  (generators, loads).
- Backend selection by config — `virtual` vs `scpi`/`gpio`/`modbus` — exactly
  like `vcan*`→virtual / else→physical.
- Extend `config/tests/environment.schema.json` (the one place buses are already
  tied to physical/virtual drivers + a DUT) with a `devices:` block:

  ```json
  "devices": {
    "psu1": {"type":"scpi","resource":"TCPIP::192.168.0.5","model":"rigol_dp832"},
    "k1":   {"type":"gpio","line":17}
  }
  ```

**Physical = live only:** physical device plugins are HIL-gated
(`BOAT_HIL_ENABLED`), excluded from determinism tests, and are **never** the
target of a replay (replay always reconstitutes into virtual device models).

### Phase 3 implementation (delivered)

Rather than a core-owned `DeviceRegistry` mirroring `CanBusRegistry`, the seam
lives **inside the device plugin** — consistent with the Phases 1–2 rule that
devices are plugins (the `tcp` plugin already owns its own socket). So a physical
device is the *same* signal-bus contract with a hardware backend swapped in
behind an `IDeviceDriver`; no new core transport, no new registry.

- `src/hil/device/device_driver.h` — `IDeviceDriver` (Open/Close/Write/Read),
  the backend seam; `line_transport.h` — `ILineTransport` (newline text wire).
- `src/hil/device/tcp_line_transport.{h,cpp}` — real TCP socket transport
  (LXI/raw-socket, port 5025), blocking with per-read poll timeout.
- `src/hil/device/scpi_device_driver.{h,cpp}` — SCPI instrument as an
  `IDeviceDriver`; channels map to SCPI command templates (`VOLT {v}`,
  `OUTP {ONOFF}`, `MEAS:VOLT?`), with `PowerSupplyDefaults()`. Transport is
  injected, so it is fully testable against an in-process mock.
- `src/plugins/scpi_device/` — v9 plugin wrapping `ScpiDeviceDriver` behind the
  signal contract; **owns its own worker thread** so slow/unreachable-instrument
  I/O never stalls the gateway tick loop. Config `{"id","host","port","poll_ms"}`.
  Without a reachable instrument it simply idles (live-only).
- `config/tests/environment.schema.json` — `devices:` block (`DeviceConfig`:
  `type` ∈ virtual/scpi/gpio/modbus, `kind`, `host`/`port`/`resource`/`poll_ms`),
  mirroring the bus `virtual`/`physical` split.
- `src/tests/unit/test_scpi_device.cpp` — drives `ScpiDeviceDriver` over a **real
  loopback socket** against an in-process mock SCPI PSU (no hardware).

**Verified end-to-end** (no hardware): a Python mock SCPI PSU + gateway
with `scpi_device` + `device_manager` — `boat device set psu.main voltage 24`
drove `VOLT 24` over SCPI and read back `voltage=24 V`, `current=8 A`, through
the identical DeviceService/CLI path used by the virtual devices.

### Post-phase follow-ups (delivered)

- **`virtual_generator`** (`src/plugins/virtual_generator/`) — a deterministic
  alternator: rpm setpoint → regulated output voltage (+ load current),
  completing the PSU/relay/generator trio device_manager already modelled.
- **`environment.schema.json` `devices:` consumer** — `EnvironmentConfig`
  (`sdk/python/boat/test/config.py`) parses the block, and
  `node_plugin_specs()` translates each device into a `BOAT_NODE_PLUGINS` entry
  (prepending `device_manager`); `TestHarness.start()` sets it. Declaring a
  device in an env config is now enough for the harness to launch it.
- Still open: `gpio`/`modbus` `IDeviceDriver` backends (the seam exists; SCPI is
  the only concrete backend so far).

---

## Verification

**Build & unit (per phase):**

```bash
cd boat-platform
cmake --preset debug && cmake --build --preset debug
ctest --preset release --output-on-failure
```

- **Phase 1:** load `virtual_psu.so`/`virtual_relay.so` via `BOAT_NODE_PLUGINS`;
  `boat` bus subscribe shows `.meas` signals; drive `.set` and observe response.
  Run the KL15 restbus demo: toggle `relay.kl15.state` and confirm cyclic TX
  starts/stops.
- **Phase 1.5:** record a device signal via `recorder.py`, replay it with the
  fixed `StartReplayFromEvents`, confirm the named signal reappears on the bus
  and a subscribed `virtual_psu` reproduces the curve.
- **Phase 2:** `boat device list` enumerates devices; `boat device set` changes a
  setpoint; state visible in `dashboard.py`.
- **Phase 2.5:** record a mixed frame+device session, replay it, confirm frames
  and device signals arrive in the original tick order.
- **Determinism (all phases):** `ctest -R boat_determinism_seed` must stay
  bit-identical with virtual devices loaded.
- **Phase 3:** run under `BOAT_HIL_ENABLED=1` against a real (or bench-simulated)
  instrument; confirm physical device excluded from determinism/replay-to-hardware.
