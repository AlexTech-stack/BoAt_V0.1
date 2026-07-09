#!/usr/bin/env python3
"""
Example: Using the BoAt Python SDK for PDU management.

Shows how to:
  1. Configure routes with transmission schedules
  2. Create and manage I-PDU groups
  3. Send PDUs with signal packing
  4. Subscribe to incoming PDUs

Prerequisites: boat gateway running on localhost:50051
"""

import time
import threading

from boat.client import BoAtClient
from boat.v1 import pdu_pb2
from boat.pdu_node import PduNode


def route_example(client: BoAtClient):
    """Configure routes with and without transmission schedules."""
    print("=== Route Configuration ===")

    # Simple CAN route (manual send only)
    resp = client.pdu.ConfigureRoute(pdu_pb2.ConfigureRouteRequest(
        route=pdu_pb2.PduRoute(
            pdu_id=0x100,
            transport=pdu_pb2.PDU_TRANSPORT_CAN,
            iface="vcan0",
        )
    ))
    print(f"  CAN route 0x100: ok={resp.ok}")

    # CAN route with Cyclic schedule (auto-sends every 100ms)
    resp = client.pdu.ConfigureRoute(pdu_pb2.ConfigureRouteRequest(
        route=pdu_pb2.PduRoute(
            pdu_id=0x200,
            transport=pdu_pb2.PDU_TRANSPORT_CAN,
            iface="vcan0",
            schedule=pdu_pb2.PduSchedule(
                send_type=pdu_pb2.SEND_TYPE_CYCLIC,
                cycle_ms=100,
            ),
        )
    ))
    print(f"  Cyclic route 0x200: ok={resp.ok}")

    # Ethernet route with OnChange + 3 fast reps
    resp = client.pdu.ConfigureRoute(pdu_pb2.ConfigureRouteRequest(
        route=pdu_pb2.PduRoute(
            pdu_id=0x300,
            transport=pdu_pb2.PDU_TRANSPORT_ETHERNET,
            iface="veth0",
            ethertype=0x88B5,
            schedule=pdu_pb2.PduSchedule(
                send_type=pdu_pb2.SEND_TYPE_ON_CHANGE,
                fast_ms=10,
                repetitions=3,
            ),
        )
    ))
    print(f"  OnChange route 0x300: ok={resp.ok}")


def group_example(client: BoAtClient):
    """Create and manage I-PDU groups."""
    print("\n=== I-PDU Groups ===")

    # Create a disabled group
    resp = client.pdu.ConfigureGroup(pdu_pb2.ConfigureGroupRequest(
        group=pdu_pb2.PduGroup(
            group_id=1,
            name="SafetyCritical",
            pdu_ids=[0x100, 0x200],
            enabled=False,
        )
    ))
    print(f"  Created group 'SafetyCritical' (disabled): ok={resp.ok}")

    # Enable at runtime
    resp = client.pdu.EnableGroup(pdu_pb2.EnableGroupRequest(group_id=1))
    print(f"  Enabled group: ok={resp.ok}")

    # List groups
    resp = client.pdu.ListGroups(pdu_pb2.ListGroupsRequest())
    print(f"  Groups ({len(resp.groups)}):")
    for g in resp.groups:
        status = "enabled" if g.enabled else "disabled"
        print(f"    [{g.group_id}] {g.name} — {status}  PDUs: {[hex(p) for p in g.pdu_ids]}")

    # Disable
    resp = client.pdu.DisableGroup(pdu_pb2.DisableGroupRequest(group_id=1))
    print(f"  Disabled group: ok={resp.ok}")


def send_example(client: BoAtClient):
    """Send PDUs and observe group gating."""
    print("\n=== Sending PDUs ===")

    # PDU without group — works
    resp = client.pdu.SendPdu(pdu_pb2.SendPduRequest(
        pdu=pdu_pb2.PduFrame(pdu_id=0x300, payload=b"\xDE\xAD")
    ))
    print(f"  Send 0x300 (no group): accepted={resp.accepted}")

    # PDU in disabled group — fails
    resp = client.pdu.SendPdu(pdu_pb2.SendPduRequest(
        pdu=pdu_pb2.PduFrame(pdu_id=0x100, payload=b"\x01")
    ))
    print(f"  Send 0x100 (disabled group): accepted={resp.accepted}")

    # Enable group, retry
    client.pdu.EnableGroup(pdu_pb2.EnableGroupRequest(group_id=1))
    resp = client.pdu.SendPdu(pdu_pb2.SendPduRequest(
        pdu=pdu_pb2.PduFrame(pdu_id=0x100, payload=b"\x02")
    ))
    print(f"  Send 0x100 (after enable): accepted={resp.accepted}")


def subscribe_example():
    """Subscribe to PDUs in a background thread."""
    print("\n=== PDU Subscription ===")

    class MyNode(PduNode):
        def on_pdu(self, pdu):
            print(f"  Received PDU 0x{pdu.pdu_id:08X}  "
                  f"payload={pdu.payload.hex()}  "
                  f"from {pdu.iface}")

    node = MyNode(pdu_ids=[0x100, 0x200])
    thread = node.run_background()
    time.sleep(1)  # let a few PDUs arrive
    node.stop()
    thread.join(timeout=2)
    print("  Subscription stopped")


if __name__ == "__main__":
    client = BoAtClient("localhost:50051")

    route_example(client)
    group_example(client)
    send_example(client)
    # subscribe_example()  # uncomment to subscribe

    client.close()
    print("\n✓ Python SDK example complete")
