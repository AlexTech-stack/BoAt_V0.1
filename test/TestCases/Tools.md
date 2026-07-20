# TestSet: Tools

System-level tests for the standalone tools (started by `start_tools.sh`):
PDU Editor (8087), Trace Analyzer (8088), Trace Editor (8089), Eth Analyzer (8090),
and the `dbc2boatjson.py` converter. These must work **without** a running gateway
except where a test explicitly says otherwise.

Common precondition: tools running (`./start_tools.sh`); sample traces available
(a CAN `.blf`, an Ethernet `.pcap` with DoIP/SOME/IP traffic, a mixed `.pcapng`).

---

### TC_Tools_001_tools_work_offline

**TestSets:** [Tools]

**Preconditions:**
- NO gateway running

**TestSteps:**
1. Open ports 8087-8090; load a file in each tool and run its main analysis/edit action

**Expected:**
- All four tools serve their pages and perform file-based work normally — no
  gateway-connection errors except on explicitly gateway-bound actions
  (e.g. "Push to Gateway")

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_002_tool_navigation_separation

**TestSets:** [Tools], [WebUIs]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Inspect the nav bar on each tool page (8087-8090) and the Trace Editor howto page

**Expected:**
- The nav lists exactly the four standalone tools (Trace Editor, Trace Analyzer,
  Eth Analyzer, PDU Editor) in that order, current page highlighted — no gateway
  UIs mixed in

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_003_pdu_editor_create_validate_save

**TestSets:** [Tools], [PDU]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. In the PDU Editor (8087), create a new database, add a message (CAN ID, length)
   with two signals (one Intel, one Motorola, with factor/offset)
2. Validate; save to JSON
3. Reopen the saved file in the editor

**Expected:**
- Validation passes against `pdu_db.schema.json`; the file round-trips losslessly;
  `boat db show` can render it

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_004_pdu_editor_schema_violation

**TestSets:** [Tools], [Error]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Create a message with an out-of-schema value (e.g. signal length 0 or overlapping
   bit ranges if checked); validate

**Expected:**
- Validation reports the specific violation; saving is possible only where the tool
  permits, and the error message names the offending field

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_005_trace_analyzer_stage1_messages

**TestSets:** [Tools], [TraceAnalysis]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. In the Trace Analyzer (8088), enter the path to the sample `.blf`, run the
   message-identification stage

**Expected:**
- Per-CAN-ID table with frame counts, DLC, cycle time, Cyclic/Spontaneous
  classification; totals match the file; duplicate-channel (gateway-relayed) IDs are
  collapsed to their original channel with the duplicates noted

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_006_trace_analyzer_counters_and_signals

**TestSets:** [Tools], [TraceAnalysis]

**Preconditions:**
- Stage 1 completed on a trace known to contain AUTOSAR rolling counters

**TestSteps:**
1. Run the counter-detection stage; then the application-signal discovery stage

**Expected:**
- Known counters are detected with correct bit positions and wrap behavior;
  signal discovery yields clustered candidate signals (byte order, signedness,
  confidence) on the remaining bits; each stage reports its runtime

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_007_trace_analyzer_pdu_db_export

**TestSets:** [Tools], [TraceAnalysis], [PDU]

**Preconditions:**
- Analysis stages completed

**TestSteps:**
1. Export the PDU database JSON from the analyzer
2. Open the export in the PDU Editor and validate

**Expected:**
- A schema-valid database skeleton containing the discovered messages/signals —
  directly usable as a starting point in the PDU Editor

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_008_trace_analyzer_convert_to_trace

**TestSets:** [Tools], [TraceAnalysis]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Use the analyzer's Convert action on the sample `.blf`
2. Open the produced `.trace` in the Trace Editor

**Expected:**
- Conversion writes `<name>.trace` into the Trace Editor's scan location; the editor
  loads it with the same frame count as the source

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_009_trace_analyzer_pcapng_input

**TestSets:** [Tools], [TraceAnalysis], [PCAPNG]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Analyze a mixed `.pcapng` in the Trace Analyzer (8088)

**Expected:**
- CAN records are analyzed normally; Ethernet records are skipped with an explicit
  "skipped N non-CAN frame(s)" note — not an error, not silently missing

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_010_trace_editor_load_filter_edit_save

**TestSets:** [Tools], [TraceEditor]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. In the Trace Editor (8089), load a `.trace`; filter by CAN ID; edit one frame's
   payload; observe the DLC field
2. Save; reload the file

**Expected:**
- Filtering narrows the table client-side; DLC auto-syncs to the edited payload
  length (mismatch warns in red); the saved file reloads with the edit persisted and
  all other frames byte-identical

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_011_trace_editor_insert_delete_warnings

**TestSets:** [Tools], [TraceEditor]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Insert a new frame after an existing one (clone), give it an out-of-order
   timestamp; delete a selection of frames; save

**Expected:**
- Insert/delete update the table and indices correctly; save succeeds but surfaces a
  non-blocking warning about non-monotonic timestamps

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_012_trace_editor_export_pcapng

**TestSets:** [Tools], [TraceEditor], [PCAPNG]

**Preconditions:**
- A loaded trace containing CAN and Ethernet frames (and, if available, a PDU frame)

**TestSteps:**
1. Click **Export to PCAPNG**, give a filename
2. Open the export in Wireshark; re-import via `boat replay import`

**Expected:**
- Export reports CAN/Ethernet counts and skipped TCP/PDU count; the file is
  Wireshark-valid and re-imports with matching frame counts

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_013_trace_editor_push_to_gateway

**TestSets:** [Tools], [TraceEditor], [Replay]

**Preconditions:**
- Gateway running (this action is the documented exception to offline operation)

**TestSteps:**
1. Edit a loaded trace (change one payload), **Push to Gateway** with trace id `edited`
2. `boat replay stream --trace edited --buses vcan0` with `candump vcan0` running

**Expected:**
- Push succeeds via ImportTraceData; the replay reflects the edit (modified payload
  on the bus)

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_014_eth_analyzer_full_analysis

**TestSets:** [Tools], [TraceAnalysis], [Ethernet]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. In the Eth Analyzer (8090), analyze the sample `.pcap`

**Expected:**
- EtherType/VLAN histograms, MAC/IP node inventory with role hints, UDP flows with
  cyclic/event classification, reconstructed TCP sessions with client/server roles,
  and a DoIP / SOME/IP service catalog — counts consistent with the capture

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_015_eth_analyzer_autosar_pdu_stage

**TestSets:** [Tools], [TraceAnalysis]

**Preconditions:**
- A capture containing AUTOSAR PDU-multiplexed UDP flows

**TestSteps:**
1. Run stage 1; then the AUTOSAR PDU stage

**Expected:**
- PDU-multiplex flows are flagged in stage 1; stage 2 produces a per-Header-ID
  catalog (lengths, samples, sending behavior) with routed duplicates collapsed to
  the original flow

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_016_eth_analyzer_csv_export_and_pcapng

**TestSets:** [Tools], [TraceAnalysis], [PCAPNG]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Export a results table to CSV; open it
2. Analyze a mixed `.pcapng` with the Eth Analyzer

**Expected:**
- CSV matches the on-screen table (headers + rows)
- The pcapng's Ethernet records are analyzed; CAN records are skipped silently
  (mirror image of TC_Tools_009)

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_017_dbc2boatjson_conversion

**TestSets:** [Tools], [PDU], [CLI]

**Preconditions:**
- A representative `.dbc` with messages, signals (Intel + Motorola), multiplexing,
  value tables, comments, and GenMsgCycleTime attributes

**TestSteps:**
1. `python3 tools/dbc2boatjson.py boat-platform/config/pdu_db.schema.json input.dbc out.json --validate`
2. Open `out.json` in the PDU Editor; `boat db show --db out.json`

**Expected:**
- Conversion succeeds and passes schema validation; messages/signals/mux/enums/
  comments/cycle times all mapped per the documented table; editor and CLI can read it

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Tools_018_analyzer_invalid_input_errors

**TestSets:** [Tools], [Error]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. In the Trace Analyzer, submit a path that does not exist, then a file with an
   unsupported suffix (e.g. `.txt`), then (CAN analyzer) a `.pcap`
2. In the Eth Analyzer, submit a `.blf`

**Expected:**
- Each case returns a specific, human-readable error (404 file not found / 400
  unsupported format listing supported suffixes / CAN-vs-Ethernet mismatch hint) —
  no stack traces in the UI, service stays up

**Verdict:** NOT_TESTED

**Result:**
