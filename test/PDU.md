# TestSet: PDU

System-level tests for PDU routing, transmission schedules, I-PDU groups, and PDU
database inspection. All routing behavior is provided by the `pdu_router.so` plugin.

Common precondition: gateway running with `BOAT_CAN_INTERFACES=vcan0` and
`BOAT_NODE_PLUGINS=<path>/pdu_router.so`; CLI installed.

---

### TC_PDU_001_route_and_send

**TestSets:** [PDU], [CLI]

**Preconditions:**
- `candump vcan0` running

**TestSteps:**
1. `boat pdu route --id 0x100 --transport can --iface vcan0`
2. `boat pdu send --id 0x100 --data 11223344`
3. `boat pdu list-routes`

**Expected:**
- The PDU is emitted on `vcan0` as a CAN frame; the route appears in `list-routes`

**Verdict:** OK

**Result:**

---

### TC_PDU_002_cyclic_transmission_schedule

**TestSets:** [PDU]

**Preconditions:**
- `candump -t d vcan0` running (delta timestamps)

**TestSteps:**
1. `boat pdu route --id 0x100 --transport can --iface vcan0 --send-type cyclic --cycle-ms 100`
2. Observe the bus for ≥ 3 seconds

**Expected:**
- The PDU's frame appears every ~100 ms (within tick resolution), continuously,
  without any simulation running (the always-on node tick drives it)

**Verdict:** OK

**Result:**

---

### TC_PDU_003_remove_route_stops_transmission

**TestSets:** [PDU]

**Preconditions:**
- A cyclic route active (TC_PDU_002)

**TestSteps:**
1. `boat pdu remove-route --id 0x100`
2. Observe `candump vcan0` for 2 s; run `boat pdu list-routes`

**Expected:**
- Cyclic transmission stops; route no longer listed

**Verdict:** OK

**Result:**

---

### TC_PDU_004_ipdu_group_enable_disable

**TestSets:** [PDU], [CLI]

**Preconditions:**
- Cyclic routes for PDUs 0x100 and 0x200 configured
- `boat pdu route --id 0x100 --transport can --iface vcan0 --send-type cyclic --cycle-ms 100`
- `boat pdu route --id 0x200 --transport can --iface vcan0 --send-type cyclic --cycle-ms 100`

**TestSteps:**
1. `boat pdu group --id 1 --name "Safety" --pdu 0x100 --pdu 0x200 --disabled`
2. Observe the bus; then `boat pdu enable-group --id 1`; observe again
3. `boat pdu list-groups`

**Expected:**
- While the group is disabled neither PDU transmits; after enable both resume
- `list-groups` shows the group with its members and current state

**Verdict:** OK

**Result:**

`boat pdu route --id 0x100 --transport can --iface vcan0 --send-type cyclic --cycle-ms 100`
| pdu_id     | iface | transport | schedule             | ok   |
|---|---|---|---|---|
| 0x00000100 | vcan0 | CAN       | cyclic(100ms/0ms/0x) | True |

`boat pdu route --id 0x200 --transport can --iface vcan0 --send-type cyclic --cycle-ms 100`
| pdu_id     | iface | transport | schedule             | ok   |
|---|---|---|---|---|
| 0x00000200 | vcan0 | CAN       | cyclic(100ms/0ms/0x) | True |

`boat pdu group --id 1 --name "Safety" --pdu 0x100 --pdu 0x200 --disabled`
| group_id | name   | pdu_ids                      | enabled | ok   |
|---|---|---|---|---|
| 0x1      | Safety | ['0x00000100', '0x00000200'] | False   | True |

`boat pdu enable-group --id 1`
| group_id | ok   |
|---|---|
| 0x1      | True |

`boat pdu list-groups`
| group_id | name   | pdu_ids                | enabled |
|---|---|---|---|
| 0x1      | Safety | 0x00000100, 0x00000200 | yes     |

`boat pdu disable-group --id 1`
| group_id | ok   |
|---|---|
| 0x1      | True |

---

### TC_PDU_005_subscribe

**TestSets:** [PDU], [CLI]

**Preconditions:**
- A cyclic route active

**TestSteps:**
1. `boat pdu subscribe --id 0x100` in a second shell

**Expected:**
- Routed PDUs stream to the subscriber with PDU id and payload

**Verdict:** OK

**Result:**

---

### TC_PDU_006_signal_packing_from_database

**TestSets:** [PDU], [WebUIs]

**Preconditions:**
- A PDU database with a message containing Intel and Motorola signals with
  factor/offset scaling; Commander UI running (port 8082)

**TestSteps:**
1. In the Commander, load the database, set signal values (e.g. MotorSpeed = 1000),
   and send the message
2. Decode the on-bus frame manually (or with a DBC-aware tool)

**Expected:**
- Bit positions, byte order, and scaling of every signal match the database
  definition — raw = (phys − offset) / factor at the defined start bit and length

**Verdict:** NOT_TESTED

**Result:**

---

### TC_PDU_007_db_inspection_cli

**TestSets:** [PDU], [CLI]

**Preconditions:**
- At least one PDU database JSON in the config directory

**TestSteps:**
1. `boat db list`
2. `boat db show --db pdu_db.json`
3. `boat db signal-routes --db pdu_db.json --signal MotorSpeed`

**Expected:**
- List shows available databases; show renders messages/signals; signal-routes
  resolves the routing of the named signal

**Verdict:** NOK

**Result:**
- Nothing works
---

### TC_PDU_008_grpc_delegation_to_plugin

**TestSets:** [PDU], [Plugins], [Error]

**Preconditions:**
- Gateway started WITHOUT the pdu_router plugin

**TestSteps:**
1. `boat pdu list-routes`

**Expected:**
- A clear error indicating the PDU router plugin is not loaded (gRPC PDU calls are
  delegated to the plugin) — not a crash, not an empty success

**Verdict:** OK

**Result:**

RPC error [NOT_FOUND]: PduRouter plugin not loaded

---

### TC_PDU_009_frame_send_pdu_dispatch

**TestSets:** [PDU], [Frame]

**Preconditions:**
- pdu_router loaded; a route for PDU 0x100 exists

**TestSteps:**
1. Send a PDU bus-type frame via the unified service:
   `boat frame send --bus-type pdu ...` (or `FrameService.SendFrame` from the SDK)

**Expected:**
- The frame is dispatched to the pdu_router plugin (not written to a wire directly)
  and the routed result appears on the target bus

**Verdict:** NOK

**Result:**
Doesnt work at all
---

### TC_PDU_010_e2e_crc_protection

**TestSets:** [PDU]

**Preconditions:**
- A database message configured with E2E CRC protection and a rolling counter

**TestSteps:**
1. Route and cyclically transmit the protected message
2. Capture ≥ 16 consecutive frames and check the counter and CRC fields

**Expected:**
- The counter increments per frame and wraps correctly; the CRC field validates
  against the E2E profile for every frame

**Verdict:** NOT_TESTED

**Result:**
Not implemented
