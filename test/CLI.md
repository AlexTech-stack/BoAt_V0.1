# TestSet: CLI

System-level tests for CLI-wide behavior: global flags, output modes, help,
dispatch, and failure behavior. Command-specific functionality is tested in the
feature TestSets ([CAN], [Replay], [PDU], ...); a case here focuses on the CLI
surface itself.

Common precondition: CLI installed (`pip install -e ./boat-platform/sdk/python[dev]
&& pip install -e ./boat-platform/cli`).

---

### TC_CLI_001_help_all_subcommands

**TestSets:** [CLI]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. `boat --help`
2. `boat <sub> --help` for every listed subcommand (sim, scenario, replay, frame,
   can-tp, pdu, plugin, db, trace, test, ai)

**Expected:**
- Every subcommand documents its options; help text matches actual accepted flags
  (spot-check: `boat trace start --format` lists asc | blf | pcap | pcapng)

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CLI_002_host_flag

**TestSets:** [CLI]

**Preconditions:**
- Gateway running on a non-default address (e.g. second host or port)

**TestSteps:**
1. `boat --host <addr>:50051 frame list-ifaces`
2. `boat frame list-ifaces` (default localhost) with no local gateway

**Expected:**
- Step 1 talks to the remote gateway; step 2 fails with a connection error —
  proving the flag routes the connection

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CLI_003_json_mode

**TestSets:** [CLI]

**Preconditions:**
- Gateway running; some routes/interfaces configured

**TestSteps:**
1. `boat --json frame list-ifaces | jq .`
2. `boat --json pdu list-routes | jq '.[] | select(.transport == "CAN")'`

**Expected:**
- Output is a valid JSON array (jq parses it); content matches the table output of
  the same command without `--json`

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CLI_004_gateway_unreachable_error

**TestSets:** [CLI], [Error]

**Preconditions:**
- No gateway running

**TestSteps:**
1. `boat sim list`
2. `boat frame send --bus-type can --can-id 0x1 --iface vcan0 --data 00`

**Expected:**
- Clean, non-traceback error messages indicating the gateway is unreachable
  (gRPC UNAVAILABLE), with non-zero exit codes

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CLI_005_invalid_argument_handling

**TestSets:** [CLI], [Error]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. `boat frame send --bus-type can --can-id notahex --iface vcan0 --data 00`
2. `boat frame send --bus-type can --can-id 0x123 --iface vcan0 --data GGHH`
3. `boat trace replay missing_file.blf --buses vcan0`

**Expected:**
- Each rejects with a specific message (invalid CAN id / invalid hex data / file not
  found) and non-zero exit code; nothing is sent

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CLI_006_system_test_runner

**TestSets:** [CLI]

**Preconditions:**
- Gateway running; test environments defined

**TestSteps:**
1. `boat test list-environments`
2. `boat test run --trace-format pcapng` against an environment

**Expected:**
- Environments are listed; the run executes, records traces in the requested format,
  and produces a result report with per-test verdicts

**Verdict:** NOT_TESTED

**Result:**

---

### TC_CLI_007_ai_assistants

**TestSets:** [CLI], [AI]

**Preconditions:**
- AI backend reachable (`boat ai config set --endpoint http://localhost:11434/v1 --model <m>`)

**TestSteps:**
1. `boat ai cli "How do I subscribe to CAN frames?"`
2. `boat ai bus-setup "vcan0 with pdu_router"`

**Expected:**
- Each returns a plausible, relevant answer/config; with the backend down, a clean
  error (no traceback)

**Verdict:** NOT_TESTED

**Result:**
