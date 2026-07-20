# TestSet: Error

Cross-cutting TestSet for error handling and edge cases. The TestCases below are
defined in their feature TestSet's file (a TestCase may belong to multiple
TestSets — see `Structure.md`); this file is the index of everything tagged
`[Error]`.

The common expectation across all of them: failures are **specific, human-readable,
and non-destructive** — a clear message naming the problem, a non-zero exit code /
proper HTTP status, no stack traces surfaced to the user, no partial state left
behind, and the affected service stays alive.

| TestCase | Defined in | Failure mode covered |
|---|---|---|
| TC_Gateway_006_v7_plugin_rejected | Gateway.md | Outdated plugin ABI rejected at load |
| TC_Gateway_009_missing_interface_error | Gateway.md | Startup with nonexistent interface |
| TC_CAN_004_canfd_length_rounding | CAN.md | Invalid CAN FD payload length |
| TC_Ethernet_004_physical_nic_requires_raw_prefix_and_cap | Ethernet.md | Missing CAP_NET_RAW on physical NIC |
| TC_Ethernet_006_tcp_send_unimplemented | Ethernet.md | Raw frame send on TCP bus type |
| TC_Simulation_008_invalid_scenario_error | Simulation.md | Simulation from unknown scenario |
| TC_Scenario_003_validate_invalid | Scenario.md | Broken scenario definition |
| TC_Replay_006_direct_rejects_pcap | Replay.md | Ethernet format on CAN-only replay path |
| TC_Replay_019_export_skips_tcp_pdu | Replay.md | Non-wire bus types in pcapng export |
| TC_Replay_021_import_unsupported_format | Replay.md | Unknown trace file suffix |
| TC_Replay_022_import_corrupt_pcapng | Replay.md | Truncated/corrupt capture file |
| TC_Recording_006_ui_format_validation | Recording.md | Unknown recording format; CAN-only format with Ethernet |
| TC_Recording_009_asc_blf_without_python_can | Recording.md | Missing optional dependency |
| TC_PDU_008_grpc_delegation_to_plugin | PDU.md | PDU calls without pdu_router loaded |
| TC_CanTp_005_send_without_configuration | CanTp.md | Send on unconfigured CAN-TP session |
| TC_Plugins_007_nonexistent_so_error | Plugins.md | Missing plugin file |
| TC_WebUIs_010_ui_behavior_gateway_down | WebUIs.md | UIs with unreachable gateway |
| TC_Tools_004_pdu_editor_schema_violation | Tools.md | Schema-invalid PDU database |
| TC_Tools_018_analyzer_invalid_input_errors | Tools.md | Bad paths/formats in analyzers |
| TC_CLI_004_gateway_unreachable_error | CLI.md | CLI with no gateway |
| TC_CLI_005_invalid_argument_handling | CLI.md | Malformed CLI arguments |
