# TestSet: Gateway

System-level tests for `boat_gateway` lifecycle: startup, interface registration,
driver selection, plugin loading, tick configuration, and shutdown.

---

### TC_Gateway_001_start_with_vcan

**TestSets:** [Gateway]

**Preconditions:**
- Gateway built (`cmake --preset debug && cmake --build --preset debug`)
- `vcan0` exists and is up (`sudo modprobe vcan && sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up`)

**TestSteps:**
1. Start `BOAT_CAN_INTERFACES=vcan0 ./build/debug/src/gateway/grpc_gateway/boat_gateway`
2. From a second shell run `boat frame list-ifaces`

**Expected:**
- Gateway starts without error and logs that it is serving gRPC on `0.0.0.0:50051`
- `list-ifaces` shows `vcan0` as a CAN interface using the virtual driver

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_002_start_with_multiple_interfaces

**TestSets:** [Gateway]

**Preconditions:**
- `vcan0`, `vcan1` exist and are up

**TestSteps:**
1. Start the gateway with `BOAT_CAN_INTERFACES=vcan0,vcan1`
2. Run `boat frame list-ifaces`

**Expected:**
- Both interfaces are listed and usable (a frame can be sent on each)

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_003_driver_selection_physical_vs_virtual

**TestSets:** [Gateway], [Hardware]

**Preconditions:**
- One physical CAN adapter (e.g. PEAK PCAN) available as `can0`, brought up with
  `sudo ip link set can0 up type can bitrate 500000`
- `vcan0` exists and is up

**TestSteps:**
1. Start the gateway with `BOAT_CAN_INTERFACES=can0,vcan0`
2. Run `boat frame list-ifaces` (and `boat --json frame list-ifaces`)

**Expected:**
- `can0` is registered with the physical driver (hardware metadata from sysfs visible),
  `vcan0` with the virtual driver
- No error at startup; both interfaces accept frames

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_004_start_with_ethernet_interface

**TestSets:** [Gateway], [Ethernet]

**Preconditions:**
- A veth pair exists (`sudo ip link add veth0 type veth peer name veth1 && sudo ip link set veth0 up && sudo ip link set veth1 up`)

**TestSteps:**
1. Start the gateway with `BOAT_ETH_INTERFACES=veth0`
2. Run `boat frame list-ifaces`

**Expected:**
- `veth0` is listed as an Ethernet interface

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_005_node_plugin_loading_with_json_config

**TestSets:** [Gateway], [Plugins]

**Preconditions:**
- Gateway and plugins built; `vcan0` up

**TestSteps:**
1. Start the gateway with
   `BOAT_NODE_PLUGINS=./build/debug/src/plugins/pdu_router/pdu_router.so,./build/debug/src/plugins/can_tp/can_tp.so?{"iface":"vcan0"}`
2. Run `boat plugin list`

**Expected:**
- Both plugins load at startup without error; the JSON config after `?` is applied
  (CAN-TP bound to `vcan0`)
- `boat plugin list` shows both plugins as loaded

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_006_v7_plugin_rejected

**TestSets:** [Gateway], [Plugins], [Error]

**Preconditions:**
- A plugin `.so` built against plugin ABI v7 (or a stub reporting `BOAT_PLUGIN_ABI_VERSION` = 7)

**TestSteps:**
1. Start the gateway with `BOAT_NODE_PLUGINS=<v7_plugin.so>`

**Expected:**
- The plugin is rejected at load with a clear error message naming the ABI version mismatch
- The gateway either continues without the plugin or exits with a diagnostic — it must not
  crash or load the plugin partially

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_007_tick_interval_configuration

**TestSets:** [Gateway]

**Preconditions:**
- Gateway built; `vcan0` up; PDU router plugin available

**TestSteps:**
1. Start the gateway with `BOAT_NODE_TICK_US=100` and a cyclic PDU route (cycle 10 ms)
2. Measure the frame cadence on `vcan0` with `candump -t d vcan0`
3. Repeat with `BOAT_NODE_TICK_MS=1` and with both variables set simultaneously

**Expected:**
- Cyclic frames appear at the configured cycle time within tick resolution
- When both variables are set, `BOAT_NODE_TICK_US` takes precedence

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_008_graceful_shutdown

**TestSets:** [Gateway]

**Preconditions:**
- Gateway running with one CAN interface and one plugin loaded

**TestSteps:**
1. Send SIGINT (Ctrl+C) to the gateway process
2. Restart the gateway with the same configuration

**Expected:**
- Gateway shuts down cleanly (plugins unloaded, no crash, exit code 0 or documented signal exit)
- Restart succeeds — no leaked sockets ("address already in use") or stale lock files

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Gateway_009_missing_interface_error

**TestSets:** [Gateway], [Error]

**Preconditions:**
- Interface `vcan99` does NOT exist

**TestSteps:**
1. Start the gateway with `BOAT_CAN_INTERFACES=vcan99`

**Expected:**
- A clear error naming the missing interface (not a crash or silent success)

**Verdict:** NOT_TESTED

**Result:**
