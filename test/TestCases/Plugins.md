# TestSet: Plugins

System-level tests for plugin lifecycle and dispatch semantics: loading, config,
bus-type filtering, the dual plugin-manager architecture, and unloading.

Common precondition: gateway running with `BOAT_CAN_INTERFACES=vcan0`; plugin `.so`
files built under `build/debug/src/plugins/`.

---

### TC_Plugins_001_register_list_info_unload

**TestSets:** [Plugins], [CLI]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. `boat plugin register` a plugin `.so` at runtime
2. `boat plugin list`, then `boat plugin info` for it
3. `boat plugin unload` it, then `boat plugin list` again

**Expected:**
- Register loads the plugin (initialize called); list/info show it with its metadata;
  unload removes it (shutdown called) and it disappears from the list

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Plugins_002_json_config_applied

**TestSets:** [Plugins]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Load a plugin with a config query string:
   `BOAT_NODE_PLUGINS=<plugin>.so?{"iface":"vcan0","key":"value"}`
2. Verify the plugin observed the config (plugin log / behavior bound to `vcan0`)

**Expected:**
- The JSON after `?` reaches the plugin's `initialize`; malformed JSON produces a
  clear load-time error

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Plugins_003_declared_buses_filtering

**TestSets:** [Plugins], [CAN], [Ethernet]

**Preconditions:**
- A test plugin declaring ONLY the `can` bus type, logging every `on_frame`
- Gateway with both a CAN and an Ethernet interface

**TestSteps:**
1. Send one CAN frame and one Ethernet frame through the gateway

**Expected:**
- The plugin's `on_frame` fires for the CAN frame only — Ethernet is never delivered
  to a plugin that did not declare `eth` (pre-filtered dispatch, no O(N) fan-out)

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Plugins_004_publish_path_through_frame_sink

**TestSets:** [Plugins], [CAN]

**Preconditions:**
- A test plugin that publishes a CAN frame from `on_tick`

**TestSteps:**
1. Load the plugin; watch `candump vcan0`

**Expected:**
- The published frame reaches the wire (plugin → frame publisher → FrameSink →
  registry → SocketCAN) with correct ID/data

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Plugins_005_dual_manager_independence

**TestSets:** [Plugins], [Simulation]

**Preconditions:**
- A node plugin loaded via `BOAT_NODE_PLUGINS` and a scenario declaring a
  simulation-scoped plugin

**TestSteps:**
1. Start the simulation; confirm both plugins are active
2. Stop the simulation

**Expected:**
- The simulation-scoped plugin loads at sim start and is torn down at sim stop;
  the node plugin keeps running throughout, unaffected

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Plugins_006_replayed_frames_reach_plugins

**TestSets:** [Plugins], [Replay]

**Preconditions:**
- A CAN-declaring test plugin loaded; a CAN trace imported as `demo`

**TestSteps:**
1. `boat replay stream --trace demo --buses vcan0`
2. Inspect the plugin's `on_frame` log

**Expected:**
- Replayed frames are delivered to the plugin's `on_frame` like live traffic
  (replay transmits through the same FrameSink/registry RX dispatch)

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Plugins_007_nonexistent_so_error

**TestSets:** [Plugins], [Error]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Start the gateway with `BOAT_NODE_PLUGINS=/no/such/plugin.so`
2. `boat plugin register /no/such/plugin.so` on a running gateway

**Expected:**
- Both paths produce a clear "cannot load" error naming the path; the gateway stays
  functional

**Verdict:** NOT_TESTED

**Result:**
