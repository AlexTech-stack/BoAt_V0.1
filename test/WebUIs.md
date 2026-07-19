# TestSet: WebUIs

System-level tests for the gateway-integrated web UIs (started by `start_ui.sh`):
Launcher (8086), Dashboard (8080), Nodes/Control Panel (8081), Commander (8082),
Recorder (8083), plus the on-demand Debug inspector (8084).
Recorder-specific recording cases live in [Recording].

Common precondition: gateway running with `BOAT_CAN_INTERFACES=vcan0`; UIs started
via `./start_ui.sh`.

---

### TC_WebUIs_001_all_uis_reachable

**TestSets:** [WebUIs]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Open ports 8086, 8080, 8081, 8082, 8083 in a browser (or `curl -s` each)

**Expected:**
- Every UI serves its page (HTTP 200, BoAt-styled page with the ⛵ header)

**Verdict:** NOT_TESTED

**Result:**

---

### TC_WebUIs_002_shared_navigation

**TestSets:** [WebUIs]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. On each gateway UI, inspect the top navigation bar
2. Click through the links

**Expected:**
- The nav lists exactly the five gateway UIs (Launcher, Dashboard, Nodes, Commander,
  Recorder) — standalone tools are NOT mixed in; the current page is highlighted;
  links resolve using the browser's current hostname

**Verdict:** NOT_TESTED

**Result:**

---

### TC_WebUIs_003_launcher_interface_creation

**TestSets:** [WebUIs], [Gateway]

**Preconditions:**
- Passwordless sudo for `modprobe`/`ip link` configured; `vcan7` does not exist

**TestSteps:**
1. In the Launcher (8086), create a new vcan interface `vcan7`
2. `ip link show vcan7` in a shell

**Expected:**
- The interface exists and is up; the Launcher lists it

**Verdict:** NOT_TESTED

**Result:**

---

### TC_WebUIs_004_launcher_gateway_lifecycle

**TestSets:** [WebUIs], [Gateway]

**Preconditions:**
- No gateway currently running; gateway binary built

**TestSteps:**
1. In the Launcher, start the gateway with `vcan0` selected
2. Observe the live log pane; run `boat frame list-ifaces`
3. Stop the gateway from the Launcher

**Expected:**
- Gateway starts, log lines stream into the UI, gRPC answers; stop terminates the
  process and the UI reflects the stopped state with exit code

**Verdict:** NOT_TESTED

**Result:**

---

### TC_WebUIs_005_dashboard_live_frames

**TestSets:** [WebUIs], [CAN]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Open the Dashboard (8080)
2. `cansend vcan0 123#AABBCCDD`

**Expected:**
- The frame appears in the live CAN trace within ~1 s with correct ID/data; event
  log and bus-signal panes update when corresponding traffic exists

**Verdict:** NOT_TESTED

**Result:**

---

### TC_WebUIs_006_nodes_start_stop

**TestSets:** [WebUIs]

**Preconditions:**
- At least one non-interactive node script under `boat-platform/nodes/`

**TestSteps:**
1. Open Nodes (8081); start a node; observe its rolling log
2. Stop the node

**Expected:**
- Node subprocess starts (traffic/log visible), log streams into the UI, stop
  terminates it and shows the exit code; interactive nodes are marked not runnable

**Verdict:** NOT_TESTED

**Result:**

---

### TC_WebUIs_007_commander_raw_send

**TestSets:** [WebUIs], [CAN]

**Preconditions:**
- `candump vcan0` running

**TestSteps:**
1. In the Commander (8082), compose and send a raw CAN frame (ID 0x321, data 0102)

**Expected:**
- The frame appears on the bus exactly as composed

**Verdict:** NOT_TESTED

**Result:**

---

### TC_WebUIs_008_commander_pdu_composed_send

**TestSets:** [WebUIs], [PDU]

**Preconditions:**
- A PDU database with scaled signals loaded in the Commander

**TestSteps:**
1. Select a message, set signal values in engineering units, send
2. Decode the on-bus frame against the database definition

**Expected:**
- Signal packing (start bit, length, byte order, factor/offset) matches the database
  — same packing rules as TC_PDU_006

**Verdict:** NOT_TESTED

**Result:**

---

### TC_WebUIs_009_debug_grpc_inspector

**TestSets:** [WebUIs]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Start `python3 ui/debug.py`, open port 8084
2. Run any CLI command (e.g. `boat frame list-ifaces`)

**Expected:**
- The inspector shows the RPC (method name, caller ip:port, lifecycle events,
  message sizes, duration, status code) in near-real time

**Verdict:** NOT_TESTED

**Result:**

---

### TC_WebUIs_010_ui_behavior_gateway_down

**TestSets:** [WebUIs], [Error]

**Preconditions:**
- UIs running; gateway stopped

**TestSteps:**
1. Open the Dashboard and Commander; attempt an action that needs the gateway

**Expected:**
- The UIs stay up and clearly indicate the gateway is unreachable (status badge /
  error toast) — no unhandled exceptions, no blank pages; they recover automatically
  once the gateway is back

**Verdict:** NOT_TESTED

**Result:**
