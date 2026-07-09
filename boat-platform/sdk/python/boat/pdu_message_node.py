"""High-level PDU message node.

Combines PduDatabase + PduNode to send messages by name or DbId with
all signal values defaulted to InitValue.

Example::

    from boat.pdu_message_node import PduMessageNode

    node = PduMessageNode(
        db_path="config/pdu_db_example.json",
        bus_map={"Motor_CAN": "vcan0", "Body_CANFD": "vcan1"},
    )
    node.send_message("Motor_1", "Motor_CAN")
    node.send_message_by_id(15)
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Dict, Optional

from boat.pdu_db import PduDatabase
from boat.pdu_node import PduNode
from boat.v1 import pdu_pb2


class PduMessageNode:
    """Send CAN/CANFD/Ethernet messages from a PDU DB with default signal values.

    Args:
        db_path:  Path to a ``pdu_db.json`` file.
        bus_map:  Mapping from symbolic bus names (as they appear in the DB)
                  to real interface names known to the gateway, e.g.::

                      {"Motor_CAN": "vcan0", "Body_CANFD": "vcan1"}

                  If a bus name is not in the map it is passed through as-is.
        address:  Gateway gRPC address (host:port).
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
        self._auto_configure()

    # ------------------------------------------------------------------
    # Route auto-configuration
    # ------------------------------------------------------------------

    def _real_iface(self, msg: dict) -> str:
        return self._bus_map.get(msg.get("Bus", ""), msg.get("Bus", ""))

    def _auto_configure(self) -> None:
        for msg in self._db.messages():
            bus_type = msg.get("BusType", "")
            iface = self._real_iface(msg)

            if bus_type in ("CAN", "CANFD"):
                self._pdu.configure_route(
                    pdu_id=msg["DbId"],
                    transport=pdu_pb2.PDU_TRANSPORT_CAN,
                    iface=iface,
                    can_id=msg.get("Identifier", 0),
                )

            elif bus_type == "ETH":
                db_ids = msg.get("IpduMEntries", [])
                pdu_ids = []
                for db_id in db_ids:
                    pdu_msg = self._db.by_id(db_id)
                    if pdu_msg:
                        pdu_ids.append(pdu_msg.get("PduId", db_id))
                    else:
                        pdu_ids.append(db_id)
                dst_ip_str = msg.get("DstIP", "")
                if pdu_ids and dst_ip_str:
                    self._pdu.configure_container(
                        container_id=msg["DbId"],
                        pdu_ids=pdu_ids,
                        iface=iface,
                        src_ip=socket.inet_aton(msg.get("SrcIP", "0.0.0.0")),
                        dst_ip=socket.inet_aton(dst_ip_str),
                        src_port=msg.get("SrcPort", 0),
                        dst_port=msg.get("DstPort", 0),
                        ttl=msg.get("TTL", 64),
                        vlan_id=msg.get("VlanId", 0),
                    )

            elif bus_type == "ETH_PDU":
                pdu_id = msg.get("PduId", 0)
                if pdu_id:
                    self._pdu.configure_route(
                        pdu_id=pdu_id,
                        transport=pdu_pb2.PDU_TRANSPORT_ETHERNET,
                        iface=iface,
                    )

    # ------------------------------------------------------------------
    # Signal packing
    # ------------------------------------------------------------------

    @staticmethod
    def _pack_message(msg: dict) -> bytes:
        """Build the on-wire payload from the message's signal defaults.

        Respects multiplexing: if a multiplexor signal (IsMuxor=true) is
        present, only static signals (no MuxValue) and signals whose
        MuxValue matches the muxor's InitValue are packed.

        CAN/CANFD frames pack signals into *Length* bytes.
        ETH_PDU frames use *Length* as the PDU payload size.
        ETH containers have no direct signals (they route via IpduM).
        """
        frame_len = msg.get("Length", 0)
        if frame_len == 0:
            return b""

        # Determine active mux group from the muxor's InitValue.
        active_mux = None
        for sig in msg.get("signals", []):
            if sig.get("IsMuxor", False):
                active_mux = int(sig.get("InitValue", 0))
                break

        buf = bytearray(frame_len)

        for sig in msg.get("signals", []):
            mv = sig.get("MuxValue")
            if mv is not None and active_mux is not None and mv != active_mux:
                continue

            start = sig["StartPos"]
            length = sig["Length"]
            byte_order = sig["ByteOrder"]  # 0=Intel, 1=Motorola
            raw = int(sig.get("InitValue", 0))
            max_val = (1 << length) - 1
            raw = min(raw, max_val)

            if byte_order == 0:  # Intel (little endian)
                for bit_in_sig in range(length):
                    if raw & (1 << bit_in_sig):
                        pos = start + bit_in_sig
                        byte_idx = pos // 8
                        bit_off = pos % 8
                        if byte_idx < len(buf):
                            buf[byte_idx] |= 1 << bit_off
            else:  # Motorola (big endian)
                for bit_in_sig in range(length):
                    if raw & (1 << (length - 1 - bit_in_sig)):
                        pos = start - bit_in_sig
                        byte_idx = pos // 8
                        bit_off = pos % 8
                        if byte_idx < len(buf):
                            buf[byte_idx] |= 1 << bit_off

        return bytes(buf)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _pdu_id_for_message(self, msg: dict) -> int:
        """Return the PDU ID to use when sending *msg*."""
        bus_type = msg.get("BusType", "")
        if bus_type == "ETH_PDU":
            return msg.get("PduId", 0)
        return msg["DbId"]

    def send_message(self, name: str, bus: str) -> bool:
        """Send a message by its *name* and *bus* with all default signal values.

        Args:
            name: Message name from the PDU DB (e.g. ``"Motor_1"``).
            bus:  Symbolic bus name (e.g. ``"Motor_CAN"``).

        Returns:
            True if the gateway accepted the PDU.
        """
        msg = self._db.by_name_and_bus(name, bus)
        if msg is None:
            raise LookupError(f"Message {name!r} not found on bus {bus!r}")
        return self._send(msg)

    def send_message_by_id(self, db_id: int) -> bool:
        """Send a message by its *DbId* with all default signal values.

        Args:
            db_id: Database ID (``DbId`` field in the PDU DB).

        Returns:
            True if the gateway accepted the PDU.
        """
        msg = self._db.by_id(db_id)
        if msg is None:
            raise LookupError(f"Message with DbId={db_id} not found")
        return self._send(msg)

    def _send(self, msg: dict) -> bool:
        payload = self._pack_message(msg)
        pdu_id = self._pdu_id_for_message(msg)
        if pdu_id == 0:
            raise ValueError(f"Message {msg.get('MessageName')!r} has no routable PDU ID")
        return self._pdu.send(pdu_id, payload)
