# TestSet: Simulation

System-level tests for the simulation lifecycle and the determinism guarantee.

Common precondition: gateway running with `BOAT_CAN_INTERFACES=vcan0`; at least one
valid scenario available (`boat scenario list`).

---

### TC_Simulation_001_create_and_start

**TestSets:** [Simulation], [CLI]

**Preconditions:**
- Scenario `my_scenario` exists

**TestSteps:**
1. `boat sim create --scenario-id my_scenario` (note the returned simulation id)
2. `boat sim start --simulation-id <id>`
3. `boat sim state --simulation-id <id>`

**Expected:**
- Create returns a simulation id; state transitions IDLE → RUNNING
- Scenario-declared traffic appears on the bus (`candump vcan0`)

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Simulation_002_pause_resume

**TestSets:** [Simulation], [CLI]

**Preconditions:**
- A running simulation producing cyclic traffic

**TestSteps:**
1. `boat sim pause --simulation-id <id>` — watch `candump vcan0`
2. `boat sim start --simulation-id <id>` (resume)

**Expected:**
- While paused: no simulation traffic on the bus, state = PAUSED
- After resume: traffic continues, state = RUNNING

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Simulation_003_step_exact_ticks

**TestSets:** [Simulation], [CLI], [Determinism]

**Preconditions:**
- A paused simulation whose scenario sends one frame every N ticks

**TestSteps:**
1. `boat sim step --simulation-id <id> --ticks 500`
2. Count the frames that appeared during the step (candump timestamped log)

**Expected:**
- Exactly the number of frames corresponding to 500 ticks is emitted, then the
  simulation is paused again — no free-running drift

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Simulation_004_stop_and_cleanup

**TestSets:** [Simulation], [CLI]

**Preconditions:**
- A running simulation with simulation-scoped plugins loaded

**TestSteps:**
1. `boat sim stop --simulation-id <id>`
2. `boat sim list`
3. `boat plugin list`

**Expected:**
- Simulation transitions to STOPPED and disappears from (or is marked stopped in) the list
- Simulation-scoped plugins are torn down; always-on node plugins remain loaded

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Simulation_005_list_and_watch

**TestSets:** [Simulation], [CLI]

**Preconditions:**
- Two simulations created, one running

**TestSteps:**
1. `boat sim list`
2. `boat sim watch --simulation-id <running-id>` for a few seconds

**Expected:**
- List shows both simulations with correct states
- Watch streams live state/tick updates until interrupted with Ctrl+C

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Simulation_006_determinism_same_seed_bit_identical

**TestSets:** [Simulation], [Determinism]

**Preconditions:**
- A scenario with seeded randomness (e.g. fault injection or randomized payloads)
- Recorder available

**TestSteps:**
1. Run the scenario with seed S while recording all traffic to a trace file; stop
2. Reset, run the identical scenario with the same seed S recording to a second file
3. Compare the two recordings' frame sequences (IDs, payloads, tick order)

**Expected:**
- Both runs produce an identical frame sequence — same frames, same payloads, same
  order (timestamps may differ in wall clock but not in tick placement)

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Simulation_007_different_seed_differs

**TestSets:** [Simulation], [Determinism]

**Preconditions:**
- Same scenario as TC_Simulation_006

**TestSteps:**
1. Run the scenario with seed S1, record
2. Run the scenario with seed S2 ≠ S1, record
3. Compare the recordings

**Expected:**
- The runs differ (sanity check that the seed actually influences behavior — guards
  against a determinism test that trivially passes because nothing is random)

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Simulation_008_invalid_scenario_error

**TestSets:** [Simulation], [Error]

**Preconditions:**
- Gateway running; scenario id `does_not_exist` absent

**TestSteps:**
1. `boat sim create --scenario-id does_not_exist`

**Expected:**
- A clear error naming the unknown scenario; no simulation is created
  (`boat sim list` unchanged)

**Verdict:** NOT_TESTED

**Result:**
