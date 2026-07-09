import json


def test_build_matches_core_loader_schema(scenario_builder):
    scenario_builder.add_plugin(
        name="can_tp",
        path="plugins/can_tp.so",
        config={"block_size": 4},
    )
    scenario_builder.add_signal("speed", 0.0, name="Vehicle Speed", unit="m/s")
    scenario_builder.add_fault("speed", "SIGNAL_CORRUPTION", 25, magnitude=0.5)

    built = scenario_builder.build()
    assert set(built.keys()) == {
        "id",
        "name",
        "version",
        "duration_ticks",
        "seed",
        "plugins",
        "signals",
        "faults",
    }
    assert built["id"] == "default_scenario"
    assert built["version"] == "1.0.0"
    assert built["duration_ticks"] == 1000
    assert built["seed"] == 0

    plugin = built["plugins"][0]
    assert set(plugin.keys()) == {"so_path", "config_json"}
    assert plugin["so_path"] == "plugins/can_tp.so"
    assert json.loads(plugin["config_json"]) == {"block_size": 4}

    signal = built["signals"][0]
    assert signal == {"id": "speed", "name": "Vehicle Speed", "type": "double", "unit": "m/s"}

    fault = built["faults"][0]
    assert fault == {
        "tick": 25,
        "signal_id": "speed",
        "fault_type": "SIGNAL_CORRUPTION",
        "magnitude": 0.5,
    }


def test_to_json_round_trip(scenario_builder):
    scenario_builder.add_signal("rpm", 900)
    payload = scenario_builder.to_json()
    decoded = json.loads(payload)
    assert decoded == scenario_builder.build()
