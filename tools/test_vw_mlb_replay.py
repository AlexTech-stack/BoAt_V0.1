#!/usr/bin/env python3
"""Load vw_mlb.json and send all CAN messages cyclically onto vcan0.

Usage:
    # 1. Start the gateway first (separate terminal):
    BOAT_CAN_INTERFACES=vcan0 \\
        ./build/debug/src/gateway/grpc_gateway/boat_gateway

    # 2. Run this script:
    python3 tools/test_vw_mlb_replay.py
"""

import sys
import time

from boat.pdu_db import PduDatabase
from boat.pdu_node import PduNode
from boat.message import Message
from boat.v1 import pdu_pb2

DB_PATH = "tools/vw_mlb.json"
BUS_MAP = {"CAN": "vcan0"}
ADDRESS = "localhost:50051"


def main():
    db = PduDatabase(DB_PATH)
    node = PduNode(address=ADDRESS)

    ok_count = 0
    err_count = 0

    for msg in db.messages():
        bus_type = msg.get("BusType", "")
        if bus_type not in ("CAN", "CANFD"):
            continue

        real_iface = BUS_MAP.get(msg.get("Bus", ""), msg.get("Bus", ""))
        if not real_iface:
            continue

        name = msg.get("MessageName", "?")
        db_id = msg["DbId"]
        cycle_ms = msg.get("CycleTime", 0) or 200
        can_id = msg.get("Identifier", db_id)

        # Configure route with cyclic schedule
        ok = node.configure_route(
            pdu_id=db_id,
            transport=pdu_pb2.PDU_TRANSPORT_CAN,
            iface=real_iface,
            can_id=can_id,
            send_type=pdu_pb2.SEND_TYPE_CYCLIC,
            cycle_ms=cycle_ms,
        )
        if not ok:
            print(f"  FAIL route: {name} (DbId={db_id})")
            err_count += 1
            continue

        # Pack and send initial payload to arm the cyclic schedule
        message = Message(msg)
        payload = message.pack()
        ok = node.send(db_id, payload)
        if not ok:
            print(f"  FAIL send:  {name} (DbId={db_id})")
            err_count += 1
            continue

        ok_count += 1

    print(f"\nConfigured {ok_count} messages, {err_count} errors")
    print("Cyclic sending active on vcan0 — press Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopped")


if __name__ == "__main__":
    main()
