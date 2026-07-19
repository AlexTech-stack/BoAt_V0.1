# BoAt System TestSuite

Top-level index of the system-level TestSuite. Structure, naming, template, and
verdict semantics are defined in [Structure.md](Structure.md). All tests operate
from the **user's perspective** — CLI, gRPC via SDK, web UIs, tools, and the wire
(`candump`/`tcpdump`) — against a running platform, with no internal mocking.

## TestSets with their own file

| TestSet | File | Cases | Covers |
|---|---|---:|---|
| [Gateway] | [Gateway.md](Gateway.md) | 9 | Startup, interface registration, driver selection, plugin loading, tick config, shutdown |
| [CAN] | [CAN.md](CAN.md) | 10 | CAN / CAN FD / extended-ID send+receive, loopback flag, bus isolation, deprecated wrappers, burst |
| [Ethernet] | [Ethernet.md](Ethernet.md) | 6 | Ethernet send/subscribe, veth + raw: physical NICs, capabilities, TCP-send rejection |
| [Simulation] | [Simulation.md](Simulation.md) | 8 | Lifecycle (create/start/pause/step/stop/list/watch), determinism (seed reproducibility) |
| [Scenario] | [Scenario.md](Scenario.md) | 5 | Create/get/list/delete/validate, AI-generated scenarios |
| [Replay] | [Replay.md](Replay.md) | 22 | Direct + server-side replay, all formats, filters, IP/MAC rewriting, pcapng export, from-events |
| [Recording] | [Recording.md](Recording.md) | 9 | CLI + Recorder UI recording in ASC/BLF/PCAP/PCAPNG, signal sidecar, record→replay round trip |
| [PDU] | [PDU.md](PDU.md) | 10 | Routes, cyclic schedules, I-PDU groups, signal packing, E2E, db inspection, plugin delegation |
| [CanTp] | [CanTp.md](CanTp.md) | 5 | ISO 15765-2 sessions, single/multi-frame, flow control, always-on reception |
| [Plugins] | [Plugins.md](Plugins.md) | 7 | Register/list/info/unload, JSON config, bus-type filtering, dual managers, replay delivery |
| [Devices] | [Devices.md](Devices.md) | 14 | Virtual PSU/relay/generator, DeviceService list/set/read/stream, discovery, record→replay, SCPI physical device, environment devices: block, determinism |
| [WebUIs] | [WebUIs.md](WebUIs.md) | 10 | Launcher, Dashboard, Nodes, Commander, Recorder UI, Debug inspector, nav, gateway-down behavior |
| [Tools] | [Tools.md](Tools.md) | 18 | PDU Editor, Trace Analyzer, Trace Editor, Eth Analyzer, dbc2boatjson, offline operation |
| [CLI] | [CLI.md](CLI.md) | 7 | Global flags, JSON mode, help accuracy, failure behavior, test runner, AI assistants |

**Total: 140 TestCases.**

## Cross-cutting TestSets (no own file)

These sets exist only as `[tags]` on TestCases defined in the files above:

| TestSet | Meaning | Index |
|---|---|---|
| [Error] | Error handling & edge cases | [ErrorHandling.md](ErrorHandling.md) |
| [Determinism] | Seed-reproducibility guarantees | TC_Simulation_003/006/007, TC_Devices_011 |
| [PCAPNG] | Mixed CAN+Ethernet capture format | TC_Replay_007/017/018/019/022, TC_Recording_001/008, TC_Tools_009/012/016 |
| [Frame] | Unified FrameService semantics | TC_Ethernet_006, TC_PDU_009 |
| [Hardware] | Requires physical CAN/Ethernet/instrument hardware | TC_Gateway_003, TC_Ethernet_004, TC_Devices_009 |
| [Performance] | Load/throughput behavior | TC_CAN_010 |
| [AI] | LLM-assistant features | TC_Scenario_005, TC_CLI_007 |
| [TraceAnalysis] | Analyzer functionality | TC_Tools_005–009, TC_Tools_014–016 |
| [TraceEditor] | Trace Editor functionality | TC_Tools_010–013 |

## Executing the suite

1. Work through the TestSets in the file order above — Gateway first (everything else
   depends on a healthy gateway), Tools last (they are independent of most failures).
2. Record the verdict (`OK` / `NOK` / `INCONCLUSIVE` / `NOT_TESTED`) and any notes in
   each TestCase's **Result** field; reference issues where filed.
3. If a precondition cannot be met (e.g. no physical hardware for [Hardware] cases),
   set `NOT_TESTED` with the reason in **Result** — do not skip silently.
4. All verdicts ship as `NOT_TESTED` in the repository; a test campaign is a commit
   that updates verdicts + results, so campaign history lives in git.
