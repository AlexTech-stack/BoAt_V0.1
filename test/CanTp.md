# TestSet: CanTp

System-level tests for the CAN Transport Protocol plugin (ISO 15765-2): session
configuration, segmentation, flow control, and always-on behavior.

Common precondition: gateway running with `BOAT_CAN_INTERFACES=vcan0` and
`BOAT_NODE_PLUGINS=<path>/can_tp.so?{"iface":"vcan0"}`.

---

### TC_CanTp_001_configure_session

**TestSets:** [CanTp], [CLI]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. `boat can-tp configure --nsdu-id diag --source-addr 0x7E0 --target-addr 0x7E8`

**Expected:**
- Configuration is accepted; subsequent sends with `--nsdu-id diag` use these
  addresses

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CanTp_002_single_frame_send

**TestSets:** [CanTp], [CAN]

**Preconditions:**
- Session configured (TC_CanTp_001); `candump vcan0` running

**TestSteps:**
1. `boat can-tp send --nsdu-id diag --source-addr 0x7E0 --target-addr 0x7E8 --data 0102030405` (≤ 7 bytes)

**Expected:**
- Exactly one CAN frame with ID 0x7E0: a Single Frame (PCI 0x05) carrying the payload

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CanTp_003_multi_frame_segmentation

**TestSets:** [CanTp], [CAN]

**Preconditions:**
- Session configured; a flow-control responder on 0x7E8 (second CAN-TP endpoint or a
  scripted responder sending FC.CTS)
- `candump vcan0` running

**TestSteps:**
1. Send a 64-byte payload via `boat can-tp send ...`

**Expected:**
- A First Frame (PCI 0x1) with the total length, followed — after the responder's
  Flow Control — by Consecutive Frames (PCI 0x2x) with incrementing sequence numbers
  until the payload is complete; reassembled data matches the input

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CanTp_004_always_on_reception

**TestSets:** [CanTp], [Plugins]

**Preconditions:**
- NO simulation running (node manager only)

**TestSteps:**
1. Inject a Single Frame diagnostic request onto `vcan0` addressed to the configured
   target (e.g. `cansend vcan0 7E0#02...`)

**Expected:**
- The CAN-TP plugin reacts (visible in gateway log / plugin response) even though no
  simulation is active — always-on node plugins are ticked independently

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CanTp_005_send_without_configuration

**TestSets:** [CanTp], [Error]

**Preconditions:**
- Freshly started gateway; nsdu-id `unknown` never configured

**TestSteps:**
1. `boat can-tp send --nsdu-id unknown --source-addr 0x7E0 --target-addr 0x7E8 --data 01`

**Expected:**
- A clear error (unknown/unconfigured session) — no partial transmission

**Verdict:** NOT_TESTED

**Result:**
