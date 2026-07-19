# TestSet: CAN

System-level tests for CAN / CAN FD frame transmission and reception through the
gateway, verified against the kernel's own view (`candump`/`cansend`).

Common precondition for all cases: gateway running with `BOAT_CAN_INTERFACES=vcan0`
(unless stated otherwise), CLI installed and connected.

---

### TC_CAN_001_send_frame_cli

**TestSets:** [CAN], [CLI]

**Preconditions:**
- `candump vcan0` running in a second shell

**TestSteps:**
1. `boat frame send --bus-type can --can-id 0x123 --iface vcan0 --data AABBCCDD`

**Expected:**
- CLI reports success
- `candump` shows exactly one frame: ID `123`, DLC 4, data `AA BB CC DD`

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CAN_002_receive_frame_subscribe

**TestSets:** [CAN], [CLI]

**Preconditions:**
- `boat frame subscribe --bus-types can` running in a second shell

**TestSteps:**
1. Inject a frame from outside the gateway: `cansend vcan0 456#DEADBEEF`

**Expected:**
- The subscriber prints the frame with ID `0x456`, data `DEADBEEF`, iface `vcan0`,
  and a plausible timestamp

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CAN_003_send_canfd_frame

**TestSets:** [CAN], [CLI]

**Preconditions:**
- `candump vcan0` running

**TestSteps:**
1. `boat frame send --bus-type canfd --can-id 0x123 --iface vcan0 --data 00112233445566778899AABBCCDDEEFF`

**Expected:**
- `candump` shows a CAN FD frame (flags indicate FDF) with a 16-byte payload
- Bus type `canfd` implies the FD flag — no separate flag argument needed

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CAN_004_canfd_length_rounding

**TestSets:** [CAN], [Error]

**Preconditions:**
- `candump vcan0` running

**TestSteps:**
1. Send a CAN FD frame with a payload of 13 bytes (not a valid ISO 11898-1 FD length):
   `boat frame send --bus-type canfd --can-id 0x123 --iface vcan0 --data 00112233445566778899AABBCC`

**Expected:**
- The frame is transmitted with the payload padded/rounded up to the next valid FD
  length (16), not rejected and not truncated

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CAN_005_extended_29bit_id

**TestSets:** [CAN]

**Preconditions:**
- `candump vcan0` running

**TestSteps:**
1. `boat frame send --bus-type can --can-id 0x1BFC829F --iface vcan0 --data 01`

**Expected:**
- `candump` shows the full 29-bit identifier `1BFC829F` (EFF flag set), not a
  truncated 11-bit ID

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CAN_006_self_sent_loopback_flag

**TestSets:** [CAN], [Plugins]

**Preconditions:**
- Gateway running with a test plugin that logs `on_frame` calls including frame flags
  (any plugin declaring the `can` bus type)

**TestSteps:**
1. Have the plugin publish one CAN frame on `vcan0`
2. Inspect the plugin's log for the frame it receives back (its own echo)

**Expected:**
- The echoed frame carries `BOAT_CAN_FLAG_SELF_SENT` (0x08), allowing the plugin to
  distinguish its own transmissions from external wire RX

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CAN_007_multi_bus_isolation

**TestSets:** [CAN]

**Preconditions:**
- Gateway running with `BOAT_CAN_INTERFACES=vcan0,vcan1`
- `candump vcan0` and `candump vcan1` running

**TestSteps:**
1. Send a frame to `vcan0` only: `boat frame send --bus-type can --can-id 0x100 --iface vcan0 --data 01`

**Expected:**
- The frame appears on `vcan0` and NOT on `vcan1` — no cross-bus leakage

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CAN_008_list_ifaces

**TestSets:** [CAN], [CLI]

**Preconditions:**
- Gateway running with `BOAT_CAN_INTERFACES=vcan0,vcan1`

**TestSteps:**
1. `boat frame list-ifaces`
2. `boat --json frame list-ifaces`

**Expected:**
- Both interfaces listed with their metadata (driver kind, FD capability)
- JSON variant emits a machine-parseable array with the same content

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CAN_009_deprecated_wrapper_compat

**TestSets:** [CAN], [CLI]

**Preconditions:**
- `candump vcan0` running

**TestSteps:**
1. Send a frame through the deprecated wrapper (e.g. `python3 -m boat can send ...`
   or `boat can ...` where available)

**Expected:**
- The wrapper still works and produces the same on-bus result as `boat frame send`
- Deprecation is indicated (help text or warning), pointing to `boat frame`

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CAN_010_high_rate_burst

**TestSets:** [CAN], [Performance]

**Preconditions:**
- `candump vcan0 | wc -l` prepared to count frames

**TestSteps:**
1. Send 1000 frames in a tight loop via the Python SDK
   (`FrameNode.send_can("vcan0", 0x100+i%16, ...)`)
2. Count frames observed by `candump`

**Expected:**
- All 1000 frames arrive on the bus, no drops, gateway remains responsive
  (`boat frame list-ifaces` still answers during the burst)

**Verdict:** NOT_TESTED

**Result:**
