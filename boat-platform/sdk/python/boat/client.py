from __future__ import annotations

from typing import Any

import grpc


class BoAtClient:
    def __init__(self, address: str = "localhost:50051") -> None:
        self.address = address
        self.channel = grpc.insecure_channel(address)
        self._stubs_loaded = False

    def _load_stubs(self) -> None:
        if self._stubs_loaded:
            return
        from boat.v1 import bus_pb2_grpc
        from boat.v1 import can_pb2_grpc
        from boat.v1 import debug_pb2_grpc
        from boat.v1 import ethernet_pb2_grpc
        from boat.v1 import fault_pb2_grpc
        from boat.v1 import metrics_pb2_grpc
        from boat.v1 import plugin_pb2_grpc
        from boat.v1 import replay_pb2_grpc
        from boat.v1 import scenario_pb2_grpc
        from boat.v1 import signal_pb2_grpc
        from boat.v1 import simulation_pb2_grpc
        from boat.v1 import pdu_pb2_grpc
        from boat.v1 import trace_pb2_grpc
        from boat.v1 import frame_pb2_grpc
        from boat.v1 import device_pb2_grpc

        self._bus = bus_pb2_grpc.BusServiceStub(self.channel)
        self._ethernet = ethernet_pb2_grpc.EthernetServiceStub(self.channel)
        self._simulation = simulation_pb2_grpc.SimulationServiceStub(self.channel)
        self._signal = signal_pb2_grpc.SignalServiceStub(self.channel)
        self._scenario = scenario_pb2_grpc.ScenarioServiceStub(self.channel)
        self._replay = replay_pb2_grpc.ReplayServiceStub(self.channel)
        self._plugin = plugin_pb2_grpc.PluginServiceStub(self.channel)
        self._metrics = metrics_pb2_grpc.MetricsServiceStub(self.channel)
        self._trace = trace_pb2_grpc.TraceServiceStub(self.channel)
        self._fault = fault_pb2_grpc.FaultServiceStub(self.channel)
        self._can = can_pb2_grpc.CanServiceStub(self.channel)
        self._pdu = pdu_pb2_grpc.PduServiceStub(self.channel)
        self._debug = debug_pb2_grpc.DebugServiceStub(self.channel)
        self._frame = frame_pb2_grpc.FrameServiceStub(self.channel)
        self._device = device_pb2_grpc.DeviceServiceStub(self.channel)
        self._stubs_loaded = True

    @property
    def bus(self) -> Any:
        self._load_stubs()
        return self._bus

    @property
    def simulation(self) -> Any:
        self._load_stubs()
        return self._simulation

    @property
    def signal(self) -> Any:
        self._load_stubs()
        return self._signal

    @property
    def scenario(self) -> Any:
        self._load_stubs()
        return self._scenario

    @property
    def replay(self) -> Any:
        self._load_stubs()
        return self._replay

    @property
    def plugin(self) -> Any:
        self._load_stubs()
        return self._plugin

    @property
    def metrics(self) -> Any:
        self._load_stubs()
        return self._metrics

    @property
    def trace(self) -> Any:
        self._load_stubs()
        return self._trace

    @property
    def fault(self) -> Any:
        self._load_stubs()
        return self._fault

    @property
    def ethernet(self) -> Any:
        self._load_stubs()
        return self._ethernet

    @property
    def can(self) -> Any:
        self._load_stubs()
        return self._can

    @property
    def pdu(self) -> Any:
        self._load_stubs()
        return self._pdu

    @property
    def debug(self) -> Any:
        self._load_stubs()
        return self._debug

    @property
    def frame(self) -> Any:
        self._load_stubs()
        return self._frame

    @property
    def device(self) -> Any:
        self._load_stubs()
        return self._device

    def close(self) -> None:
        self.channel.close()
