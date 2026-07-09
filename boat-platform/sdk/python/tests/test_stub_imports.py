import importlib


def test_generated_stub_modules_import_from_boat_v1_package():
    modules = [
        "boat.v1.common_pb2",
        "boat.v1.fault_pb2",
        "boat.v1.fault_pb2_grpc",
        "boat.v1.metrics_pb2",
        "boat.v1.metrics_pb2_grpc",
        "boat.v1.plugin_pb2",
        "boat.v1.plugin_pb2_grpc",
        "boat.v1.replay_pb2",
        "boat.v1.replay_pb2_grpc",
        "boat.v1.scenario_pb2",
        "boat.v1.scenario_pb2_grpc",
        "boat.v1.signal_pb2",
        "boat.v1.signal_pb2_grpc",
        "boat.v1.simulation_pb2",
        "boat.v1.simulation_pb2_grpc",
        "boat.v1.trace_pb2",
        "boat.v1.trace_pb2_grpc",
    ]
    for module_name in modules:
        importlib.import_module(module_name)


def test_boat_client_loads_all_service_stubs(boat_client):
    assert boat_client.simulation is not None
    assert boat_client.signal is not None
    assert boat_client.scenario is not None
    assert boat_client.replay is not None
    assert boat_client.plugin is not None
    assert boat_client.metrics is not None
    assert boat_client.trace is not None
    assert boat_client.fault is not None


def test_replay_stub_has_new_rpcs():
    from boat.v1 import replay_pb2, replay_pb2_grpc

    assert hasattr(replay_pb2, "PauseReplayRequest")
    assert hasattr(replay_pb2, "ResumeReplayRequest")
    assert hasattr(replay_pb2, "StopReplayRequest")
    assert hasattr(replay_pb2, "ImportTraceDataRequest")
    assert hasattr(replay_pb2, "StartReplayFromEventsRequest")
    assert hasattr(replay_pb2, "ReplaySpeed")
    assert hasattr(replay_pb2, "REPLAY_SPEED_REAL_TIME")
    assert hasattr(replay_pb2, "REPLAY_SPEED_ACCELERATED")
    assert hasattr(replay_pb2, "REPLAY_SPEED_STEP_BY_STEP")

    assert hasattr(replay_pb2_grpc.ReplayServiceServicer, "PauseReplay")
    assert hasattr(replay_pb2_grpc.ReplayServiceServicer, "ResumeReplay")
    assert hasattr(replay_pb2_grpc.ReplayServiceServicer, "StopReplay")
    assert hasattr(replay_pb2_grpc.ReplayServiceServicer, "ImportTraceData")
    assert hasattr(replay_pb2_grpc.ReplayServiceServicer, "StartReplayFromEvents")


def test_start_replay_request_has_speed_fields():
    from boat.v1 import replay_pb2

    req = replay_pb2.StartReplayRequest(
        trace_id="test",
        speed=replay_pb2.REPLAY_SPEED_ACCELERATED,
        speed_multiplier=2.5,
    )
    assert req.trace_id == "test"
    assert req.speed == replay_pb2.REPLAY_SPEED_ACCELERATED
    assert req.speed_multiplier == 2.5
    # mac_map field should exist
    assert hasattr(req, "mac_map")
    req.mac_map["192.168.0.100"] = "02:de:ad:be:ef:01"
    assert req.mac_map["192.168.0.100"] == "02:de:ad:be:ef:01"
