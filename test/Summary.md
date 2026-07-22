# Manual Release Full-Test — System Level

| Field | Value |
|---|---|
| **Date** | 20.07.2026 |
| **Tester Name** | Alexander Günther |
| **TestVersion** | v0.1.0 |
| **SW** | v0.1.3-alpha |
| **TestSetup** | Linux testcomputer 6.17.0-29-generic #29~24.04.1-Ubuntu SMP PREEMPT_DYNAMIC x86_64 GNU/Linux |
| | Intel(R) Core(TM) i5-8265U CPU @ 1.60GHz 8 GB DDR4|
| | PEAK System PCAN-USB Pro FD |
| | USB2CANFDV2 1-4.4 WeAct Studio B245208A348 |
| | D-Link Corp. DUB-E100 Fast Ethernet Adapter(rev.B1) [ASIX AX88772] |
| | ASIX Electronics Corp. AX88179 Gigabit Ethernet |

## Test Case Overview

| TestSet | TestCase | Verdict | Comment |
|---|---|---|---|
| CAN | TC_CAN_001_send_frame_cli | OK | |
| CAN | TC_CAN_002_receive_frame_subscribe | NOK | Payload in lowercase|
| CAN | TC_CAN_003_send_canfd_frame | OK | |
| CAN | TC_CAN_004_canfd_length_rounding | OK | |
| CAN | TC_CAN_005_extended_29bit_id | OK | |
| CAN | TC_CAN_006_self_sent_loopback_flag | NOT_TESTED | |
| CAN | TC_CAN_007_multi_bus_isolation | OK | |
| CAN | TC_CAN_008_list_ifaces | OK | |
| CAN | TC_CAN_009_deprecated_wrapper_compat | INCONCLUSIVE | TC has to be removed |
| CAN | TC_CAN_010_high_rate_burst | OK | |
| CanTp | TC_CanTp_001_configure_session | NOK | CAN_TP Plugin shifted to v0.2.x-alpha |
| CanTp | TC_CanTp_002_single_frame_send | NOK | CAN_TP Plugin shifted to v0.2.x-alpha |
| CanTp | TC_CanTp_003_multi_frame_segmentation | NOK | CAN_TP Plugin shifted to v0.2.x-alpha |
| CanTp | TC_CanTp_004_always_on_reception | NOK | CAN_TP Plugin shifted to v0.2.x-alpha |
| CanTp | TC_CanTp_005_send_without_configuration | NOK | CAN_TP Plugin shifted to v0.2.x-alpha |
| CLI | TC_CLI_001_help_all_subcommands | NOK | No help description for sim, scenario, plugin |
| CLI | TC_CLI_002_host_flag | OK | done with gateway at 0.0.0.0:50061 |
| CLI | TC_CLI_003_json_mode | OK | |
| CLI | TC_CLI_004_gateway_unreachable_error | OK | |
| CLI | TC_CLI_005_invalid_argument_handling | OK | |
| CLI | TC_CLI_006_system_test_runner | OK | HTML Looks very oldschool. Style eeds some rework |
| CLI | TC_CLI_007_ai_assistants | NOT_TESTED | |
| Devices | TC_Devices_001_virtual_psu_set_and_read | NOT_TESTED | |
| Devices | TC_Devices_002_virtual_psu_ohms_law_current | NOT_TESTED | |
| Devices | TC_Devices_003_virtual_relay_set_state | NOT_TESTED | |
| Devices | TC_Devices_004_device_manager_discovery | NOT_TESTED | |
| Devices | TC_Devices_005_setcontrol_rejects_unknown_channel | NOT_TESTED | |
| Devices | TC_Devices_006_kl15_gates_restbus | NOT_TESTED | |
| Devices | TC_Devices_007_record_replay_device_curve | NOT_TESTED | |
| Devices | TC_Devices_008_scpi_physical_psu_mock | NOT_TESTED | |
| Devices | TC_Devices_009_scpi_physical_psu_hardware | NOT_TESTED | |
| Devices | TC_Devices_010_stream_state | NOT_TESTED | |
| Devices | TC_Devices_011_determinism_unaffected | NOT_TESTED | |
| Devices | TC_Devices_012_cli_surface | NOT_TESTED | |
| Devices | TC_Devices_013_virtual_generator | NOT_TESTED | |
| Devices | TC_Devices_014_environment_devices_block | NOT_TESTED | |
| Ethernet | TC_Ethernet_001_send_frame_cli | NOT_TESTED | |
| Ethernet | TC_Ethernet_002_subscribe | NOT_TESTED | |
| Ethernet | TC_Ethernet_003_mixed_subscribe_can_and_eth | NOT_TESTED | |
| Ethernet | TC_Ethernet_004_physical_nic_requires_raw_prefix_and_cap | NOT_TESTED | |
| Ethernet | TC_Ethernet_005_self_sent_flag | NOT_TESTED | |
| Ethernet | TC_Ethernet_006_tcp_send_unimplemented | NOT_TESTED | |
| Gateway | TC_Gateway_001_start_with_vcan | NOT_TESTED | |
| Gateway | TC_Gateway_002_start_with_multiple_interfaces | NOT_TESTED | |
| Gateway | TC_Gateway_003_driver_selection_physical_vs_virtual | NOT_TESTED | |
| Gateway | TC_Gateway_004_start_with_ethernet_interface | NOT_TESTED | |
| Gateway | TC_Gateway_005_node_plugin_loading_with_json_config | NOT_TESTED | |
| Gateway | TC_Gateway_006_v7_plugin_rejected | NOT_TESTED | |
| Gateway | TC_Gateway_007_tick_interval_configuration | NOT_TESTED | |
| Gateway | TC_Gateway_008_graceful_shutdown | NOT_TESTED | |
| Gateway | TC_Gateway_009_missing_interface_error | NOT_TESTED | |
| PDU | TC_PDU_001_route_and_send | NOT_TESTED | |
| PDU | TC_PDU_002_cyclic_transmission_schedule | NOT_TESTED | |
| PDU | TC_PDU_003_remove_route_stops_transmission | NOT_TESTED | |
| PDU | TC_PDU_004_ipdu_group_enable_disable | NOT_TESTED | |
| PDU | TC_PDU_005_subscribe | NOT_TESTED | |
| PDU | TC_PDU_006_signal_packing_from_database | NOT_TESTED | |
| PDU | TC_PDU_007_db_inspection_cli | NOT_TESTED | |
| PDU | TC_PDU_008_grpc_delegation_to_plugin | NOT_TESTED | |
| PDU | TC_PDU_009_frame_send_pdu_dispatch | NOT_TESTED | |
| PDU | TC_PDU_010_e2e_crc_protection | NOT_TESTED | |
| Plugins | TC_Plugins_001_register_list_info_unload | NOT_TESTED | |
| Plugins | TC_Plugins_002_json_config_applied | NOT_TESTED | |
| Plugins | TC_Plugins_003_declared_buses_filtering | NOT_TESTED | |
| Plugins | TC_Plugins_004_publish_path_through_frame_sink | NOT_TESTED | |
| Plugins | TC_Plugins_005_dual_manager_independence | NOT_TESTED | |
| Plugins | TC_Plugins_006_replayed_frames_reach_plugins | NOT_TESTED | |
| Plugins | TC_Plugins_007_nonexistent_so_error | NOT_TESTED | |
| Recording | TC_Recording_001_cli_record_pcapng_mixed | NOT_TESTED | |
| Recording | TC_Recording_002_cli_record_asc | NOT_TESTED | |
| Recording | TC_Recording_003_cli_record_blf | NOT_TESTED | |
| Recording | TC_Recording_004_cli_record_legacy_pcap_two_files | NOT_TESTED | |
| Recording | TC_Recording_005_ui_session_lifecycle | NOT_TESTED | |
| Recording | TC_Recording_006_ui_format_validation | NOT_TESTED | |
| Recording | TC_Recording_007_signals_sidecar_jsonl | NOT_TESTED | |
| Recording | TC_Recording_008_record_replay_roundtrip | NOT_TESTED | |
| Recording | TC_Recording_009_asc_blf_without_python_can | NOT_TESTED | |
| Replay | TC_Replay_001_direct_can_replay_blf | NOT_TESTED | |
| Replay | TC_Replay_002_direct_channel_mapping | NOT_TESTED | |
| Replay | TC_Replay_003_direct_channel_and_id_filter | NOT_TESTED | |
| Replay | TC_Replay_004_direct_speed_control | NOT_TESTED | |
| Replay | TC_Replay_005_direct_loop_mode | NOT_TESTED | |
| Replay | TC_Replay_006_direct_rejects_pcap | NOT_TESTED | |
| Replay | TC_Replay_007_direct_pcapng_can_extraction | NOT_TESTED | |
| Replay | TC_Replay_008_import_blf_frame_count | NOT_TESTED | |
| Replay | TC_Replay_009_server_side_can_stream | NOT_TESTED | |
| Replay | TC_Replay_010_pause_resume_seek_stop | NOT_TESTED | |
| Replay | TC_Replay_011_repeat_without_reimport | NOT_TESTED | |
| Replay | TC_Replay_012_eth_pcap_import_global_ip_rewrite | NOT_TESTED | |
| Replay | TC_Replay_013_eth_ip_map_and_filter | NOT_TESTED | |
| Replay | TC_Replay_014_eth_ethertype_protocol_port_filters | NOT_TESTED | |
| Replay | TC_Replay_015_eth_ipv6_and_icmpv6 | NOT_TESTED | |
| Replay | TC_Replay_016_eth_mac_map_playback_time | NOT_TESTED | |
| Replay | TC_Replay_017_import_mixed_pcapng | NOT_TESTED | |
| Replay | TC_Replay_018_export_trace_to_pcapng | NOT_TESTED | |
| Replay | TC_Replay_019_export_skips_tcp_pdu | NOT_TESTED | |
| Replay | TC_Replay_020_from_events | NOT_TESTED | |
| Replay | TC_Replay_021_import_unsupported_format | NOT_TESTED | |
| Replay | TC_Replay_022_import_corrupt_pcapng | NOT_TESTED | |
| Scenario | TC_Scenario_001_create_and_get | NOT_TESTED | |
| Scenario | TC_Scenario_002_validate_valid | NOT_TESTED | |
| Scenario | TC_Scenario_003_validate_invalid | NOT_TESTED | |
| Scenario | TC_Scenario_004_delete | NOT_TESTED | |
| Scenario | TC_Scenario_005_ai_generated_scenario | NOT_TESTED | |
| Simulation | TC_Simulation_001_create_and_start | NOT_TESTED | |
| Simulation | TC_Simulation_002_pause_resume | NOT_TESTED | |
| Simulation | TC_Simulation_003_step_exact_ticks | NOT_TESTED | |
| Simulation | TC_Simulation_004_stop_and_cleanup | NOT_TESTED | |
| Simulation | TC_Simulation_005_list_and_watch | NOT_TESTED | |
| Simulation | TC_Simulation_006_determinism_same_seed_bit_identical | NOT_TESTED | |
| Simulation | TC_Simulation_007_different_seed_differs | NOT_TESTED | |
| Simulation | TC_Simulation_008_invalid_scenario_error | NOT_TESTED | |
| Tools | TC_Tools_001_tools_work_offline | NOT_TESTED | |
| Tools | TC_Tools_002_tool_navigation_separation | NOT_TESTED | |
| Tools | TC_Tools_003_pdu_editor_create_validate_save | NOT_TESTED | |
| Tools | TC_Tools_004_pdu_editor_schema_violation | NOT_TESTED | |
| Tools | TC_Tools_005_trace_analyzer_stage1_messages | NOT_TESTED | |
| Tools | TC_Tools_006_trace_analyzer_counters_and_signals | NOT_TESTED | |
| Tools | TC_Tools_007_trace_analyzer_pdu_db_export | NOT_TESTED | |
| Tools | TC_Tools_008_trace_analyzer_convert_to_trace | NOT_TESTED | |
| Tools | TC_Tools_009_trace_analyzer_pcapng_input | NOT_TESTED | |
| Tools | TC_Tools_010_trace_editor_load_filter_edit_save | NOT_TESTED | |
| Tools | TC_Tools_011_trace_editor_insert_delete_warnings | NOT_TESTED | |
| Tools | TC_Tools_012_trace_editor_export_pcapng | NOT_TESTED | |
| Tools | TC_Tools_013_trace_editor_push_to_gateway | NOT_TESTED | |
| Tools | TC_Tools_014_eth_analyzer_full_analysis | NOT_TESTED | |
| Tools | TC_Tools_015_eth_analyzer_autosar_pdu_stage | NOT_TESTED | |
| Tools | TC_Tools_016_eth_analyzer_csv_export_and_pcapng | NOT_TESTED | |
| Tools | TC_Tools_017_dbc2boatjson_conversion | NOT_TESTED | |
| Tools | TC_Tools_018_analyzer_invalid_input_errors | NOT_TESTED | |
| WebUIs | TC_WebUIs_001_all_uis_reachable | NOT_TESTED | |
| WebUIs | TC_WebUIs_002_shared_navigation | NOT_TESTED | |
| WebUIs | TC_WebUIs_003_launcher_interface_creation | NOT_TESTED | |
| WebUIs | TC_WebUIs_004_launcher_gateway_lifecycle | NOT_TESTED | |
| WebUIs | TC_WebUIs_005_dashboard_live_frames | NOT_TESTED | |
| WebUIs | TC_WebUIs_006_nodes_start_stop | NOT_TESTED | |
| WebUIs | TC_WebUIs_007_commander_raw_send | NOT_TESTED | |
| WebUIs | TC_WebUIs_008_commander_pdu_composed_send | NOT_TESTED | |
| WebUIs | TC_WebUIs_009_debug_grpc_inspector | NOT_TESTED | |
| WebUIs | TC_WebUIs_010_ui_behavior_gateway_down | NOT_TESTED | |
