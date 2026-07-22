#!/usr/bin/env python3
"""TC_Ethernet_006: TCP send via FrameService.SendFrame returns UNIMPLEMENTED."""

import argparse
import sys

import grpc

from boat.client import BoAtClient
from boat.v1 import frame_pb2


def test_tcp_send_unimplemented(address: str) -> None:
    client = BoAtClient(address)

    frame = frame_pb2.Frame()
    frame.bus_type = frame_pb2.Frame.TCP
    frame.iface = "lo"
    frame.payload = b"\x01\x02\x03\x04"
    frame.tcp.src_ip = b"\x0a\x00\x00\x01"
    frame.tcp.dst_ip = b"\x0a\x00\x00\x02"
    frame.tcp.src_port = 12345
    frame.tcp.dst_port = 80
    frame.tcp.ip_version = 4
    frame.tcp.conn_id = -1

    req = frame_pb2.SendFrameRequest(frame=frame)

    try:
        client.frame.SendFrame(req)
        print("FAIL: SendFrame for TCP returned OK (expected UNIMPLEMENTED)")
        sys.exit(1)
    except grpc.RpcError as e:
        if e.code() == grpc.StatusCode.UNIMPLEMENTED:
            print(f"PASS: Got expected UNIMPLEMENTED: {e.details()}")
        else:
            print(f"FAIL: Expected UNIMPLEMENTED, got {e.code().name}: {e.details()}")
            sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="TC_Ethernet_006: TCP send via FrameService.SendFrame returns UNIMPLEMENTED")
    parser.add_argument("--address", default="localhost:50051",
                        help="Gateway gRPC address")
    args = parser.parse_args()

    print("TC_Ethernet_006_tcp_send_unimplemented")
    print(f"  Gateway: {args.address}")
    test_tcp_send_unimplemented(args.address)
    print("  Verdict: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
