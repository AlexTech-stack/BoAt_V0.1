#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* CanTp N-SDU connection configuration (ISO 15765-2).
   A connection represents one session between source_addr (this node)
   and target_addr (peer node). The same session handles both TX and RX:
     - We send data (FF/CF) and receive FC on source_addr.
     - We receive data (SF/FF/CF) and send FC on target_addr.
   For single-ID backward compat, set source_addr=target_addr=0 and
   nsdu_id will be used as both. */
typedef struct CanTpConfig {
  uint32_t nsdu_id;            /* Logical session identifier */
  uint32_t source_addr;        /* CAN ID of this node (0 = use nsdu_id) */
  uint32_t target_addr;        /* CAN ID of the peer node (0 = use nsdu_id) */
  uint32_t rx_buffer_size;     /* max reassembly buffer (default 4095) */
  uint8_t  block_size;         /* BS to advertise in sent FC (0 = unlimited) */
  uint8_t  st_min;             /* STmin to advertise in sent FC (0..127 ms) */
  uint8_t  can_dlc;            /* max CAN DLC for this connection (8 or 64) */
  bool     extended_addressing;/* use first data byte as target address */
} CanTpConfig;

/* Send a PDU through CanTp segmentation.
   Returns 1 for a single-frame send, 0 for multi-frame (initiated
   asynchronously via the internal TX thread), or -1 on error. */
int32_t can_tp_send(void* tp_ctx, uint32_t nsdu_id,
                    const uint8_t* data, uint32_t len);

/* Configure an N-SDU connection. Returns 0 on success. */
int32_t can_tp_configure(void* tp_ctx, const CanTpConfig* config);

#ifdef __cplusplus
}
#endif
