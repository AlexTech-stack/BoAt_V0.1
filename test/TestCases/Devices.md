# TestSet: Devices

System-level tests for **electrical device integration** — power supplies,
relays/contactors, generators, and generic I/O modelled as named signals on the
always-on signal bus, controlled via the `DeviceService` (`boat device …`), and
backed by deterministic virtual models or real SCPI hardware.

Many cases here are automated by [`test_device_integration.py`](test_device_integration.py)
(`python3 test/test_device_integration.py`), which launches the gateway with the
device plugins and asserts behaviour end-to-end (including an in-process mock
SCPI instrument — no hardware).

Common precondition: a debug build (`cmake --build --preset debug`) and the SDK
installed. The gateway is started per-case with the device plugins under
`build/debug/src/plugins/{device_manager,virtual_psu,virtual_relay,scpi_device}/`.
`BOAT_CAN_INTERFACES=""` is fine — devices are signal-domain, not wire buses.

---

### TC_Devices_001_virtual_psu_set_and_read

**TestSets:** [Devices], [CLI]

**Preconditions:**
- Gateway running with `BOAT_NODE_PLUGINS=…/device_manager.so,…/virtual_psu.so?{"id":"main","r_load":3.0}`

**TestSteps:**
1. `boat device list`
2. `boat device set psu.main voltage 24`
3. `boat device read psu.main`

**Expected:**
- `list` shows `psu.main` (kind `power_supply`) with settable channels
  `voltage`, `enable`, `load` and readable `voltage`, `current`
- After the set, `read` shows `voltage = 24 V`

**Verdict:** OK

**Result:**
Automated by `test_device_integration.py::test_virtual_devices` (passing).

---

### TC_Devices_002_virtual_psu_ohms_law_current

**TestSets:** [Devices]

**Preconditions:**
- Gateway with `virtual_psu.so?{"id":"main","r_load":3.0}`

**TestSteps:**
1. `boat device set psu.main voltage 24`
2. `boat device read psu.main`

**Expected:**
- `current = 8 A` (24 V across a 3 Ω load), updated from the ramped voltage

**Verdict:** OK

**Result:**
Automated by `test_device_integration.py::test_virtual_devices` (passing).

---

### TC_Devices_003_virtual_relay_set_state

**TestSets:** [Devices], [CLI]

**Preconditions:**
- Gateway with `virtual_relay.so?{"id":"kl15"}`

**TestSteps:**
1. `boat device set relay.kl15 state 1`
2. `boat device read relay.kl15`

**Expected:**
- `state = 1` (contact closed); setting `0` returns it to `0` (open)

**Verdict:** OK

**Result:**
Automated by `test_device_integration.py::test_virtual_devices` (passing).

---

### TC_Devices_004_device_manager_discovery

**TestSets:** [Devices], [Plugins]

**Preconditions:**
- Gateway with `device_manager.so` + `virtual_psu.so` + `virtual_relay.so`

**TestSteps:**
1. Wait ~1 s for the devices to publish their first measurements
2. `boat device list`

**Expected:**
- Both `psu.main` and `relay.kl15` are discovered with correct kinds and
  settable/readable channel flags — discovery is by observing published
  `.meas`/`.state` signals (no config coupling)

**Verdict:** OK

**Result:**
Automated by `test_device_integration.py::test_virtual_devices` (passing).

---

### TC_Devices_005_setcontrol_rejects_unknown_channel

**TestSets:** [Devices], [Error]

**Preconditions:**
- Gateway with `device_manager.so` + `virtual_psu.so`

**TestSteps:**
1. `boat device set psu.main nonsense 1`

**Expected:**
- Rejected with an error message (`channel 'nonsense' is not settable …`);
  no signal is published

**Verdict:** OK

**Result:**
Automated by `test_device_integration.py::test_virtual_devices` (passing).

---

### TC_Devices_006_kl15_gates_restbus

**TestSets:** [Devices], [PDU]

**Preconditions:**
- Gateway with `virtual_relay.so?{"id":"kl15"}` and a CAN interface (`vcan0`)
- A PDU database available for the restbus demo

**TestSteps:**
1. `python3 demo/restbus_simulator.py <db>.json --kl15-relay kl15` (blocks, waiting for ignition)
2. `boat device set relay.kl15 state 1` — observe cyclic CAN traffic start (`candump vcan0`)
3. `boat device set relay.kl15 state 0` — observe traffic stop

**Expected:**
- Cyclic transmission starts when the ignition contact closes and stops when it
  opens — the restbus follows KL15 like a real vehicle

**Verdict:** OK

**Result:**
Gate logic automated by `test_device_integration.py::test_kl15_restbus_gate`
(passing): a real `_Kl15Gate` over the live bus + `virtual_relay` invokes
start/stop on ignition transitions. The CAN/PDU transmission side is stubbed
(needs vcan + a PDU database) — that observation remains manual.

---

### TC_Devices_007_record_replay_device_curve

**TestSets:** [Devices], [Replay], [Recording]

**Preconditions:**
- Recording gateway started with `BOAT_RECORD_BUS_SIGNALS=devrec BOAT_RECORD_BUS_PREFIXES=psu.`
  and `virtual_psu.so`, run from `boat-platform/` (so `boat_events.db` is written there)

**TestSteps:**
1. Drive a voltage ramp (publish `psu.main.voltage.set`); stop the gateway
2. Start a fresh gateway (no recording); subscribe to `psu.main.voltage.meas`
3. `boat replay from-events --sim-id devrec --signal-id psu.main.voltage.meas --speed accelerated -m 20`

**Expected:**
- The recorded voltage curve is republished onto the signal bus as named
  signals (not synthetic CAN frames); the subscriber sees the full ramp

**Verdict:** OK

**Result:**
Automated by `test_device_integration.py::test_record_replay` (passing).

---

### TC_Devices_008_scpi_physical_psu_mock

**TestSets:** [Devices]

**Preconditions:**
- A SCPI instrument reachable over TCP. The automated test uses an in-process
  mock PSU (3 Ω load); no hardware required.

**TestSteps:**
1. Gateway with `device_manager.so` + `scpi_device.so?{"id":"main","host":<h>,"port":<p>}`
2. `boat device set psu.main voltage 24`
3. `boat device read psu.main`

**Expected:**
- `scpi_device` connects (`*IDN?`), sends `VOLT 24`, polls `MEAS:VOLT?`/`MEAS:CURR?`;
  `read` shows `voltage = 24 V`, `current = 8 A` — same DeviceService/CLI path as
  the virtual PSU

**Verdict:** OK

**Result:**
Automated by `test_device_integration.py::test_scpi_device` (passing, mock instrument).

---

### TC_Devices_009_scpi_physical_psu_hardware

**TestSets:** [Devices], [Hardware]

**Preconditions:**
- A real SCPI bench power supply on the LXI/raw-socket port (e.g. `:5025`),
  `BOAT_HIL_ENABLED=1`

**TestSteps:**
1. Gateway with `scpi_device.so?{"id":"main","host":<psu-ip>,"port":5025}`
2. `boat device set psu.main voltage 5`; measure the physical output
3. `boat device read psu.main`

**Expected:**
- The physical output tracks the setpoint; measured values match the instrument
  front panel

**Verdict:** NOT_TESTED

**Result:**
Requires physical hardware — live-only, excluded from CI.

---

### TC_Devices_010_stream_state

**TestSets:** [Devices]

**Preconditions:**
- Gateway with `device_manager.so` + `virtual_psu.so`

**TestSteps:**
1. Open a `DeviceService.StreamState` stream (SDK: `DeviceNode.stream_state`)
2. Drive `psu.main` voltage while streaming

**Expected:**
- A `DeviceStateUpdate` arrives for each measured change with the correct
  `device_id`, `channel`, `value`, and a timestamp

**Verdict:** OK

**Result:**
Automated by `test_device_integration.py::test_stream_state` (passing).

---

### TC_Devices_011_determinism_unaffected

**TestSets:** [Devices], [Determinism]

**Preconditions:**
- Debug build

**TestSteps:**
1. `ctest --test-dir build/debug -R boat_determinism_seed` (or the `Determinism` tests)
   with the virtual device plugins available

**Expected:**
- Seed reproducibility still holds bit-for-bit; virtual devices are pure
  functions of the tick sequence and receive no unseeded input in the seed test

**Verdict:** OK

**Result:**
Verified — determinism/seed tests pass with the device work in the tree.

---

### TC_Devices_012_cli_surface

**TestSets:** [Devices], [CLI]

**Preconditions:**
- Gateway with `device_manager.so` + a device plugin

**TestSteps:**
1. `boat device --help`; `boat device list`; `boat device set …`; `boat device read …`
2. `boat --json device list`

**Expected:**
- All three subcommands render (Rich tables by default, JSON with `--json`);
  help text is accurate

**Verdict:** OK

**Result:**
Verified manually via the `boat device` console script against a running gateway.

---

### TC_Devices_013_virtual_generator

**TestSets:** [Devices]

**Preconditions:**
- Gateway with `device_manager.so` + `virtual_generator.so?{"id":"alt","r_load":2.0}`

**TestSteps:**
1. `boat device set gen.alt rpm 6000`
2. `boat device read gen.alt`

**Expected:**
- `rpm ≈ 6000`, `output_voltage ≈ 14.2 V` (regulated at/above rated rpm),
  `output_current ≈ 7.1 A` (14.2 V / 2 Ω); below cut-in rpm the voltage rises
  proportionally from 0

**Verdict:** OK

**Result:**
Automated by `test_device_integration.py::test_virtual_generator` (passing).

---

### TC_Devices_014_environment_devices_block

**TestSets:** [Devices], [Plugins]

**Preconditions:**
- An environment config (`config/tests/*.json`) with a `devices:` block

**TestSteps:**
1. Load it via `EnvironmentConfig.from_dict(...)`
2. Inspect `cfg.node_plugin_specs()` (what `TestHarness` sets as `BOAT_NODE_PLUGINS`)

**Expected:**
- Each declared device becomes the correct plugin entry (`virtual_psu`/
  `virtual_relay`/`virtual_generator`/`scpi_device` with `id`/`host`/`port`),
  and `device_manager` is prepended so `DeviceService` works; no devices → no
  node plugins

**Verdict:** OK

**Result:**
Automated by `sdk/python/tests/test_test_config.py::TestDeviceConfig` (passing).
