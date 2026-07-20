# TestSet: Scenario

System-level tests for scenario management via `boat scenario`.

Common precondition: gateway running.

---

### TC_Scenario_001_create_and_get

**TestSets:** [Scenario], [CLI]

**Preconditions:**
- A valid scenario definition file prepared

**TestSteps:**
1. `boat scenario create ...` with the definition
2. `boat scenario get` for the created id
3. `boat scenario list`

**Expected:**
- Create succeeds and returns/accepts the scenario id
- Get returns the same content that was submitted; list contains the id

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Scenario_002_validate_valid

**TestSets:** [Scenario], [CLI]

**Preconditions:**
- A syntactically and semantically valid scenario definition

**TestSteps:**
1. `boat scenario validate` with the definition

**Expected:**
- Validation passes with no findings

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Scenario_003_validate_invalid

**TestSets:** [Scenario], [CLI], [Error]

**Preconditions:**
- A scenario definition with a deliberate error (e.g. reference to an undefined bus
  or plugin, malformed schedule)

**TestSteps:**
1. `boat scenario validate` with the broken definition
2. Attempt `boat scenario create` with it

**Expected:**
- Validate reports the specific problem (actionable message, not a stack trace)
- Create is rejected or the scenario is unusable with the same diagnostic

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Scenario_004_delete

**TestSets:** [Scenario], [CLI]

**Preconditions:**
- A scenario exists that is not referenced by a running simulation

**TestSteps:**
1. `boat scenario delete` for the id
2. `boat scenario list`
3. `boat sim create --scenario-id <deleted-id>`

**Expected:**
- Scenario disappears from the list; creating a simulation from it now fails with a
  clear error

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Scenario_005_ai_generated_scenario

**TestSets:** [Scenario], [AI]

**Preconditions:**
- AI backend configured (`boat ai config set --endpoint ... --model ...`), reachable

**TestSteps:**
1. `boat ai scenario "Create a CAN bus with two ECUs exchanging 0x100 and 0x200"`
2. `boat scenario validate` on the generated output

**Expected:**
- A scenario definition is generated; it passes validation (or the failure is in the
  generated content, clearly reported by validate — not a crash in the tooling)

**Verdict:** NOT_TESTED

**Result:**
