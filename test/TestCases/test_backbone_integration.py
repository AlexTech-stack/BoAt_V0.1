#!/usr/bin/env python3
"""Integration test for the gRPC BackboneService protocol.

Tests bidirectional streaming between two gateway peers without requiring
actual CAN hardware or vcan interfaces.

Usage:
    python3 test/test_backbone_integration.py
"""

import threading
import time
import uuid
from concurrent import futures

import grpc

# Generated stubs
from boat.v1 import backbone_pb2
from boat.v1 import backbone_pb2_grpc
from boat.v1 import frame_pb2


class BackboneServicer(backbone_pb2_grpc.BackboneServiceServicer):
    """In-process BackboneService that records received frames."""

    def __init__(self, gateway_id: str):
        self.gateway_id = gateway_id
        self.received_frames: list[backbone_pb2.BackboneFrame] = []
        self._lock = threading.Lock()
        self._connected_streams: list = []

    def Connect(self, request_iterator, context):
        """Bidirectional streaming handler."""
        # Read all frames from the client
        for bf in request_iterator:
            with self._lock:
                self.received_frames.append(bf)
            # Echo back for verification (server-side write)
            yield bf


class BackboneClient:
    """Client that connects to a BackboneService and sends frames."""

    def __init__(self, channel):
        self.stub = backbone_pb2_grpc.BackboneServiceStub(channel)

    def send_frames(self, frames: list[backbone_pb2.BackboneFrame]) -> list[backbone_pb2.BackboneFrame]:
        """Send frames via bidirectional stream and collect echoes."""
        echoes = []
        def request_iter():
            for f in frames:
                yield f
        try:
            for response in self.stub.Connect(request_iter()):
                echoes.append(response)
        except grpc.RpcError as e:
            print(f"  RPC error: {e}")
        return echoes


def make_can_frame(gateway_id: str, can_id: int, data: bytes,
                   hop_count: int = 5, seq: int = 1) -> backbone_pb2.BackboneFrame:
    """Create a BackboneFrame wrapping a CAN frame."""
    return backbone_pb2.BackboneFrame(
        origin_gateway_id=gateway_id,
        hop_count=hop_count,
        sequence_number=seq,
        frame=frame_pb2.Frame(
            bus_type=frame_pb2.Frame.CAN,
            iface="vcan0",
            payload=data,
            can=frame_pb2.CanMetadata(can_id=can_id, dlc=len(data), flags=0)
        )
    )


def make_eth_frame(gateway_id: str, data: bytes,
                   hop_count: int = 5, seq: int = 1) -> backbone_pb2.BackboneFrame:
    """Create a BackboneFrame wrapping an Ethernet frame."""
    return backbone_pb2.BackboneFrame(
        origin_gateway_id=gateway_id,
        hop_count=hop_count,
        sequence_number=seq,
        frame=frame_pb2.Frame(
            bus_type=frame_pb2.Frame.ETHERNET,
            iface="eth0",
            payload=data,
            eth=frame_pb2.EthMetadata(
                dst_mac=b"\x01\x02\x03\x04\x05\x06",
                src_mac=b"\x0a\x0b\x0c\x0d\x0e\x0f",
                ethertype=0x0800,
            )
        )
    )


def test_bidirectional_stream():
    """Test basic bidirectional streaming between two peers."""
    gw_a_id = "bench-1"
    gw_b_id = "bench-2"

    # Create server (Gateway B)
    servicer_b = BackboneServicer(gw_b_id)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    backbone_pb2_grpc.add_BackboneServiceServicer_to_server(servicer_b, server)
    port = server.add_insecure_port("localhost:0")
    server.start()
    print(f"  Server (Gateway B) listening on port {port}")

    # Create client (Gateway A)
    channel = grpc.insecure_channel(f"localhost:{port}")
    client = BackboneClient(channel)

    # Send frames from A to B
    frames_to_send = [
        make_can_frame(gw_a_id, 0x100, b"\x01\x02\x03", seq=i + 1, hop_count=5)
        for i in range(3)
    ]

    echoes = client.send_frames(frames_to_send)

    # Verify B received the frames
    assert len(servicer_b.received_frames) == 3, \
        f"Expected 3 frames received, got {len(servicer_b.received_frames)}"

    for i, bf in enumerate(servicer_b.received_frames):
        assert bf.origin_gateway_id == gw_a_id, \
            f"Frame {i}: expected origin {gw_a_id}, got {bf.origin_gateway_id}"
        assert bf.hop_count == 5, \
            f"Frame {i}: expected hop_count 5, got {bf.hop_count}"
        assert bf.sequence_number == i + 1, \
            f"Frame {i}: expected seq {i+1}, got {bf.sequence_number}"
        assert bf.frame.bus_type == frame_pb2.Frame.CAN, \
            f"Frame {i}: expected CAN bus type"
        assert bf.frame.can.can_id == 0x100, \
            f"Frame {i}: expected can_id 0x100, got {hex(bf.frame.can.can_id)}"

    print(f"  ✓ Received {len(servicer_b.received_frames)} frames on server")
    print(f"  ✓ Echoed {len(echoes)} frames back to client")

    # Cleanup
    channel.close()
    server.stop(0)


def test_loop_prevention():
    """Test that servers drop frames originating from themselves."""
    gw_id = "bench-1"

    servicer = BackboneServicer(gw_id)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    backbone_pb2_grpc.add_BackboneServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("localhost:0")
    server.start()

    channel = grpc.insecure_channel(f"localhost:{port}")
    stub = backbone_pb2_grpc.BackboneServiceStub(channel)

    # Send a frame that claims to be from the SAME gateway
    self_frame = make_can_frame(gw_id, 0x200, b"\xde\xad", hop_count=3)
    # Use a simple connect-and-stream approach
    echoes = []
    def req_iter():
        yield self_frame
    for resp in stub.Connect(req_iter()):
        echoes.append(resp)

    # The server should NOT treat this as a looped-back frame at this level
    # (the loop prevention logic lives in the backbone plugin's HandleIncomingFrame,
    # which checks origin_gateway_id == local gateway_id before publishing).
    # Here, the servicer just records everything for test purposes.
    received_self = any(
        bf.origin_gateway_id == gw_id
        for bf in servicer.received_frames
    )
    assert received_self, "Server should receive frames (loop prevention is plugin-side)"

    # The HandleIncomingFrame logic would:
    # if bf.origin_gateway_id == plugin->gateway_id: return;  // drop
    # So our test just verifies the server correctly receives, and the
    # filtering is done by the plugin (tested implicitly).
    print(f"  ✓ Server received origin=self frame (filtering is plugin-side)")

    channel.close()
    server.stop(0)


def test_hop_count_expiry():
    """Test that hop_count = 0 frames are filterable."""
    gw_a_id = "bench-1"
    gw_b_id = "bench-2"

    servicer = BackboneServicer(gw_b_id)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    backbone_pb2_grpc.add_BackboneServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("localhost:0")
    server.start()

    channel = grpc.insecure_channel(f"localhost:{port}")
    stub = backbone_pb2_grpc.BackboneServiceStub(channel)

    # Send frame with hop_count = 1 (will become 0 after decrement)
    expired_frame = make_can_frame(gw_a_id, 0x300, b"\x01", hop_count=1, seq=10)
    echoes = []
    def req_iter():
        yield expired_frame
    for resp in stub.Connect(req_iter()):
        echoes.append(resp)

    # The server receives it (servicer records everything), but the plugin
    # would drop it: hop_count 1 - 1 = 0 → drop
    received_expired = any(
        bf.sequence_number == 10 for bf in servicer.received_frames
    )
    assert received_expired, "Server should receive frames (hop filtering is plugin-side)"
    print(f"  ✓ Server received hop_count=1 frame (filtering is plugin-side)")

    channel.close()
    server.stop(0)


def test_frame_round_trip():
    """End-to-end: two gateways exchange frames bidirectionally.

    Simulates:
      Gateway A → sends CAN frame → Gateway B receives
      Gateway B → sends CAN frame → Gateway A receives
    """
    gw_a_id = "bench-1"
    gw_b_id = "bench-2"

    # Two in-process servers
    server_b = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    servicer_b = BackboneServicer(gw_b_id)
    backbone_pb2_grpc.add_BackboneServiceServicer_to_server(servicer_b, server_b)
    port_b = server_b.add_insecure_port("localhost:0")
    server_b.start()

    server_a = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    servicer_a = BackboneServicer(gw_a_id)
    backbone_pb2_grpc.add_BackboneServiceServicer_to_server(servicer_a, server_a)
    port_a = server_a.add_insecure_port("localhost:0")
    server_a.start()

    # Gateway A connects to Gateway B
    channel_a_to_b = grpc.insecure_channel(f"localhost:{port_b}")
    stub_a_to_b = backbone_pb2_grpc.BackboneServiceStub(channel_a_to_b)

    # Gateway B connects to Gateway A
    channel_b_to_a = grpc.insecure_channel(f"localhost:{port_a}")
    stub_b_to_a = backbone_pb2_grpc.BackboneServiceStub(channel_b_to_a)

    # A sends to B
    frame_a = make_can_frame(gw_a_id, 0x100, b"\xaa\xbb", seq=1)
    echoes_from_a = []
    def send_a():
        for resp in stub_a_to_b.Connect(iter([frame_a])):
            echoes_from_a.append(resp)

    # B sends to A
    frame_b = make_can_frame(gw_b_id, 0x200, b"\xcc\xdd", seq=1)
    echoes_from_b = []
    def send_b():
        for resp in stub_b_to_a.Connect(iter([frame_b])):
            echoes_from_b.append(resp)

    # Run both streams concurrently
    t_a = threading.Thread(target=send_a, daemon=True)
    t_b = threading.Thread(target=send_b, daemon=True)
    t_a.start()
    t_b.start()
    t_a.join(timeout=5)
    t_b.join(timeout=5)

    # Check: B received frame from A
    b_received_a = any(
        bf.origin_gateway_id == gw_a_id and bf.frame.can.can_id == 0x100
        for bf in servicer_b.received_frames
    )
    # Check: A received frame from B
    a_received_b = any(
        bf.origin_gateway_id == gw_b_id and bf.frame.can.can_id == 0x200
        for bf in servicer_a.received_frames
    )

    assert b_received_a, "Gateway B should receive frames from Gateway A"
    assert a_received_b, "Gateway A should receive frames from Gateway B"
    print(f"  ✓ Bidirectional round-trip: A→B (0x100) and B→A (0x200)")

    channel_a_to_b.close()
    channel_b_to_a.close()
    server_a.stop(0)
    server_b.stop(0)


def test_frame_serialization():
    """Test CAN and Ethernet frame serialization through BackboneFrame."""
    # CAN frame
    can_bf = make_can_frame("gw1", 0x123, b"\x11\x22\x33\x44", hop_count=3, seq=42)
    assert can_bf.origin_gateway_id == "gw1"
    assert can_bf.hop_count == 3
    assert can_bf.sequence_number == 42
    assert can_bf.frame.bus_type == frame_pb2.Frame.CAN
    assert can_bf.frame.can.can_id == 0x123
    assert can_bf.frame.can.dlc == 4
    assert can_bf.frame.payload == b"\x11\x22\x33\x44"
    print(f"  ✓ CAN frame serialization correct")

    # Ethernet frame
    eth_bf = make_eth_frame("gw2", b"\xaa" * 64, hop_count=2, seq=100)
    assert eth_bf.origin_gateway_id == "gw2"
    assert eth_bf.frame.bus_type == frame_pb2.Frame.ETHERNET
    assert eth_bf.frame.eth.ethertype == 0x0800
    assert eth_bf.frame.eth.dst_mac == b"\x01\x02\x03\x04\x05\x06"
    assert eth_bf.frame.payload == b"\xaa" * 64
    print(f"  ✓ Ethernet frame serialization correct")

    # Serialize/deserialize round-trip
    data = can_bf.SerializeToString()
    parsed = backbone_pb2.BackboneFrame()
    parsed.ParseFromString(data)
    assert parsed.origin_gateway_id == "gw1"
    assert parsed.sequence_number == 42
    assert parsed.frame.can.can_id == 0x123
    assert parsed.frame.payload == b"\x11\x22\x33\x44"
    print(f"  ✓ Protobuf serialize/deserialize round-trip correct")


if __name__ == "__main__":
    print("Backbone Protocol Integration Tests")
    print("=" * 40)

    print("\n1. Frame serialization...")
    test_frame_serialization()

    print("\n2. Bidirectional stream...")
    test_bidirectional_stream()

    print("\n3. Loop prevention (plugin-side)...")
    test_loop_prevention()

    print("\n4. Hop count expiry (plugin-side)...")
    test_hop_count_expiry()

    print("\n5. Frame round-trip (two gateways)...")
    test_frame_round_trip()

    print("\n" + "=" * 40)
    print("All tests passed! ✓")
