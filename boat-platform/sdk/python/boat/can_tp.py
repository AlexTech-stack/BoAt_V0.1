"""Python interface for CAN Transport Protocol (ISO 15765-2).

This is a high-level wrapper around the CanTp plugin's standalone C ABI.
The plugin must be loaded via BOAT_NODE_PLUGINS or registered via the
PluginService gRPC API.

This module provides:

- CanTpHandle(so_path): load the CanTp shared library and get a handle
- configure(handle, nsdu_id, ...): configure an N-SDU connection
- send(handle, nsdu_id, data): send a large PDU through CanTp segmentation

For the CLI, use::
    boat can-tp send --nsdu-id 0x7E0 --data 0123456789ABCDEF...
"""

from __future__ import annotations

import ctypes
import os
from typing import Optional


class CanTpConfig(ctypes.Structure):
    """Mirrors the C struct CanTpConfig from boat/can_tp.h.
    A connection represents one session between source_addr (this node)
    and target_addr (peer node)."""
    _fields_ = [
        ("nsdu_id", ctypes.c_uint32),
        ("source_addr", ctypes.c_uint32),
        ("target_addr", ctypes.c_uint32),
        ("rx_buffer_size", ctypes.c_uint32),
        ("block_size", ctypes.c_uint8),
        ("st_min", ctypes.c_uint8),
        ("can_dlc", ctypes.c_uint8),
        ("extended_addressing", ctypes.c_bool),
    ]


class CanTpHandle:
    """Handle to a loaded CanTp plugin .so.

    The handle loads the shared library and resolves the can_tp_send and
    can_tp_configure symbols.
    """

    def __init__(self, so_path: str) -> None:
        if not os.path.exists(so_path):
            raise FileNotFoundError(f"CanTp plugin not found: {so_path}")
        self._lib = ctypes.CDLL(so_path)

        # can_tp_configure(void* tp_ctx, CanTpConfig* config) -> int32_t
        self._lib.can_tp_configure.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(CanTpConfig),
        ]
        self._lib.can_tp_configure.restype = ctypes.c_int32

        # can_tp_send(void* tp_ctx, uint32_t nsdu_id, uint8_t* data, uint32_t len) -> int32_t
        self._lib.can_tp_send.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint8),
            ctypes.c_uint32,
        ]
        self._lib.can_tp_send.restype = ctypes.c_int32

        # We need the plugin instance pointer. The CanTp plugin is loaded
        # by the gateway's node_manager, not by Python. For Python-level
        # access, callers provide a (host, port) and we use the CanTp gRPC
        # service instead, or load a second instance locally.
        self._ctx = None

    def configure(self, nsdu_id: int, **kwargs) -> bool:
        """Configure an N-SDU connection.

        Args:
            nsdu_id: N-SDU identifier.
            **kwargs: Override CanTpConfig fields (source_addr, target_addr,
                      rx_buffer_size, block_size, st_min, can_dlc,
                      extended_addressing).

        Returns:
            True if configured successfully.
        """
        source_addr = kwargs.get("source_addr", 0)
        target_addr = kwargs.get("target_addr", 0)
        if source_addr == 0 and target_addr == 0:
            source_addr = nsdu_id
            target_addr = nsdu_id
        config = CanTpConfig(
            nsdu_id=nsdu_id,
            source_addr=source_addr,
            target_addr=target_addr,
            rx_buffer_size=kwargs.get("rx_buffer_size", 4095),
            block_size=kwargs.get("block_size", 0),
            st_min=kwargs.get("st_min", 0),
            can_dlc=kwargs.get("can_dlc", 8),
            extended_addressing=kwargs.get("extended_addressing", False),
        )
        result = self._lib.can_tp_configure(None, ctypes.byref(config))
        return result == 0

    def send(self, nsdu_id: int, data: bytes) -> bool:
        """Send a large PDU through CanTp segmentation.

        Args:
            nsdu_id: N-SDU identifier.
            data: PDU payload bytes.

        Returns:
            True if the send was initiated.
        """
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        result = self._lib.can_tp_send(None, nsdu_id, buf, len(data))
        return result > 0
