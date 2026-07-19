# Devices HOWTO — power supplies, relays, generators

Integrate and drive **electrical devices** — programmable DC power supplies,
relays/contactors, generators, electronic loads, generic analog/digital I/O —
in BoAt. Devices are modelled as **named signals on the always-on signal bus**
and controlled through the `DeviceService` (`boat device …`), backed by either
deterministic **virtual** models or real **SCPI** hardware.

See also the architecture write-up:
[`docs/architecture/device-integration-plan.md`](../architecture/device-integration-plan.md).

## The model in one paragraph

A device is **not** a new bus type. It lives as a set of named signals following
the convention `<kind>.<id>.<channel>[.<role>]`, e.g. `psu.main.voltage.set`
(a setpoint) and `psu.main.voltage.meas` (a measurement). Device plugins receive
setpoints via the v9 `on_signal` hook and publish measurements via the bus
publisher. The `device_manager` plugin aggregates these into a device-shaped
view, and the gRPC `DeviceService` (delegating to it, like `PduService` →
`pdu_router`) exposes `ListDevices` / `SetControl` / `ReadState` / `StreamState`.

## Quick start (virtual power supply + relay)

```bash
cd boat-platform
cmake --preset debug && cmake --build --preset debug

GW=build/debug/src/gateway/grpc_gateway/boat_gateway
P=build/debug/src/plugins

# Devices are signal-domain — no CAN interface is required.
BOAT_CAN_INTERFACES="" \
BOAT_NODE_PLUGINS="$P/device_manager/device_manager.so,\
$P/virtual_psu/virtual_psu.so?{\"id\":\"main\",\"r_load\":3.0},\
$P/virtual_relay/virtual_relay.so?{\"id\":\"kl15\"}" \
  $GW
```

In another shell:

```bash
boat device list
# psu.main    power_supply   voltage*=13.5V, enable*, load*, current=4.5A
# relay.kl15  relay          state*=0

boat device set psu.main voltage 24     # setpoint
boat device set relay.kl15 state 1      # close the contact
boat device read psu.main
# voltage  settable=yes readable=yes  value=24  unit=V
# current                readable=yes  value=8   unit=A     (24 V / 3 Ω)
```

`*` marks a settable channel. Relay commands use `0` = open, non-zero = closed.

## Programmatic (Python SDK)

```python
from boat.device_node import DeviceNode

dev = DeviceNode()                      # localhost:50051
for d in dev.list_devices():
    print(d.device_id, d.kind)
dev.set_control("psu.main", "voltage", 24.0)
dev.set_control("relay.kl15", "state", 1)
info = dev.read_state("psu.main")

# Stream measurement updates:
dev.stream_state(lambda u: print(u.device_id, u.channel, u.value))
```

The lower-level `BusNode` also works — publish `psu.main.voltage.set` directly
and subscribe to `psu.main.voltage.meas` — which is what the Python device
*nodes* (`demo/virtual_psu_node.py`, `demo/virtual_relay_node.py`) use for
interactive control without a gateway rebuild.

## Virtual device configuration

`virtual_psu` (deterministic, tick-driven):

| Key | Meaning | Default |
|---|---|---|
| `id` | device id → `psu.<id>.…` | `main` |
| `v_set` | initial target voltage [V] | `13.5` |
| `v_min` / `v_max` | clamp range | `0` / `60` |
| `ramp_v_per_tick` | slew per tick [V] | `0.05` |
| `r_load` | load resistance [Ω] (0 = open, I = 0) | `0` |
| `i_limit` | current cap [A] (0 = unlimited) | `0` |
| `enabled` | output on/off | `true` |

`virtual_relay`: `id` (default `kl15`), `default_closed` (bool), `debounce_ticks`.

`virtual_generator` (alternator): `id` (default `alt`), `rpm_set`, `rpm_per_tick`,
`cut_in_rpm`, `rated_rpm`, `v_rest`, `v_regulated`, `r_load`. Consumes
`gen.<id>.rpm.set` / `.enable` / `.load.resistance`; publishes `gen.<id>.rpm.meas`,
`gen.<id>.output_voltage.meas`, `gen.<id>.output_current.meas`. Below cut-in rpm
the output rises proportionally; above it, it regulates linearly to `v_regulated`
at rated rpm. Drive it the same way: `boat device set gen.alt rpm 6000`.

Consumed signals: `psu.<id>.voltage.set`, `psu.<id>.enable`,
`psu.<id>.load.resistance`, `relay.<id>.set`. Published: `psu.<id>.voltage.meas`,
`psu.<id>.current.meas`, `relay.<id>.state`.

## KL15 gate — ignition-controlled restbus

Make cyclic transmission follow an ignition relay, like a real vehicle:

```bash
# terminal 1 — a relay + the restbus gated on it (needs a CAN iface + PDU db)
sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up
BOAT_CAN_INTERFACES=vcan0 \
  BOAT_NODE_PLUGINS="$P/virtual_relay/virtual_relay.so?{\"id\":\"kl15\"}" $GW &
python3 demo/restbus_simulator.py config/<db>.json --kl15-relay kl15

# terminal 2
boat device set relay.kl15 state 1   # ignition ON  → cyclic frames start
boat device set relay.kl15 state 0   # ignition OFF → bus goes quiet
```

## Record & replay device state

Device measurements can be recorded into the event store and replayed back onto
the signal bus (they replay as named signals, not synthetic frames).

```bash
# record (run from boat-platform/ so boat_events.db lands here)
BOAT_RECORD_BUS_SIGNALS=devrec BOAT_RECORD_BUS_PREFIXES=psu. \
BOAT_NODE_PLUGINS="$P/virtual_psu/virtual_psu.so?{\"id\":\"main\",\"r_load\":3.0}" $GW
# … drive psu.main voltage, then stop the gateway …

# replay the recorded curve onto the bus
BOAT_CAN_INTERFACES="" $GW &
boat replay from-events --sim-id devrec --signal-id psu.main.voltage.meas \
  --speed accelerated -m 20
```

`BOAT_RECORD_BUS_SIGNALS=<tag>` enables recording (off by default);
`BOAT_RECORD_BUS_PREFIXES=psu.,relay.` narrows which signals are captured.

## Physical hardware over SCPI

`scpi_device` drives a real SCPI power supply / e-load over TCP (the LXI/raw-
socket port, commonly `5025`) — the *same* signal contract as `virtual_psu`, so
`device_manager`, the CLI, and the SDK see it identically.

```bash
BOAT_NODE_PLUGINS="$P/device_manager/device_manager.so,\
$P/scpi_device/scpi_device.so?{\"id\":\"main\",\"host\":\"192.168.0.5\",\"port\":5025,\"poll_ms\":200}" \
  $GW
# then drive it exactly as the virtual PSU:
boat device set psu.main voltage 5
boat device read psu.main
```

Channels map to SCPI commands (`voltage`→`VOLT {v}`, `enable`→`OUTP {ONOFF}`,
reads → `MEAS:VOLT?`/`MEAS:CURR?`). All instrument I/O runs on the plugin's own
worker thread, so a slow or unreachable instrument never stalls the gateway tick
loop; without a reachable instrument the plugin simply idles.

**Physical devices are live-only:** they are HIL-gated, excluded from the
determinism seed test, and are never the target of a replay (a replay
reconstitutes recorded state into a *virtual* model).

## Environment config (`environment.schema.json`)

The HIL environment schema has a `devices:` block mirroring the bus
virtual/physical split:

```json
"devices": {
  "psu.main":  { "type": "scpi",    "kind": "power_supply", "host": "192.168.0.5", "port": 5025 },
  "relay.kl15":{ "type": "virtual", "kind": "relay" }
}
```

`type` ∈ `virtual | scpi | gpio | modbus`. The **test harness consumes this
block**: `EnvironmentConfig.node_plugin_specs()` turns each device into a
`BOAT_NODE_PLUGINS` entry (prepending `device_manager`), so declaring a device in
the env config is enough for `TestHarness` to launch it — no manual plugin wiring.
Mapping: `virtual`+`power_supply`/`relay`/`generator` →
`virtual_psu`/`virtual_relay`/`virtual_generator`; `scpi` → `scpi_device`;
`gpio` → `gpio_relay`; `modbus` → `modbus_device`.

## Tests

- C++ unit: `boat_unit_scpi_device` (SCPI driver over a loopback socket vs a mock
  instrument), plus the `virtual_psu`/recorder coverage in `boat_unit_replay_engine`.
- System-level: [`test/Devices.md`](../../../test/Devices.md) and the runnable
  [`test/test_device_integration.py`](../../../test/test_device_integration.py):
  ```bash
  python3 test/test_device_integration.py
  ```
