"""Hardware Ethernet loopback test via BoAt gateway.

Sends a raw Ethernet frame from enx28107b9f2016 (50.50.0.1) through the
BoAt gRPC gateway and verifies it is received on enx28107b9f2017 (50.50.0.2).

Usage:  python3 hw_eth_test.py
Requires: gateway running with BOAT_ETH_INTERFACES="raw:enx28107b9f2016,raw:enx28107b9f2017"
"""

import sys
import os
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sdk', 'python'))

import grpc
from boat.v1 import ethernet_pb2, ethernet_pb2_grpc

GATEWAY = "localhost:50051"
TX_IFACE = "enx28107b9f2016"
RX_IFACE = "enx28107b9f2017"
TX_MAC = bytes.fromhex("28107b9f2016")
RX_MAC = bytes.fromhex("28107b9f2017")
ETHERTYPE = 0x88B5  # BoAt sim EtherType (arbitrary raw payload)
PAYLOAD = b"BoAt-HW-Test-" + b"\xDE\xAD\xBE\xEF" * 4


def list_interfaces(stub):
    resp = stub.ListInterfaces(ethernet_pb2.ListEthernetInterfacesRequest())
    return list(resp.ifaces)


def receive_frame(stub, timeout=5.0):
    req = ethernet_pb2.SubscribeEthernetFramesRequest(iface=RX_IFACE)
    received = threading.Event()
    frame_out = {}

    def _rx():
        try:
            for frame in stub.SubscribeFrames(req, timeout=timeout):
                # Ethernet pads payloads < 46 B with zeros; check prefix.
                if frame.ethertype == ETHERTYPE and frame.payload[:len(PAYLOAD)] == PAYLOAD:
                    frame_out['frame'] = frame
                    received.set()
                    return
        except grpc.RpcError:
            pass

    t = threading.Thread(target=_rx, daemon=True)
    t.start()
    return received, frame_out, t


def main():
    channel = grpc.insecure_channel(GATEWAY)
    stub = ethernet_pb2_grpc.EthernetServiceStub(channel)

    print("=== BoAt Hardware Ethernet Test ===")

    # List registered interfaces
    ifaces = list_interfaces(stub)
    print(f"Gateway interfaces: {ifaces}")
    if TX_IFACE not in ifaces or RX_IFACE not in ifaces:
        print(f"ERROR: Expected {TX_IFACE} and {RX_IFACE} in gateway. Got: {ifaces}")
        sys.exit(1)

    # Start receiver first
    received, frame_out, rx_thread = receive_frame(stub)
    time.sleep(0.2)  # let subscriber register

    # Send frame via gateway
    frame = ethernet_pb2.EthernetFrame(
        iface=TX_IFACE,
        src_mac=TX_MAC,
        dst_mac=RX_MAC,
        ethertype=ETHERTYPE,
        payload=PAYLOAD,
    )
    req = ethernet_pb2.SendEthernetFrameRequest(frame=frame)
    resp = stub.SendFrame(req)
    print(f"SendFrame accepted: {resp.accepted}")
    if not resp.accepted:
        print("ERROR: Gateway rejected frame.")
        sys.exit(1)

    # Wait for reception
    if received.wait(timeout=5.0):
        f = frame_out['frame']
        print(f"PASS: Frame received on {f.iface}")
        print(f"  src_mac: {f.src_mac.hex(':')}")
        print(f"  dst_mac: {f.dst_mac.hex(':')}")
        print(f"  ethertype: 0x{f.ethertype:04X}")
        print(f"  payload ({len(f.payload)}B): {f.payload.hex()}")
    else:
        print("FAIL: No matching frame received within 5 seconds.")
        sys.exit(1)

    channel.close()
    print("=== Test PASSED ===")


if __name__ == "__main__":
    main()
