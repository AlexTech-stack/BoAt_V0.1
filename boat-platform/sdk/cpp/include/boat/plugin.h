#pragma once

#include <stddef.h>
#include <stdint.h>

#include "boat/frame.h"

#define BOAT_PLUGIN_ABI_VERSION 8

#ifdef __cplusplus
extern "C" {
#endif

/* Callback a plugin calls to publish a numeric signal value. */
typedef void (*BoatPublishFn)(void* publisher_ctx, const char* signal_id,
                              uint64_t tick, double value);

/* CAN FD flag constants — kept for self-sent detection in can_tp. */
#define BOAT_CAN_FLAG_SELF_SENT 0x08

/* Ethernet self-sent flag — set by EthernetBusRegistry when a frame was
   sourced locally (loopback prevention). */
#define BOAT_ETH_FLAG_SELF_SENT 0x01

/* PDU frame type used by the PDU-publish callback.
   This is the mechanism for CanTp to deliver reassembled I-PDUs. */
typedef struct BoatPduFrame {
  uint32_t    pdu_id;
  uint8_t*    payload;
  size_t      payload_len;
  const char* iface;  /* interface the frame arrived on */
} BoatPduFrame;

/* Callback a plugin calls to publish a fully-formed PDU into the frame bus.
   The host dispatches it via DispatchFrame for routing by the PduRouter plugin. */
typedef void (*BoatPduPublishFn)(void* publisher_ctx, const BoatPduFrame* frame);

/* Callback a plugin calls to publish a named value on the always-on signal bus.
   The bus is independent of any simulation lifecycle. */
typedef void (*BoatBusPublishFn)(void* publisher_ctx, const char* name, double value);

/* ── v8 Plugin VTable ────────────────────────────────────────────────── */

typedef struct BoatPluginVTable {
  /* Required — parse config JSON, return 0 on success. */
  int  (*initialize)(void* ctx, const char* config_json);

  /* Required — called on every tick by the host scheduler. */
  void (*on_tick)(void* ctx, uint64_t tick);

  /* Required — cleanup. Host guarantees no concurrent callbacks after return. */
  void (*shutdown)(void* ctx);

  /* Optional — set to NULL if the plugin does not publish signals. */
  void (*set_publisher)(void* ctx, BoatPublishFn fn, void* publisher_ctx);

  /* Optional — set to NULL if the plugin does not publish bus signals. */
  void (*set_bus_publisher)(void* ctx, BoatBusPublishFn fn, void* publisher_ctx);

  /* Optional — set to NULL if the plugin does not publish PDU frames.
     The host dispatches these as BOAT_BUS_PDU frames for routing. */
  void (*set_pdu_publisher)(void* ctx, BoatPduPublishFn fn, void* publisher_ctx);

  /* Host → Plugin: deliver a unified BoatFrame. */
  BoatFrameReceiveFn on_frame;

  /* Plugin → Host: publish a unified BoatFrame onto the bus. */
  void (*set_frame_publisher)(void* ctx, BoatFramePublishFn fn,
                              void* publisher_ctx);

  /* Optional: plugin declares which bus types it handles.
     Returns a JSON array of bus type names, e.g. "[\"can\",\"eth\"]".
     "" or NULL means "accept all". */
  BoatDeclaredBusesFn declared_buses;
} BoatPluginVTable;

typedef struct BoatPlugin {
  BoatPluginVTable* vtable;
  void* ctx;
} BoatPlugin;

typedef BoatPlugin* (*boat_plugin_create_fn)();
typedef void (*boat_plugin_destroy_fn)(BoatPlugin* plugin);
typedef uint32_t (*boat_plugin_abi_version_fn)();

BoatPlugin* boat_plugin_create();
void boat_plugin_destroy(BoatPlugin* plugin);
uint32_t boat_plugin_abi_version();

/* ── Optional: named C++ service export ─────────────────────────────────
   A plugin MAY export both of these symbols to expose a named C++ service
   pointer that host-side gRPC service implementations look up via
   PluginManager::FindService(name) -- e.g. the pdu_router plugin exposing
   an IPduRouter* for PduServiceImpl to delegate to. Omit both symbols
   entirely if the plugin has no such service; this is independent of
   BoatPluginVTable, so it does not require an ABI version bump for
   plugins that don't use it. boat_plugin_service_ptr is called once,
   right after initialize() succeeds, with the same ctx passed to every
   other vtable function -- the returned pointer must stay valid until
   shutdown() returns. */
typedef const char* (*boat_plugin_service_name_fn)();
typedef void* (*boat_plugin_service_ptr_fn)(void* ctx);

#ifdef __cplusplus
}
#endif
