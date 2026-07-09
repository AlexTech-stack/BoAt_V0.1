"""Base class for Python PDU nodes.

A PDU node connects to the BoAt gateway, subscribes to PDU frames, and can
send PDUs or configure routing rules.  Subclass PduNode, override on_pdu(),
then call run() or run_background().

PDUs are AUTOSAR-style protocol data units routed over CAN or Ethernet.
On the Ethernet transport the gateway frames PDUs as:
  [4 bytes PDU ID big-endian] + payload (EtherType defaults to 0x88B5).
"""

from __future__ import annotations

import threading
from typing import Any, List

import grpc

from boat.client import BoAtClient
from boat.v1 import pdu_pb2


class PduNode:
    """Abstract base for Python PDU processing nodes.

    Args:
        address:  Gateway gRPC address (host:port).
        pdu_ids:  PDU IDs to subscribe to.  Empty list = subscribe to all PDUs.
    """

    def __init__(
        self,
        address: str = "localhost:50051",
        pdu_ids: List[int] | None = None,
    ) -> None:
        self._client = BoAtClient(address)
        self._pdu_ids: List[int] = pdu_ids or []
        self._stream: Any = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Override in subclass
    # ------------------------------------------------------------------

    def on_pdu(self, pdu: Any) -> None:
        """Called for every received PduFrame.  Override in subclass."""

    # ------------------------------------------------------------------
    # Route management
    # ------------------------------------------------------------------

    def configure_route(
        self,
        pdu_id: int,
        transport: int,
        iface: str,
        can_id: int = 0,
        ethertype: int = 0x88B5,
        vlan_id: int = 0,
        src_ip: bytes = b"",
        dst_ip: bytes = b"",
        src_port: int = 0,
        dst_port: int = 0,
        ttl: int = 64,
        send_type: int = pdu_pb2.SEND_TYPE_NONE,
        cycle_ms: int = 0,
        fast_ms: int = 0,
        repetitions: int = 0,
    ) -> bool:
        """Configure a PDU routing rule in the gateway.

        Args:
            pdu_id:    32-bit PDU identifier.
            transport: ``pdu_pb2.PDU_TRANSPORT_CAN`` or
                       ``pdu_pb2.PDU_TRANSPORT_ETHERNET``.
            iface:     Interface name (e.g. ``"vcan0"`` or ``"veth0"``).
            can_id:    CAN frame ID override (0 = use pdu_id).
            ethertype: EtherType — only used when dst_ip is empty (sim-only).
            vlan_id:   VLAN ID (0 = untagged).
            src_ip:    Source IP — 4 bytes (IPv4) or 16 bytes (IPv6).
                       When set, PDUs are sent as IP/UDP/IpduM frames.
            dst_ip:    Destination IP — 4 bytes (IPv4) or 16 bytes (IPv6).
            src_port:  UDP source port.
            dst_port:  UDP destination port.
            ttl:       IPv4 TTL / IPv6 Hop Limit (default 64).
            send_type: ``pdu_pb2.SEND_TYPE_CYCLIC``, ``_ON_CHANGE``, ``_MIXED``, or ``_NONE``.
            cycle_ms:  Base cycle in ms for cyclic/mixed modes.
            fast_ms:   Fast period in ms for n-times repetitions.
            repetitions: Number of fast repetitions per change event.

        Returns:
            True if the gateway accepted the route.
        """
        schedule = pdu_pb2.PduSchedule(
            send_type=send_type,
            cycle_ms=cycle_ms,
            fast_ms=fast_ms,
            repetitions=repetitions,
        )
        route = pdu_pb2.PduRoute(
            pdu_id=pdu_id,
            transport=transport,
            iface=iface,
            can_id=can_id,
            ethertype=ethertype,
            vlan_id=vlan_id,
            src_ip=src_ip,
            dst_ip=dst_ip,
            src_port=src_port,
            dst_port=dst_port,
            ttl=ttl,
            schedule=schedule,
        )
        try:
            resp = self._client.pdu.ConfigureRoute(
                pdu_pb2.ConfigureRouteRequest(route=route)
            )
            return bool(resp.ok)
        except grpc.RpcError:
            return False

    def remove_route(self, pdu_id: int) -> bool:
        """Remove a PDU routing rule and its transmission schedule."""
        try:
            resp = self._client.pdu.RemoveRoute(
                pdu_pb2.RemoveRouteRequest(pdu_id=pdu_id)
            )
            return bool(resp.ok)
        except grpc.RpcError:
            return False

    def list_routes(self) -> list:
        """Return all configured routes from the gateway."""
        try:
            resp = self._client.pdu.ListRoutes(pdu_pb2.ListRoutesRequest())
            return list(resp.routes)
        except grpc.RpcError:
            return []

    def configure_container(
        self,
        container_id: int,
        pdu_ids: list,
        iface: str,
        src_ip: bytes,
        dst_ip: bytes,
        src_port: int = 0,
        dst_port: int = 0,
        ttl: int = 64,
        vlan_id: int = 0,
    ) -> bool:
        """Register an IpduM container on the gateway.

        All PDU IDs listed in pdu_ids will be multiplexed into a single
        Ethernet frame whenever any of them is sent via SendPdu.

        Args:
            container_id: Arbitrary non-zero integer ID for this container.
            pdu_ids:      List of 32-bit PDU IDs that share this container.
            iface:        Ethernet interface name (e.g. "enx28107b9f2016").
            src_ip:       Source IP — 4 bytes (IPv4) or 16 bytes (IPv6).
            dst_ip:       Destination IP.
            src_port:     UDP source port.
            dst_port:     UDP destination port.
            ttl:          IPv4 TTL / IPv6 Hop Limit.
            vlan_id:      VLAN ID (0 = untagged).

        Returns:
            True if the gateway accepted the container definition.
        """
        container = pdu_pb2.PduContainerDef(
            container_id=container_id,
            iface=iface,
            src_ip=src_ip,
            dst_ip=dst_ip,
            src_port=src_port,
            dst_port=dst_port,
            ttl=ttl,
            vlan_id=vlan_id,
            pdu_ids=pdu_ids,
        )
        try:
            resp = self._client.pdu.ConfigureContainer(
                pdu_pb2.ConfigureContainerRequest(container=container)
            )
            return bool(resp.ok)
        except grpc.RpcError:
            return False

    # ------------------------------------------------------------------
    # I-PDU Group management
    # ------------------------------------------------------------------

    def configure_group(
        self,
        group_id: int,
        name: str = "",
        pdu_ids: list | None = None,
        enabled: bool = True,
    ) -> bool:
        """Configure an I-PDU group.

        Args:
            group_id: Unique group identifier (non-zero).
            name:     Optional human-readable name.
            pdu_ids:  List of PDU IDs to include in the group.
            enabled:  Whether the group is enabled at creation.

        Returns:
            True if the gateway accepted the group.
        """
        group = pdu_pb2.PduGroup(
            group_id=group_id,
            name=name,
            pdu_ids=pdu_ids or [],
            enabled=enabled,
        )
        try:
            resp = self._client.pdu.ConfigureGroup(
                pdu_pb2.ConfigureGroupRequest(group=group)
            )
            return bool(resp.ok)
        except grpc.RpcError:
            return False

    def enable_group(self, group_id: int) -> bool:
        """Enable an I-PDU group."""
        try:
            resp = self._client.pdu.EnableGroup(
                pdu_pb2.EnableGroupRequest(group_id=group_id)
            )
            return bool(resp.ok)
        except grpc.RpcError:
            return False

    def disable_group(self, group_id: int) -> bool:
        """Disable an I-PDU group."""
        try:
            resp = self._client.pdu.DisableGroup(
                pdu_pb2.DisableGroupRequest(group_id=group_id)
            )
            return bool(resp.ok)
        except grpc.RpcError:
            return False

    def list_groups(self) -> list:
        """Return all configured I-PDU groups."""
        try:
            resp = self._client.pdu.ListGroups(pdu_pb2.ListGroupsRequest())
            return list(resp.groups)
        except grpc.RpcError:
            return []

    # ------------------------------------------------------------------
    # Send helpers
    # ------------------------------------------------------------------

    def send(self, pdu_id: int, payload: bytes) -> bool:
        """Send a PDU via the gateway.

        Args:
            pdu_id:  32-bit PDU identifier (must have a configured route).
            payload: PDU payload bytes.

        Returns:
            True if the gateway accepted the PDU.
        """
        pdu = pdu_pb2.PduFrame(pdu_id=pdu_id, payload=bytes(payload))
        try:
            resp = self._client.pdu.SendPdu(pdu_pb2.SendPduRequest(pdu=pdu))
            return bool(resp.accepted)
        except grpc.RpcError:
            return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Subscribe to PDU frames and block until stop() is called."""
        self._stop_event.clear()
        self._stream = self._client.pdu.SubscribePdus(
            pdu_pb2.SubscribePdusRequest(pdu_ids=self._pdu_ids)
        )
        try:
            for pdu in self._stream:
                if self._stop_event.is_set():
                    break
                self.on_pdu(pdu)
        except grpc.RpcError:
            pass
        finally:
            if self._stream is not None:
                self._stream.cancel()
            self._client.close()

    def run_background(self) -> threading.Thread:
        """Start the node in a daemon thread.  Returns the thread."""
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        """Signal the node to stop after the current PDU."""
        self._stop_event.set()
        if self._stream is not None:
            self._stream.cancel()
