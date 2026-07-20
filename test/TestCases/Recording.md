# TestSet: Recording

System-level tests for trace recording via the CLI (`boat trace start/stop/status`)
and the Recorder web UI (port 8083), across all supported output formats.

Common precondition: gateway running with `BOAT_CAN_INTERFACES=vcan0` and
`BOAT_ETH_INTERFACES=veth0`; Recorder UI running (`python3 ui/recorder.py`);
a traffic generator available (e.g. `cansend` in a loop or a running simulation).

---

### TC_Recording_001_cli_record_pcapng_mixed

**TestSets:** [Recording], [PCAPNG], [CLI]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. `boat trace start --format pcapng --buses vcan0 --eth veth0`
2. Generate CAN traffic (`cansend vcan0 123#AABB` × 20) and Ethernet traffic
3. `boat trace stop`; locate the produced `.pcapng`

**Expected:**
- One `.pcapng` file containing both CAN and Ethernet frames on one timeline
  (two interface blocks); frame counts match the generated traffic
- File opens in Wireshark with correctly decoded CAN IDs

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Recording_002_cli_record_asc

**TestSets:** [Recording], [CAN], [CLI]

**Preconditions:**
- `python-can` installed

**TestSteps:**
1. `boat trace start --format asc --buses vcan0`; generate 20 CAN frames; stop

**Expected:**
- A valid `.asc` file readable by python-can/Vector tooling with all 20 frames,
  correct IDs, channels, and timestamps

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Recording_003_cli_record_blf

**TestSets:** [Recording], [CAN], [CLI]

**Preconditions:**
- `python-can` installed

**TestSteps:**
1. `boat trace start --format blf --buses vcan0`; generate frames incl. one CAN FD
   frame; stop

**Expected:**
- A valid `.blf` with all frames; the FD frame retains FDF/BRS flags

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Recording_004_cli_record_legacy_pcap_two_files

**TestSets:** [Recording], [CLI]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. `boat trace start --format pcap --buses vcan0 --eth veth0`; generate CAN and
   Ethernet traffic; stop

**Expected:**
- Two files are produced (`*_can.pcap` with DLT_CAN_SOCKETCAN, `*_eth.pcap` with
  DLT_EN10MB) — classic pcap cannot mix link types; both open in Wireshark

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Recording_005_ui_session_lifecycle

**TestSets:** [Recording], [WebUIs]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Open `http://localhost:8083`, select format PCAPNG (default), tick `vcan0` and
   `veth0`, click **Start Recording**
2. Generate traffic; observe the Active Sessions counters
3. Stop the session; download the file from Session History

**Expected:**
- Session appears under Active Sessions with live CAN/ETH frame counters increasing
- After stop it moves to history with file name + size; the downloaded file contains
  the recorded frames

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Recording_006_ui_format_validation

**TestSets:** [Recording], [WebUIs], [Error]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. POST `/api/sessions` with `{"format":"xyz"}` (e.g. via curl)
2. In the UI, select ASC format and observe the Ethernet interface checkboxes

**Expected:**
- Step 1: HTTP 400 naming the allowed formats (asc, blf, pcap, pcapng)
- Step 2: Ethernet checkboxes are disabled for CAN-only formats (ASC/BLF), enabled
  for PCAP/PCAPNG

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Recording_007_signals_sidecar_jsonl

**TestSets:** [Recording]

**Preconditions:**
- A simulation or node publishing BoAt bus signals

**TestSteps:**
1. Start a recording with "Include BoAt bus signals" enabled; let signals flow; stop

**Expected:**
- A `_bus.jsonl` sidecar exists next to the trace, one JSON object per signal with
  timestamp, name, type, value

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Recording_008_record_replay_roundtrip

**TestSets:** [Recording], [Replay], [PCAPNG]

**Preconditions:**
- Common preconditions of this TestSet (see top of file)

**TestSteps:**
1. Record a known, scripted traffic pattern (20 CAN frames with incrementing IDs) to
   PCAPNG
2. `boat replay import <recording>.pcapng --trace-id rt`
3. `boat replay stream --trace rt --buses vcan1` while `candump vcan1` runs

**Expected:**
- The replayed sequence on `vcan1` matches the originally generated sequence
  frame-for-frame — full record → import → replay round trip

**Verdict:** NOT_TESTED

**Result:**

---

### TC_Recording_009_asc_blf_without_python_can

**TestSets:** [Recording], [Error]

**Preconditions:**
- An environment where `python-can` is NOT installed

**TestSteps:**
1. Attempt to start an ASC recording (UI or API)
2. Attempt a PCAPNG recording in the same environment

**Expected:**
- ASC/BLF: clean error stating python-can is required (HTTP 500 with message, no crash)
- PCAPNG: works — it has no third-party dependency

**Verdict:** NOT_TESTED

**Result:**
