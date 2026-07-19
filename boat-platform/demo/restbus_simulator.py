#!/usr/bin/env python3
"""Restbus simulator — loads a PDU database and starts cyclic transmission.

Configures every CAN/CANFD message as a cyclic PDU route in the gateway and
seeds its initial payload from the signal InitValues.  The gateway's tick-
driven PDU transmission engine then handles the cyclic sending autonomously.

Usage::

    # Basic — uses bus names from the DB directly
    python3 demo/restbus_simulator.py config/pdu_db_vcan.json

    # With bus remapping
    python3 demo/restbus_simulator.py config/pdu_db_vcan.json \\
        --bus-map vcan0=can0 --bus-map vcan1=can1

    # Custom gateway address
    python3 demo/restbus_simulator.py config/pdu_db_vcan.json --gateway 10.0.0.5:50051
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict

from boat.bus_node import BusNode
from boat.pdu_db import PduDatabase
from boat.pdu_node import PduNode
from boat.message import Message
from boat.v1 import pdu_pb2


class RestbusSimulator:
    """Configure and seed cyclic CAN transmission from a PDU database.

    Args:
        db_path:  Path to a ``pdu_db.json`` file.
        bus_map:  Mapping from symbolic bus names to real interface names.
                  If a bus name is not in the map it is passed through as-is.
        address:  Gateway gRPC address (``host:port``).
    """

    def __init__(
        self,
        db_path: str | Path,
        bus_map: Dict[str, str] | None = None,
        address: str = "localhost:50051",
    ) -> None:
        self._db = PduDatabase(db_path)
        self._bus_map: Dict[str, str] = bus_map or {}
        self._pdu = PduNode(address=address)

    # ------------------------------------------------------------------
    # Mapping from DB send-type strings to proto enums
    # ------------------------------------------------------------------

    _SEND_TYPE_MAP = {
        "Cyclic":    pdu_pb2.SEND_TYPE_CYCLIC,
        "OnChange":  pdu_pb2.SEND_TYPE_ON_CHANGE,
        "Mixed":     pdu_pb2.SEND_TYPE_MIXED,
    }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _real_iface(self, bus_name: str) -> str:
        return self._bus_map.get(bus_name, bus_name)

    def _route_summary(self, msg: dict, iface: str, ok: bool) -> None:
        pdu_id = msg["DbId"]
        can_id = msg.get("Identifier", pdu_id)
        name = msg["MessageName"]
        cycle_ms = msg.get("CycleTime", 0)
        sigs = msg.get("signals", [])
        sig_desc = f"{sigs[0]['SignalName']} {sigs[0]['Length']}b" if sigs else "no signals"
        ft = "29bit" if msg.get("FrameType") else "11bit"
        status = "OK" if ok else "FAIL"
        print(f"  [{iface:10s}] 0x{can_id:07X}  {name:25s}  {sig_desc:25s}  "
              f"{cycle_ms:5}ms  {ft:5s}  {status}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> int:
        """Configure all CAN/CANFD routes and seed initial payloads.

        Returns the number of successfully configured + seeded messages.
        """
        count = 0
        for msg in self._db.messages():
            bus_type = msg.get("BusType", "")
            if bus_type not in ("CAN", "CANFD"):
                continue

            iface = self._real_iface(msg.get("Bus", ""))
            pdu_id = msg["DbId"]
            can_id = msg.get("Identifier", pdu_id)
            send_type_str = msg.get("SendType", "Cyclic")
            st = self._SEND_TYPE_MAP.get(send_type_str, pdu_pb2.SEND_TYPE_NONE)

            # Pack initial payload from signal InitValues
            payload = Message(msg).pack()

            # Configure the route with a transmission schedule
            ok = self._pdu.configure_route(
                pdu_id=pdu_id,
                transport=pdu_pb2.PDU_TRANSPORT_CAN,
                iface=iface,
                can_id=can_id,
                send_type=st,
                cycle_ms=msg.get("CycleTime", 0) if st != pdu_pb2.SEND_TYPE_NONE else 0,
                fast_ms=msg.get("CycleTimeFast", 0),
                repetitions=msg.get("NrOfRepetitions", 0),
            )
            if not ok:
                self._route_summary(msg, iface, False)
                continue

            # Seed the initial payload so the gateway has data to send cyclically
            if payload:
                ok = self._pdu.send(pdu_id=pdu_id, payload=payload)
                if not ok:
                    self._route_summary(msg, iface, False)
                    continue

            self._route_summary(msg, iface, True)
            count += 1

        return count

    def stop(self) -> None:
        """Remove all CAN/CANFD routes (stops the gateway from sending them)."""
        for msg in self._db.messages():
            if msg.get("BusType") not in ("CAN", "CANFD"):
                continue
            self._pdu.remove_route(pdu_id=msg["DbId"])


class _Kl15Gate(BusNode):
    """Gate cyclic transmission on an ignition relay's contact state.

    Subscribes to ``relay.<id>.state`` (published by virtual_relay). When the
    contact closes (ignition on) the restbus routes are configured + seeded;
    when it opens (ignition off) they are removed and the bus goes quiet — just
    like terminal 15 gating a real vehicle's cyclic traffic.
    """

    def __init__(self, sim: "RestbusSimulator", relay_id: str, address: str) -> None:
        super().__init__(address=address, node_id="restbus.kl15gate")
        self._sim = sim
        self._state_sig = f"relay.{relay_id}.state"
        self._running = False

    @property
    def names(self) -> list[str]:
        return [self._state_sig]

    def on_signal(self, signal) -> None:  # noqa: ANN001
        if signal.name != self._state_sig:
            return
        closed = signal.number_value != 0.0
        if closed and not self._running:
            count = self._sim.start()
            self._running = True
            print(f"\n[KL15] ignition ON — {count} route(s) transmitting")
        elif not closed and self._running:
            self._sim.stop()
            self._running = False
            print("\n[KL15] ignition OFF — restbus quiet")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Restbus simulator for the BoAt platform."
    )
    parser.add_argument(
        "db_path",
        type=Path,
        help="Path to a PDU database JSON file (e.g. config/pdu_db_vcan.json)",
    )
    parser.add_argument(
        "--bus-map",
        action="append",
        default=[],
        help="Map logical bus name to physical interface, e.g. Powertrain_CAN=vcan0. "
             "Repeatable for multiple mappings.",
    )
    parser.add_argument(
        "--gateway",
        default="localhost:50051",
        help="Gateway gRPC address (default: localhost:50051)",
    )
    parser.add_argument(
        "--kl15-relay",
        metavar="ID",
        default=None,
        help="Gate transmission on relay.<ID>.state (e.g. --kl15-relay kl15). "
             "Requires a virtual_relay for that ID. Ignition off = restbus quiet.",
    )

    args = parser.parse_args()

    if not args.db_path.exists():
        print(f"Database file not found: {args.db_path}")
        sys.exit(1)

    bus_map: Dict[str, str] = {}
    for entry in args.bus_map:
        if "=" not in entry:
            print(f"Invalid --bus-map entry: {entry!r}  (expected format logical=physical)")
            sys.exit(1)
        logical, physical = entry.split("=", 1)
        bus_map[logical] = physical

    sim = RestbusSimulator(
        db_path=args.db_path,
        bus_map=bus_map,
        address=args.gateway,
    )

    print(f"Restbus simulator — {args.db_path.name}")
    if bus_map:
        for logical, physical in bus_map.items():
            print(f"  Bus map: {logical} → {physical}")
    else:
        print("  Bus map: (none — using DB bus names directly)")
    print(f"  Gateway: {args.gateway}")
    print()
    print(f"  {'Bus':10s}  {'CAN ID':9s}  {'Message':27s}  {'Signal':27s}  "
          f"{'Cycle':6s}  {'Type':6s}  Status")
    print(f"  {'-'*10}  {'-'*9}  {'-'*27}  {'-'*27}  "
          f"{'-'*6}  {'-'*6}  ------")

    # KL15-gated mode: wait for an ignition relay to turn transmission on/off.
    if args.kl15_relay:
        gate = _Kl15Gate(sim, args.kl15_relay, args.gateway)
        print(f"\n  KL15 gate: relay.{args.kl15_relay}.state controls transmission")
        print("  Waiting for ignition…  (Ctrl-C to stop)")
        try:
            gate.run(names=gate.names)
        except KeyboardInterrupt:
            pass
        finally:
            gate.stop()
            sim.stop()  # ensure routes are removed on exit
        return

    count = sim.start()

    print()
    if count == 0:
        print("No routes configured.  Is the gateway running?")
        sys.exit(1)
    print(f"{count} route(s) configured and seeded.  "
          f"Gateway handles cyclic transmission.")


if __name__ == "__main__":
    main()
